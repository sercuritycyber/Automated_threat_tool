// TAXII 2.1 / STIX feed checks — run with plain Node.
//   node worker/test/taxii.mjs
import * as taxii from "../src/taxii.js";

let failed = 0;
const ok = (name, cond) => { console.log(`  [${cond ? "ok  " : "FAIL"}] ${name}`); if (!cond) failed++; };

// Real SQLite catches cursor ordering and SQL semantics that a Map mock misses.
import { DatabaseSync } from "node:sqlite";
import {readFileSync} from "node:fs";
function mockD() {
 const sql=new DatabaseSync(":memory:");sql.exec(readFileSync(new URL("../schema.sql",import.meta.url),"utf8"));
 const D={_sql:sql,prepare(query){return {params:[],bind(...p){this.params=p;return this;},async all(){return {results:sql.prepare(query).all(...this.params)};},async run(){return sql.prepare(query).run(...this.params);},async first(){return sql.prepare(query).get(...this.params);}};},async batch(stmts){sql.exec("BEGIN");try{for(const s of stmts)await s.run();sql.exec("COMMIT");}catch(e){sql.exec("ROLLBACK");throw e;}}};
 return D;
}

const cfg = { site_url: "https://cti.securitycyber.uk", contact_email: "x@y.z" };
const U = (p) => new URL("https://cti.securitycyber.uk" + p);

const ind = (id, pattern) => ({ type: "indicator", spec_version: "2.1", id, pattern, pattern_type: "stix", created:"2026-01-01T00:00:00.000Z", modified:"2026-01-01T00:00:00.000Z", valid_from:"2026-01-01T00:00:00.000Z" });

const D = mockD();

// ---- ingest ----
const n = await taxii.ingest(D, { objects: [
  ind("indicator--11111111-1111-4111-8111-111111111111", "[ipv4-addr:value = '45.33.32.156']"),
  ind("indicator--22222222-2222-4222-8222-222222222222", "[file:hashes.'SHA-256' = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa']"),
  { type: "malware", id: "not-an-indicator" },            // skipped
  { id: "no-type" },                                       // skipped
] });
ok("ingest returns indicator count (2)", n === 2);
ok("ingest stored 2", D._sql.prepare("SELECT count(*) AS n FROM stix_objects").get().n === 2);

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

// Follow the cursor: equal timestamps must not lose the second indicator.
const firstId=j.objects[0].id;
r=await taxii.handle(D,cfg,U("/taxii2/api/collections/indicators/objects/?limit=1&next="+encodeURIComponent(j.next)),"/taxii2/api/collections/indicators/objects/");
j=await r.json();ok("same-batch pagination loses no indicators",j.objects.length===1&&j.objects[0].id!==firstId&&!j.more);
r=await taxii.handle(D,cfg,U("/taxii2/api/collections/indicators/objects/?limit=-1"),"/taxii2/api/collections/indicators/objects/");ok("negative limit rejected",r.status===400);
r=await taxii.handle(D,cfg,U("/taxii2/api/collections/indicators/manifest/"),"/taxii2/api/collections/indicators/manifest/");j=await r.json();ok("manifest available",j.objects.length===2&&!!j.objects[0].version);
r=await taxii.handle(D,cfg,U("/taxii2/api/collections/indicators/objects/?match[type]=malware"),"/taxii2/api/collections/indicators/objects/");j=await r.json();ok("type filter respected",j.objects.length===0);
const broken={prepare(){throw Error("offline");}};
r=await taxii.handle(broken,cfg,U("/taxii2/api/collections/indicators/objects/"),"/taxii2/api/collections/indicators/objects/");ok("storage failure is not empty success",r.status===503);

// ---- added_after filter (everything has the same added ts => after=now returns none) ----
const future = new Date(Date.now() + 60000).toISOString();
r = await taxii.handle(D, cfg, U(`/taxii2/api/collections/indicators/objects/?added_after=${future}`), "/taxii2/api/collections/indicators/objects/");
j = await r.json();
ok("added_after filters out older", j.objects.length === 0);

// Incremental updates remain visible; stale replays cannot roll indicators back.
const id="indicator--11111111-1111-4111-8111-111111111111";
D._sql.prepare("UPDATE stix_objects SET added=? WHERE id=?").run("2026-01-01T00:00:00.000Z",id);
const changed={...ind(id,"[ipv4-addr:value = '192.0.2.10']"),modified:"2026-02-01T00:00:00Z"};
await taxii.ingest(D,{objects:[changed]});
let updated=D._sql.prepare("SELECT added,obj FROM stix_objects WHERE id=?").get(id);
ok("update advances added timestamp",updated.added>"2026-01-01T00:00:00.000Z");
await taxii.ingest(D,{objects:[ind(id,"[ipv4-addr:value = '192.0.2.99']")]});
ok("older replay cannot overwrite",JSON.parse(D._sql.prepare("SELECT obj FROM stix_objects WHERE id=?").get(id).obj).pattern===changed.pattern);
r=await taxii.handle(D,cfg,U("/taxii2/api/collections/indicators/objects/"+id+"/versions/"),"/taxii2/api/collections/indicators/objects/"+id+"/versions/");j=await r.json();ok("versions endpoint returns available version",j.versions[0]===changed.modified);

// ---- 404 ----
r = await taxii.handle(D, cfg, U("/taxii2/api/collections/nope/"), "/taxii2/api/collections/nope/");
ok("unknown path 404s", r.status === 404);

console.log(failed ? `\n❌ ${failed} TAXII check(s) failed` : "\n✅ TAXII checks passed");
process.exit(failed ? 1 : 0);
