// Public source archive. Model findings are separate annotations, never source replacements.
import {html, json, esc} from './util.js';
import {page} from './pages.js';
import {timingSafeEqual} from './tokens.js';
const ID = /^[a-f0-9]{64}$/;
const canonical = (d) => JSON.stringify(Object.fromEntries(['cve_ids','source','source_text','source_url','title'].map(k => [k,d[k]])));
const sha = async s => [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(x=>x.toString(16).padStart(2,'0')).join('');
const validDate = s => typeof s === 'string' && /^\d{4}-\d\d-\d\dT.*Z$/.test(s) && Number.isFinite(Date.parse(s));
const publicURL = s => {try {const u=new URL(s);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password;}catch{return false;}};
export async function validDocument(d) {
  if (!d || !ID.test(d.id) || d.schema_version !== 1 || d.method !== 'deterministic-source-excerpt' || d.verification !== 'source-reported') return false;
  if (!['title','source','source_text','source_url','action','limitations'].every(k=>typeof d[k]==='string'&&d[k].length>0&&d[k].length<=32000)) return false;
  if (!publicURL(d.source_url)||!validDate(d.published)||!Number.isInteger(d.score)||d.score<0||d.score>100) return false;
  if (!Array.isArray(d.takeaways)||d.takeaways.length!==3||!d.takeaways.every(x=>typeof x==='string'&&x.length<=4000)) return false;
  if (!Array.isArray(d.cve_ids)||!d.cve_ids.every(x=>/^CVE-\d{4}-\d{4,}$/.test(x)&&(d.title+'\n'+d.source_text).toUpperCase().includes(x))) return false;
  return await sha(canonical(d)) === d.id;
}
async function uuid(key) {
  // RFC 4122 UUIDv5, DNS namespace. Stable across downloads and retries.
  const ns=Uint8Array.from('6ba7b8109dad11d180b400c04fd430c8'.match(/../g),x=>parseInt(x,16));
  const text=new TextEncoder().encode('cti.securitycyber.uk/'+key), bytes=new Uint8Array(ns.length+text.length);
  bytes.set(ns);bytes.set(text,ns.length);
  const h=new Uint8Array(await crypto.subtle.digest('SHA-1',bytes)).slice(0,16);h[6]=(h[6]&15)|80;h[8]=(h[8]&63)|128;
  const s=[...h].map(x=>x.toString(16).padStart(2,'0')).join('');return `${s.slice(0,8)}-${s.slice(8,12)}-${s.slice(12,16)}-${s.slice(16,20)}-${s.slice(20)}`;
}
export async function stix(d, base) {
  const common={spec_version:'2.1',created:d.published,modified:d.published};
  const action={...common,type:'course-of-action',id:'course-of-action--'+await uuid(d.id+'/action'),name:'Assess source-reported exposure',description:d.action};
  const vulns=await Promise.all(d.cve_ids.map(async c=>({...common,type:'vulnerability',id:'vulnerability--'+await uuid(d.id+'/'+c),name:c,external_references:[{source_name:'cve',external_id:c}]})));
  const report={...common,type:'report',id:'report--'+await uuid(d.id),name:d.title,published:d.published,report_types:['threat-report'],
    description:d.takeaways.join('\n')+'\n\nSource excerpt:\n'+d.source_text+'\n\n'+d.limitations,
    external_references:[{source_name:d.source,url:d.source_url},{source_name:'SecurityCyber',url:base+'/advisories/'+d.id}],
    object_refs:[action.id,...vulns.map(x=>x.id)],confidence:0};
  return {type:'bundle',id:'bundle--'+await uuid(d.id+'/bundle'),objects:[report,action,...vulns]};
}
const xml = s => esc(String(s)).replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g,'');
export async function route(request,env,cfg,url) {
  const path=url.pathname, D=env.DB, base=cfg.site_url || url.origin;
  if(path==='/internal/advisories') {
    if(request.method!=='POST')return json({error:'method not allowed'},405);
    const token=(request.headers.get('authorization')||'').replace(/^Bearer\s+/i,'');
    if(!env.INGEST_SECRET||!timingSafeEqual(token,env.INGEST_SECRET))return json({error:'unauthorized'},401);
    const raw=await request.text();if(raw.length>100000)return json({error:'too large'},413);
    let d;try{d=JSON.parse(raw);}catch{return json({error:'invalid JSON'},400);}
    if(!await validDocument(d))return json({error:'invalid source advisory'},400);
    await D.prepare('INSERT OR IGNORE INTO advisories(id,title,source_text,published,document,added_at) VALUES(?,?,?,?,?,?)').bind(d.id,d.title,d.source_text,d.published,JSON.stringify(d),new Date().toISOString()).run();
    return json({id:d.id,stored:true});
  }
  const match=path.match(/^\/advisories\/([a-f0-9]{64})(\.stix\.json|\.json)?$/);
  if(match) {
    if(request.method!=='GET')return json({error:'method not allowed'},405);
    const row=await D.prepare('SELECT document FROM advisories WHERE id=?').bind(match[1]).first();
    if(!row)return html(page(cfg,'Not found','<h1>Advisory not found</h1>'),404);
    const d=JSON.parse(row.document);
    if(match[2]==='.json')return json(d);
    if(match[2]==='.stix.json')return json(await stix(d,base),200,{'Content-Type':'application/stix+json;version=2.1'});
    const body=`<article><h1>${esc(d.title)}</h1><p>${esc(d.published)} · Source-reported · Score ${d.score}</p><h2>Key takeaways / immediate actions</h2><ul>${d.takeaways.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><h2>Technical source detail</h2><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(d.source_text)}</pre><p>${esc(d.cve_ids.join(', '))}</p><p>${esc(d.limitations)}</p><p><a href="${esc(d.source_url)}" rel="noopener noreferrer">Original source: ${esc(d.source)}</a></p><p><a href="${path}.json">JSON</a> · <a href="${path}.stix.json">STIX 2.1</a> · <a href="/advisories">Archive</a></p></article>`;
    return html(page(cfg,d.title,body).replace('</head>',`<link rel="canonical" href="${esc(base+path)}"></head>`));
  }
  if(!['/advisories','/rss.xml','/sitemap.xml'].includes(path))return null;
  if(request.method!=='GET')return json({error:'method not allowed'},405);
  if(path==='/sitemap.xml'&&!url.searchParams.has('before')) {
    const groups=(await D.prepare('SELECT MAX(seq) AS last_seq FROM advisories GROUP BY CAST((seq-1)/1000 AS INTEGER)').all()).results;
    return new Response(`<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${groups.map(g=>`<sitemap><loc>${xml(base+'/sitemap.xml?before='+(g.last_seq+1))}</loc></sitemap>`).join('')}</sitemapindex>`,{headers:{'Content-Type':'application/xml'}});
  }
  const before=Number(url.searchParams.get('before')||Number.MAX_SAFE_INTEGER);
  if(!Number.isSafeInteger(before)||before<1)return json({error:'invalid cursor'},400);
  const q=(url.searchParams.get('q')||'').slice(0,200);
  const limit=path==='/sitemap.xml'?1000:50;
  const rows=(await D.prepare('SELECT seq,document FROM advisories WHERE seq < ? AND (? = \'\' OR instr(lower(title || char(10) || source_text),lower(?)) > 0) ORDER BY seq DESC LIMIT ?').bind(before,q,q,limit+1).all()).results;
  const more=rows.length>limit;rows.splice(limit);const docs=rows.map(r=>JSON.parse(r.document));
  const next=more?`${path}?before=${rows.at(-1).seq}&q=${encodeURIComponent(q)}`:null;
  if(path==='/rss.xml') {
    const items=docs.map(d=>`<item><title>${xml(d.title)}</title><link>${xml(base+'/advisories/'+d.id)}</link><guid isPermaLink="true">${xml(base+'/advisories/'+d.id)}</guid><pubDate>${new Date(d.published).toUTCString()}</pubDate><description>${xml(d.takeaways.join('\n')+'\n\n'+d.source_text+'\n\nSource: '+d.source_url)}</description></item>`).join('');
    return new Response(`<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>SecurityCyber source advisories</title><link>${xml(base+'/advisories')}</link><description>Free source-reported CTI. No model required.</description>${items}</channel></rss>`,{headers:{'Content-Type':'application/rss+xml; charset=utf-8'}});
  }
  if(path==='/sitemap.xml')return new Response(`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${docs.map(d=>`<url><loc>${xml(base+'/advisories/'+d.id)}</loc></url>`).join('')}</urlset>`,{headers:{'Content-Type':'application/xml',...(next?{'Link':`<${base+next}>; rel="next"`}:{})}});
  return html(page(cfg,'Source advisory archive',`<h1>Source advisory archive</h1><p>Free, deterministic, source-attributed intelligence. AI checks are complementary; source reports are not independent verification.</p><p><a href="/rss.xml">RSS</a> · <a href="/taxii2/">TAXII 2.1 indicators</a></p><form><label>Search CVEs, hashes or source text <input name="q" value="${esc(q)}" maxlength="200"></label><button>Search</button></form>${docs.map(d=>`<article><h2><a href="/advisories/${d.id}">${esc(d.title)}</a></h2><ul>${d.takeaways.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></article>`).join('')}${next?`<a href="${esc(next)}">Older advisories</a>`:''}`, '', '',!!q));
}
