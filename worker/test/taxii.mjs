// TAXII 2.1 / STIX feed checks — run with plain Node.
//   node worker/test/taxii.mjs
import * as taxii from "../src/taxii.js";

let failed = 0;
const ok = (name, cond) => { console.log(`  [${cond ? "ok  " : "FAIL"}] ${name}`); if (!cond) failed++; };

// ---- mock D1 (enough for ingest batch + objects select) ----
function mockD() {
  const store = new Map();
  return {
    _store: store,
    prepare(sql) {
      return {
        _sql: sql, _b: [],
        bind(...a) { this._b = a; return this; },
        async all() {
          let rows = [...store.values()];
          if (/added > \?1/.test(this._sql)) { const after = this._b[0]; rows = rows.filter((r) => r.added > after); }
          rows.sort((a, b) => (a.added < b.added ? -1 : 1));
          const limit = this._b[this._b.length - 1];
          rows = rows.slice(0, limit);
          return { results: rows.map((r) => ({ obj: r.obj, added: r.added })) };
        },
      };
    },
    async batch(stmts) { for (const s of stmts) { const b = s._b; store.set(b[0], { id: b[0], added: b[4], obj: b[6] }); } },
  };
}

const cfg = { site_url: "https://cti.securitycyber.uk", contact_email: "x@y.z" };
const U = (p) => new URL("https://cti.securitycyber.uk" + p);

const ind = (id, pattern) => ({ type: "indicator", spec_version: "2.1", id, pattern, pattern_type: "stix" });

const D = mockD();

// ---- ingest ----
const n = await taxii.ingest(D, { objects: [
  ind("indicator--11111111-1111-4111-8111-111111111111", "[ipv4-addr:value = '45.33.32.156']"),
  ind("indicator--22222222-2222-4222-8222-222222222222", "[file:hashes.'SHA-256' = 'abc']"),
  { type: "malware", id: "not-an-indicator" },            // skipped
  { id: "no-type" },                                       // skipped
] });
ok("ingest returns indicator count (2)", n === 2);
ok("ingest stored 2", D._store.size === 2);

// ---- discovery ----
let r = await taxii.handle(D, cfg, U("/taxii2/"), "/taxii2/");
ok("discovery 200 + taxii content-type", r.status === 200 && r.headers.get("Content-Type").includes("taxii+json;version=2.1"));
let j = await r.json();
ok("discovery lists api_roots", Array.isArray(j.api_roots) && j.api_roots[0].endsWith("/taxii2/api/"));

// ---- api root ----
r = await taxii.handle(D, cfg, U("/taxii2/api/"), "/taxii2/api/");
j = await r.json();
ok("api-root advertises taxii 2.1", j.versions[0].includes("version=2.1"));

// ---- collections ----
r = await taxii.handle(D, cfg, U("/taxii2/api/collections/"), "/taxii2/api/collections/");
j = await r.json();
ok("collection is read-only + indicators", j.collections[0].id === "indicators" && j.collections[0].can_read && !j.collections[0].can_write);

// ---- objects ----
r = await taxii.handle(D, cfg, U("/taxii2/api/collections/indicators/objects/"), "/taxii2/api/collections/indicators/objects/");
ok("objects envelope is taxii+json (TAXII 2.1), not stix", r.headers.get("Content-Type").includes("taxii+json;version=2.1"));
j = await r.json();
ok("objects envelope has 2 indicators", Array.isArray(j.objects) && j.objects.length === 2 && j.objects[0].type === "indicator");
ok("objects has X-TAXII-Date-Added headers", !!r.headers.get("X-TAXII-Date-Added-First"));
ok("no more/next when all fit", j.more === false && j.next === undefined);

// pagination: limit below count -> more:true + a next cursor
r = await taxii.handle(D, cfg, U("/taxii2/api/collections/indicators/objects/?limit=1"), "/taxii2/api/collections/indicators/objects/");
j = await r.json();
ok("more:true + next cursor when paging", j.more === true && typeof j.next === "string" && j.objects.length === 1);

// ---- added_after filter (everything has the same added ts => after=now returns none) ----
const future = new Date(Date.now() + 60000).toISOString();
r = await taxii.handle(D, cfg, U(`/taxii2/api/collections/indicators/objects/?added_after=${future}`), "/taxii2/api/collections/indicators/objects/");
j = await r.json();
ok("added_after filters out older", j.objects.length === 0);

// ---- 404 ----
r = await taxii.handle(D, cfg, U("/taxii2/api/collections/nope/"), "/taxii2/api/collections/nope/");
ok("unknown path 404s", r.status === 404);

console.log(failed ? `\n❌ ${failed} TAXII check(s) failed` : "\n✅ TAXII checks passed");
process.exit(failed ? 1 : 0);
