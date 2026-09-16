#!/usr/bin/env python3
"""Intel Desk — on-demand CVE research inside the Admin Center.

Proxies the CVEFeed.io API (the operator's key, server-side) so the operator can
look up any CVE, vendor or product without leaving the console — then turn a
finding straight into a draft alert for the pipeline.  That last step is the
point: CVEFeed gives the data, we add the analyst brief + distribution.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://cvefeed.io/api"
_CACHE: dict[str, tuple[float, dict]] = {}   # q -> (ts, result), 60s TTL — spares the CVEQL quota
CVE_RE = re.compile(r"^\s*CVE-\d{4}-\d{4,7}\s*$", re.I)
_UA = "SecurityCyber-CTI/1.0 (admin-center)"


def _get(path: str, key: str, params: dict | None = None):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _post(path: str, key: str, body: dict):
    req = urllib.request.Request(BASE + path, method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json",
                                          "Accept": "application/json", "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _row(v: dict) -> dict:
    cvss = v.get("cvss_score")
    sev = (v.get("severity") or "").upper()
    return {
        "id": v.get("id"),
        "title": v.get("title") or v.get("id"),
        "description": (v.get("description") or "").strip(),
        "cvss": cvss,
        "severity": sev or ("CRITICAL" if (cvss or 0) >= 9 else "HIGH" if (cvss or 0) >= 7
                            else "MEDIUM" if (cvss or 0) >= 4 else "LOW"),
        "cvss_version": v.get("cvss_version"),
        "published": (v.get("published") or "")[:10],
        "kev": bool(v.get("cisa_kev_detail")),
        "source": v.get("source_identifier"),
        "url": v.get("url") or (f"https://cvefeed.io/vuln/detail/{v.get('id','')}" if v.get("id") else ""),
    }


def search(q: str, key: str) -> dict:
    if not key:
        return {"error": "CVEFEED_API_KEY is not set in config/env"}
    q = (q or "").strip()
    if not q:
        return {"results": [], "note": "type a CVE id, a keyword, a vendor/product, or a CVEQL query"}
    ck = q.lower()
    hit = _CACHE.get(ck)
    if hit and hit[0] > time.time() - 60:
        return {**hit[1], "cached": True}
    try:
        if CVE_RE.match(q):
            v = _get(f"/vulnerability/{q.upper().strip()}/", key)
            return {"results": [_row(v)], "mode": "cve"}
        # A raw CVEQL expression (has an operator / field) runs as-is; anything
        # else becomes a full-text contains-search over title + description.
        raw_cveql = bool(re.search(r"[<>~]|=|\b(AND|OR)\b|\b(cvss_score|published|last_modified|"
                                   r"severity|vendor|product|title|description|is_kev|cwe)\b", q, re.I))
        if raw_cveql:
            query = q
            mode = "cveql"
        else:
            safe = q.replace('"', " ").strip()
            query = f'description ~ "{safe}"'
            mode = "keyword"
        d = _post("/cveql/search/", key, {"query": query})
        out = {"results": [_row(x) for x in d.get("results", [])[:40]],
               "count": d.get("count"), "mode": mode}
        _CACHE[ck] = (time.time(), out)
        return out
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        if e.code == 429:
            m = re.search(r"in (\d+) second", body)
            return {"error": f"CVEFeed rate limit — try again in {m.group(1) if m else 'a few'} seconds "
                    f"(CVEQL is capped per minute on this plan)."}
        return {"error": f"CVEFeed API HTTP {e.code}: {body}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def latest_kev(key: str, limit: int = 8) -> dict:
    """Most recent CISA KEV entries from CVEFeed — auto-populates the desk so
    there's always something useful on screen without typing a query. Goes
    through the same search()/60s cache as a manual lookup, so opening the
    page repeatedly doesn't burn extra CVEQL quota."""
    out = search("is_kev = true", key)
    if out.get("error"):
        return out
    return {**out, "results": out.get("results", [])[:limit]}


def latest_shipped(root: Path, limit: int = 8) -> list[dict]:
    """Our own most-recently-published briefs — free, always available, no
    CVEFeed call at all. This is the pipeline's own output, not a lookup."""
    hist = root / "state" / "history.jsonl"
    if not hist.exists():
        return []
    rows = []
    for line in hist.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            pass
    rows.sort(key=lambda r: r.get("posted_at", ""), reverse=True)
    out = []
    for r in rows[:limit]:
        out.append({
            "title": r.get("title", ""),
            "cve_ids": r.get("cve_ids", []),
            "score": r.get("score"),
            "lane": r.get("lane", ""),
            "posted_at": r.get("posted_at", ""),
            "url": r.get("post_url") or r.get("url", ""),
        })
    return out


def detail(cve_id: str, key: str) -> dict:
    if not key:
        return {"error": "CVEFEED_API_KEY is not set"}
    try:
        v = _get(f"/vulnerability/{cve_id.upper().strip()}/", key)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    row = _row(v)
    row["references"] = v.get("references") or v.get("reference_urls") or []
    row["weaknesses"] = v.get("weaknesses") or v.get("cwe") or []
    row["products"] = v.get("products") or v.get("affected_products") or []
    row["raw_extra"] = {k: v[k] for k in ("epss_score", "epss_percentile", "vector_string")
                        if k in v}
    return row


def draft_text(row: dict) -> dict:
    """Pre-fill a compose draft from a CVE row — operator edits, then hands off."""
    cvss = row.get("cvss")
    sev = row.get("severity", "")
    title = f"{row['id']} — {sev.title()}" + (f" (CVSS {cvss})" if cvss is not None else "")
    body = (
        f"{row.get('description','').strip()}\n\n"
        f"• Vulnerability: {row['id']}"
        + (f" (CVSS {cvss}{', ' + row['cvss_version'] if row.get('cvss_version') else ''})" if cvss is not None else "")
        + (f" — on CISA KEV" if row.get("kev") else "") + "\n"
        f"• Affected: see the advisory — {row.get('url','')}\n"
        f"• Do now: confirm whether your stack is affected and apply the vendor fix.\n\n"
        f"Source: {row.get('source') or 'NVD'} via CVEFeed. Published {row.get('published','')}."
    )
    return {"title": title, "body": body, "url": row.get("url", ""),
            "short_text": f"{row['id']}" + (f" (CVSS {cvss})" if cvss is not None else "")
            + f": {row.get('description','')[:180]} {row.get('url','')}"}


# --------------------------------------------------------------------------- page
def render_page() -> str:
    return _PAGE


_PAGE = r"""
<style>
.cve{--b:#24344d}
.cve .search{display:flex;gap:8px;margin-bottom:14px}
.cve .search input{flex:1}
.cve .hint{color:var(--dim);font-size:12px;margin-bottom:14px}
.cve .r{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:12px 14px;margin:8px 0}
.cve .r .top{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.cve .r .id{font-weight:700;font-family:ui-monospace,monospace}
.cve .b{font-size:11px;font-weight:700;padding:2px 7px;border-radius:5px}
.cve .b.CRITICAL{background:#7f1d1d;color:#fca5a5}.cve .b.HIGH{background:#7c2d12;color:#fdba74}
.cve .b.MEDIUM{background:#78350f;color:#fcd34d}.cve .b.LOW{background:#1e3a8a;color:#93c5fd}
.cve .b.kev{background:#991b1b;color:#fecaca}
.cve .r p{margin:8px 0 0;color:#cbd5e1;font-size:13px}
.cve .r .act{margin-top:10px;display:flex;gap:8px}
.cve .meta{color:var(--dim);font-size:11.5px}
.cve .section{display:flex;align-items:center;gap:10px;margin:22px 0 10px}
.cve .section h3{margin:0;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--dim)}
.cve .section .btn{margin-left:auto;padding:3px 10px;font-size:12px}
.cve .shipped{display:flex;justify-content:space-between;gap:10px;align-items:baseline;padding:7px 0;border-bottom:1px solid var(--line)}
.cve .shipped .t{font-size:13px}.cve .shipped .m{color:var(--dim);font-size:11.5px;white-space:nowrap}
</style>
<div class="cve">
  <div class="search">
    <input id="cq" placeholder="CVE-2026-1234  ·  keyword  ·  vendor/product  ·  cvss_score >= 9 AND published >= &quot;2026-09-01&quot;">
    <button class="btn" onclick="run()">Search</button>
  </div>
  <div class="hint">Live from CVEFeed.io (NVD + CISA KEV + vendor advisories). A CVE id opens the record; an expression with an operator runs as CVEQL; anything else is a keyword search.</div>
  <div id="cout"></div>

  <div class="section"><h3>Our latest shipped briefs</h3><button class="btn" onclick="loadLatest()">Refresh</button></div>
  <div id="shipped"><p class="meta">loading…</p></div>

  <div class="section"><h3>Latest CISA KEV additions</h3></div>
  <div id="kev"><p class="meta">loading…</p></div>
</div>
<script>
const cq=document.getElementById('cq'),cout=document.getElementById('cout');
cq.addEventListener('keydown',e=>{if(e.key==='Enter')run();});
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function fmtAge(iso){
  if(!iso) return '';
  const ms=Date.now()-new Date(iso).getTime(), h=ms/3600000;
  if(h<1) return Math.round(ms/60000)+'m ago';
  if(h<48) return Math.round(h)+'h ago';
  return Math.round(h/24)+'d ago';
}
function shippedRow(r){
  const cves=(r.cve_ids||[]).join(', ');
  return `<div class="shipped">
    <span class="t">${esc(r.title)}${cves?' <span class="meta">('+esc(cves)+')</span>':''}</span>
    <span class="m">${r.score!=null?r.score+'/100 · ':''}${esc(fmtAge(r.posted_at))}${r.url?' · <a href="'+esc(r.url)+'" target="_blank">open</a>':''}</span>
  </div>`;
}
async function loadLatest(){
  document.getElementById('shipped').innerHTML='<p class=meta>loading…</p>';
  document.getElementById('kev').innerHTML='<p class=meta>loading…</p>';
  let d; try{ d=await (await fetch('/intel/api/latest')).json(); }
  catch(e){ document.getElementById('shipped').innerHTML='<p class=meta>error loading</p>'; return; }
  const sh=d.shipped||[];
  document.getElementById('shipped').innerHTML = sh.length
    ? sh.map(shippedRow).join('') : '<p class=meta>nothing shipped yet</p>';
  const kev=d.kev||{};
  if(kev.error){ document.getElementById('kev').innerHTML='<p class=meta>'+esc(kev.error)+'</p>'; return; }
  const kr=kev.results||[];
  document.getElementById('kev').innerHTML = kr.length
    ? kr.map(card).join('') : '<p class=meta>none returned</p>';
}
loadLatest();
async function run(){
  cout.innerHTML='<p class=meta>searching…</p>';
  let d; try{ d=await (await fetch('/intel/api/search?q='+encodeURIComponent(cq.value))).json(); }
  catch(e){ cout.innerHTML='<p class=meta>error</p>'; return; }
  if(d.error){ cout.innerHTML='<p class=meta>'+esc(d.error)+'</p>'; return; }
  const rs=d.results||[];
  if(!rs.length){ cout.innerHTML='<p class=meta>'+esc(d.note||'no matches')+'</p>'; return; }
  cout.innerHTML=(d.count?`<p class=meta>${d.count} match(es) · showing ${rs.length} · ${d.mode}</p>`:'')
    + rs.map(card).join('');
}
function card(r){
  return `<div class="r">
    <div class="top">
      <span class="id">${esc(r.id)}</span>
      ${r.cvss!=null?`<span class="b ${r.severity}">${r.severity} ${r.cvss}</span>`:''}
      ${r.kev?'<span class="b kev">CISA KEV</span>':''}
      <span class="meta">${esc(r.source||'')} · ${esc(r.published||'')}</span>
      <a class="meta" href="${esc(r.url)}" target="_blank" style="margin-left:auto">CVEFeed ↗</a>
    </div>
    <p>${esc((r.description||'').slice(0,420))}${(r.description||'').length>420?'…':''}</p>
    <div class="act">
      <button class="btn" onclick='draft(${JSON.stringify(r)})'>Draft an alert →</button>
    </div>
  </div>`;
}
async function draft(r){
  const res=await (await fetch('/intel/api/draft',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:r.id})})).json();
  if(res.queued){ if(confirm('Drafted and queued for the pipeline. Open the Marketing desk to edit / publish?')) location.href='/marketing'; }
  else alert('draft failed: '+(res.error||'unknown'));
}
</script>
"""
