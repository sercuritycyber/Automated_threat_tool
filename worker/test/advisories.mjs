import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,writeFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import worker from '../src/index.js';
import {createServer} from 'node:http';
const sql=new DatabaseSync(':memory:');sql.exec(readFileSync(new URL('../schema.sql',import.meta.url),'utf8'));
const D={prepare(query){return {params:[],bind(...p){this.params=p;return this;},async all(){return {results:sql.prepare(query).all(...this.params)};},async first(){return sql.prepare(query).get(...this.params);},async run(){return sql.prepare(query).run(...this.params);}};},async batch(stmts){sql.exec('BEGIN');try{for(const s of stmts)await s.run();sql.exec('COMMIT');}catch(e){sql.exec('ROLLBACK');throw e;}}};
const result=spawnSync('python3',['-c',`import sys,json;sys.path.insert(0,'bin');from advisory_pipeline import build;print(json.dumps(build({'url':'https://vendor.example/a','source':'Vendor','title':'CVE-2026-12345 <script>','summary':'Technical detail 1.2.3 & evidence.','pre_score':80,'published':'2026-10-20T00:00:00Z'},None)))`],{cwd:new URL('../../',import.meta.url),encoding:'utf8'});
assert.equal(result.status,0,result.stderr);const doc=JSON.parse(result.stdout);
const env={DB:D,INGEST_SECRET:'test-only-secret',SITE_URL:'http://127.0.0.1:18765',CONFIG:JSON.stringify({live:true,free_mode:true})};
const req=async(path,method='GET',body,auth=true)=>worker.fetch(new Request(env.SITE_URL+path,{method,headers:{...(auth?{Authorization:'Bearer test-only-secret'}:{}),'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})}),env,{waitUntil(){}});
assert.equal((await req('/internal/advisories','POST',doc,false)).status,401);
assert.equal((await req('/internal/advisories','POST',{...doc,title:'tampered'})).status,400);
assert.equal((await req('/internal/advisories','POST',doc)).status,200);
await req('/internal/advisories','POST',doc);assert.equal(sql.prepare('SELECT count(*) AS n FROM advisories').get().n,1);
let r=await req('/advisories/'+doc.id);assert.equal(r.status,200);let body=await r.text();assert(body.includes('&lt;script&gt;'));assert(!body.includes('CVE-2026-12345 <script>'));assert(body.includes('canonical'));assert(body.includes('Key takeaways'));
r=await req('/rss.xml');assert(r.headers.get('Content-Type').includes('rss+xml'));assert((await r.text()).includes('&lt;script&gt;'));
r=await req('/advisories?q=1.2.3');assert((await r.text()).includes(doc.id));
r=await req('/advisories/'+doc.id+'.stix.json');assert.equal(r.status,200);writeFileSync('/tmp/cti-advisory-stix.json',await r.text());
const indicators=[1,2,3].map(n=>({type:'indicator',spec_version:'2.1',id:`indicator--00000000-0000-4000-8000-${String(n).padStart(12,'0')}`,created:'2026-10-20T00:00:00.000Z',modified:'2026-10-20T00:00:00.000Z',valid_from:'2026-10-20T00:00:00.000Z',pattern_type:'stix',pattern:`[ipv4-addr:value = '192.0.2.${n}']`}));
assert.equal((await req('/internal/stix','POST',{objects:indicators})).status,200);
assert.equal((await req('/taxii2/','POST',{})).status,405);
assert.equal((await req('/subscribe','POST',{})).status,410);
console.log('Advisory auth, provenance, idempotency, HTML, RSS, search, STIX and existing TAXII routing passed');
if(process.argv.includes('--serve')) {
 const server=createServer(async(incoming,out)=>{try{const chunks=[];for await(const c of incoming)chunks.push(c);const bytes=Buffer.concat(chunks);const request=new Request(env.SITE_URL+incoming.url,{method:incoming.method,headers:incoming.headers,...(bytes.length?{body:bytes}:{})});const response=await worker.fetch(request,env,{waitUntil(){}});out.writeHead(response.status,Object.fromEntries(response.headers));out.end(Buffer.from(await response.arrayBuffer()));}catch(e){out.writeHead(500);out.end(String(e));}});
 server.listen(18765,'127.0.0.1',()=>console.log('Local protocol fixture ready on 18765'));
}
