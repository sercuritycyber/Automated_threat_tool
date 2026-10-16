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
    if (!o || o.type !== "indicator" || !o.id) continue;
    stmts.push(D.prepare(
      `INSERT INTO stix_objects (id, stype, created, modified, added, pattern, obj)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
       ON CONFLICT(id) DO UPDATE SET modified = ?4, pattern = ?6, obj = ?7`,
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
  if (path === BASE + "/objects" || path === BASE + "/objects/") {
    const addedAfter = url.searchParams.get("added_after") || "";
    const limit = Math.min(Number(url.searchParams.get("limit")) || 500, 2000);
    let sql = "SELECT obj, added FROM stix_objects";
    const binds = [];
    if (addedAfter) { sql += " WHERE added > ?1"; binds.push(addedAfter); }
    sql += ` ORDER BY added ASC LIMIT ?${binds.length + 1}`;
    binds.push(limit);
    const res = await D.prepare(sql).bind(...binds).all();
    const rows = res.results || [];
    const objects = rows.map((r) => JSON.parse(r.obj));
    const envelope = { more: rows.length >= limit, objects };
    const headers = { "Content-Type": STIX_CT, "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=300" };
    if (rows.length) {
      headers["X-TAXII-Date-Added-First"] = rows[0].added;
      headers["X-TAXII-Date-Added-Last"] = rows[rows.length - 1].added;
    }
    return new Response(JSON.stringify(envelope), { status: 200, headers });
  }
  return tjson({ title: "Not Found", http_status: "404" }, 404);
}
