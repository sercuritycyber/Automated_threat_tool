// TAXII 2.1 read-only server + STIX 2.1 ingest, backed by D1 (stix_objects).
//
// A free, open, standards-based CTI feed: Splunk (TAXII input) and Microsoft
// Sentinel (native "Threat Intelligence - TAXII" connector) can both poll this
// with their built-in connectors — no custom code consumer-side.
//
// Endpoints (all GET, anonymous, read-only):
//   /taxii2/                                       discovery
//   /taxii2/api/                                   api-root info
//   /taxii2/api/collections/                       collection list
//   /taxii2/api/collections/indicators/            the collection
//   /taxii2/api/collections/indicators/objects/    STIX objects (envelope)
// Plus POST /internal/stix (Bearer INGEST_SECRET) — the VM pushes indicators here.

const TAXII_CT = "application/taxii+json;version=2.1";
const STIX_CT = "application/stix+json;version=2.1";
const COLLECTION_ID = "indicators";
const BASE = "/taxii2/api/collections/" + COLLECTION_ID;

function tjson(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": TAXII_CT, "Access-Control-Allow-Origin": "*", ...extra },
  });
}

function collectionObj() {
  return {
    id: COLLECTION_ID,
    title: "SecurityCyber honeypot & CTI indicators",
    description: "Atomic indicators (IPv4, URLs, domains, file hashes) from SecurityCyber's "
      + "own honeypots and verified CTI. Free, no paywall. Inquest, not judgment.",
    can_read: true,
    can_write: false,
    media_types: [STIX_CT],
  };
}

// ---- ingest (VM -> worker) -------------------------------------------------
export async function ingest(D, body) {
  const objs = Array.isArray(body && body.objects) ? body.objects : [];
  const now = new Date().toISOString();
  const stmts = [];
  for (const o of objs) {
    if (!o || o.type !== "indicator" || !/^indicator--[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(o.id || "")) continue;
    if (o.spec_version !== "2.1" || o.pattern_type !== "stix" || typeof o.pattern !== "string" || !o.pattern.trim()) continue;
    if (![o.created,o.modified,o.valid_from].every(v=>typeof v==="string"&&v.endsWith("Z")&&Number.isFinite(Date.parse(v)))) continue;
    if (Date.parse(o.modified)<Date.parse(o.created)) continue;
    stmts.push(D.prepare(
      `INSERT INTO stix_objects (id, stype, created, modified, added, pattern, obj)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
       ON CONFLICT(id) DO UPDATE SET modified = ?4, pattern = ?6, obj = ?7, added = ?5
       WHERE julianday(excluded.modified) > julianday(stix_objects.modified)`,
    ).bind(o.id, o.type, o.created || now, o.modified || now, now, o.pattern || "", JSON.stringify(o)));
  }
  if (stmts.length) await D.batch(stmts);
  return stmts.length;
}

// ---- read (consumer polls) -------------------------------------------------
export async function handle(D, cfg, url, path) {
  const root = `${cfg.site_url}/taxii2/api/`;

  if (path === "/taxii2" || path === "/taxii2/") {
    return tjson({
      title: "SecurityCyber TAXII 2.1",
      description: "Free, open threat-intelligence feed for defenders. No paywall, no vendor bias.",
      default: root,
      api_roots: [root],
    });
  }
  if (path === "/taxii2/api" || path === "/taxii2/api/") {
    return tjson({ title: "SecurityCyber CTI", versions: [TAXII_CT], max_content_length: 1048576 });
  }
  if (path === "/taxii2/api/collections" || path === "/taxii2/api/collections/") {
    return tjson({ collections: [collectionObj()] });
  }
  if (path === BASE || path === BASE + "/") {
    return tjson(collectionObj());
  }
  const suffix = path.slice(BASE.length).replace(/\/$/, "");
  const objectMatch = suffix.match(/^\/objects\/(indicator--[a-f0-9-]+)(\/versions)?$/);
  if (path.startsWith(BASE + "/") && (["/objects", "/manifest"].includes(suffix) || objectMatch)) {
    const limitRaw = url.searchParams.get("limit") || "500";
    const limit = Number(limitRaw);
    if (!Number.isInteger(limit) || limit < 1 || limit > 2000) return tjson({title:"Invalid limit"},400);
    const clauses=[], binds=[];
    const add=(sql,...values)=>{clauses.push(sql);binds.push(...values);};
    const after=url.searchParams.get("added_after");
    if(after) {
      if(!Number.isFinite(Date.parse(after)))return tjson({title:"Invalid added_after"},400);
      add("added > ?",new Date(after).toISOString());
    }
    const cursor=url.searchParams.get("next");
    if(cursor) {
      try {
        const pair=JSON.parse(atob(cursor));
        if(!Array.isArray(pair)||pair.length!==2||!Number.isFinite(Date.parse(pair[0]))||typeof pair[1]!=="string")throw Error();
        add("(added > ? OR (added = ? AND id > ?))",pair[0],pair[0],pair[1]);
      } catch {return tjson({title:"Invalid pagination cursor"},400);}
    }
    if(objectMatch)add("id = ?",objectMatch[1]);
    for(const [param,col] of [["match[id]","id"],["match[type]","stype"]]) {
      const value=url.searchParams.get(param);
      if(value) {const values=value.split(",");if(values.length>100)return tjson({title:"Too many filters"},400);add(col+" IN ("+values.map(()=>"?").join(",")+")",...values);}
    }
    const spec=url.searchParams.get("match[spec_version]");
    if(spec&&!spec.split(",").includes("2.1"))add("1 = 0");
    const version=url.searchParams.get("match[version]");
    if(version&&!['all','first','last'].includes(version)) {
      if(!Number.isFinite(Date.parse(version)))return tjson({title:"Invalid version"},400);
      add("julianday(modified) = julianday(?)",new Date(version).toISOString());
    }
    let rows;
    try {
      const sql="SELECT id, obj, added, modified FROM stix_objects"+(clauses.length?" WHERE "+clauses.join(" AND "):"")+" ORDER BY added ASC, id ASC LIMIT ?";
      rows=(await D.prepare(sql).bind(...binds,limit+1).all()).results||[];
    } catch {return tjson({title:"Feed temporarily unavailable",http_status:"503"},503);}
    const more=rows.length>limit;rows=rows.slice(0,limit);
    if(objectMatch&&!rows.length)return tjson({title:"Object not found"},404);
    const envelope={more};
    if(objectMatch?.[2])envelope.versions=rows.map(r=>r.modified);
    else if(suffix==="/manifest")envelope.objects=rows.map(r=>({id:r.id,date_added:r.added,version:r.modified,media_type:STIX_CT}));
    else envelope.objects=rows.map(r=>JSON.parse(r.obj));
    if(more)envelope.next=btoa(JSON.stringify([rows.at(-1).added,rows.at(-1).id]));
    const headers={"Cache-Control":"no-cache"};
    if(rows.length){headers["X-TAXII-Date-Added-First"]=rows[0].added;headers["X-TAXII-Date-Added-Last"]=rows.at(-1).added;}
    return tjson(envelope,200,headers);
  }
  return tjson({ title: "Not Found", http_status: "404" }, 404);
}
