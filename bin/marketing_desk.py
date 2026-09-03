#!/usr/bin/env python3
"""Marketing Desk — content console for the Blue Team CTI panel.

Pulls draft posts from the local repo (drafts/*.md, state/pending.json,
state/decision.json), formats each one for every channel, tracks
status / schedule / budget / KPIs, and exports an upload-ready bundle.

Wired into web/app.py as the /marketing page. Persists to state/marketing.json.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

COMPANY_URL = "https://www.linkedin.com/company/security-cyber-uk"
COMPANY_PLUG = (
    "\n\n———\n"
    "Published by SecurityCyber · UK threat intelligence for small teams.\n"
    f"Follow the company page: {COMPANY_URL}"
)

# channel -> (hard char limit, keep hashtags, append company plug)
PLATFORMS = {
    "linkedin":  (3000, True,  True),
    "x":         (280,  False, False),
    "bluesky":   (300,  False, False),
    "mastodon":  (500,  True,  False),
    "threads":   (500,  True,  False),
    "instagram": (2200, True,  False),
    "facebook":  (5000, True,  True),
}
STATUSES = ["draft", "queued", "scheduled", "approved", "posted", "skip"]


# --------------------------------------------------------------------------- parse
def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:60] or "post"


def _pid(url: str, title: str) -> str:
    key = (url or "").strip().lower() or (title or "").strip().lower()
    return _slug(title)[:32] + "-" + hashlib.md5(key.encode()).hexdigest()[:6]


def _first_url(text: str) -> str:
    m = re.search(r"https?://\S+", text or "")
    return m.group(0).rstrip(").,") if m else ""


def _parse_draft(text: str, src: str) -> list[dict]:
    out = []
    for chunk in re.split(r"\n##\s+post\s+", text)[1:]:
        lines = chunk.splitlines()
        head = lines[0] if lines else ""
        sm = re.search(r"score\s+(\d+)", head)
        rest = "\n".join(lines[1:])
        tm = re.match(r"\s*(.+?)\s*<(https?://[^>]+)>", rest)
        title = (tm.group(1).strip() if tm else (lines[1].strip() if len(lines) > 1 else "Untitled"))
        url = tm.group(2).strip() if tm else _first_url(rest)
        body = rest
        if tm:
            body = rest[tm.end():]
        body = re.sub(r"^\s*cves:.*$", "", body, flags=re.M).strip()
        short = ""
        smt = re.search(r"\n\[short\]\s*(.+)$", body, flags=re.S)
        if smt:
            short = smt.group(1).strip()
            body = body[:smt.start()].strip()
        cves = re.findall(r"CVE-\d{4}-\d{3,7}", chunk)
        out.append({
            "title": title, "url": url, "post_text": body, "short_text": short,
            "score": int(sm.group(1)) if sm else None, "cve_ids": sorted(set(cves)),
            "source": src, "origin": "draft",
        })
    return out


def scan_posts(root: Path) -> list[dict]:
    """All candidate posts from the local repo, newest first, deduped by URL."""
    seen: dict[str, dict] = {}
    order: list[str] = []

    def add(p: dict, origin: str, prio: int):
        if not (p.get("post_text") or "").strip():
            return
        p = dict(p)
        p["origin"] = origin
        p["id"] = _pid(p.get("url", ""), p.get("title", ""))
        key = (p.get("url") or p.get("title") or "").strip().lower()
        cur = seen.get(key)
        if cur is None:
            seen[key] = p
            seen[key]["_prio"] = prio
            order.append(key)
        elif prio > cur.get("_prio", 0):
            p["_prio"] = prio
            seen[key] = p

    # 1. operator queue (highest priority — these are chosen)
    pend = root / "state" / "pending.json"
    if pend.exists():
        try:
            q = json.loads(pend.read_text()).get("queue", [])
            for it in q:
                add(it, "queue", 3)
        except Exception:
            pass
    # 2. latest analyst decision
    dec = root / "state" / "decision.json"
    if dec.exists():
        try:
            d = json.loads(dec.read_text())
            for it in d.get("posts", []) or []:
                add(it, "decision", 2)
        except Exception:
            pass
    # 3. draft history
    for f in sorted((root / "drafts").glob("*.md"), reverse=True):
        try:
            for it in _parse_draft(f.read_text(), f.name):
                add(it, "draft", 1)
        except Exception:
            pass

    posts = [seen[k] for k in order]
    for p in posts:
        p.pop("_prio", None)
    return posts


# --------------------------------------------------------------------------- format
def _teaser(text: str, limit: int, short: str = "") -> str:
    if short and len(short) <= limit:
        return short
    if len(text) <= limit:
        return text
    hook = (text or "").strip().split("\n", 1)[0].strip()
    url = _first_url(text)
    room = limit - (len(url) + 2 if url else 1)
    body = hook if len(hook) <= room else hook[:room - 1].rstrip() + "…"
    return (body + ("\n" + url if url else "")).strip()[:limit]


def format_for(post: dict, platform: str, store_post: dict | None = None) -> str:
    limit, keep_tags, plug = PLATFORMS[platform]
    base = ((store_post or {}).get("edited") or post.get("post_text") or "").strip()
    short = post.get("short_text") or ""
    if not keep_tags:
        base = re.sub(r"\n?#\w[\w-]*(\s+#\w[\w-]*)*\s*$", "", base).strip()
    if limit < 600:
        return _teaser(base, limit, short)
    if plug and "company/security-cyber-uk" not in base:
        cand = base + COMPANY_PLUG
        if len(cand) <= limit:
            base = cand
    return base[:limit].rstrip()


def bundle(post: dict, store_post: dict | None = None) -> dict:
    return {
        "id": post["id"], "title": post.get("title"), "slug": _slug(post.get("title", "")),
        "url": post.get("url"), "score": post.get("score"),
        "cve_ids": post.get("cve_ids", []),
        "status": (store_post or {}).get("status", "draft"),
        "scheduled_for": (store_post or {}).get("scheduled_for", ""),
        "formats": {p: format_for(post, p, store_post) for p in PLATFORMS},
    }


# --------------------------------------------------------------------------- store
def _store_path(root: Path) -> Path:
    return root / "state" / "marketing.json"


def store_load(root: Path) -> dict:
    p = _store_path(root)
    if p.exists():
        try:
            d = json.loads(p.read_text())
        except Exception:
            d = {}
    else:
        d = {}
    d.setdefault("posts", {})
    d.setdefault("kpis", {})
    d.setdefault("budget", {"monthly": 0, "entries": []})
    return d


def store_save(root: Path, d: dict) -> None:
    _store_path(root).write_text(json.dumps(d, indent=2))


def update_post(root: Path, pid: str, **fields) -> dict:
    d = store_load(root)
    rec = d["posts"].setdefault(pid, {})
    for k, v in fields.items():
        if v is None:
            continue
        rec[k] = v
    rec["updated_at"] = datetime.now(timezone.utc).isoformat()
    store_save(root, d)
    return rec


# --------------------------------------------------------------------------- kpis
def _posted_urls(root: Path) -> set[str]:
    urls = set()
    h = root / "state" / "history.jsonl"
    if h.exists():
        for line in h.read_text().splitlines():
            try:
                urls.add((json.loads(line).get("url") or "").strip().lower())
            except Exception:
                pass
    return urls


def kpis(root: Path, creds: dict | None = None) -> dict:
    posts = scan_posts(root)
    store = store_load(root)
    posted = _posted_urls(root)
    counts = {s: 0 for s in STATUSES}
    now = datetime.now(timezone.utc)
    sched_week = 0
    for p in posts:
        rec = store["posts"].get(p["id"], {})
        st = rec.get("status")
        if not st:
            st = "posted" if (p.get("url") or "").strip().lower() in posted else \
                 ("queued" if p["origin"] == "queue" else "draft")
        counts[st] = counts.get(st, 0) + 1
        sf = rec.get("scheduled_for")
        if sf:
            try:
                dt = datetime.fromisoformat(sf.replace("Z", "+00:00"))
                if 0 <= (dt - now).days < 7:
                    sched_week += 1
            except Exception:
                pass

    out = {
        "content": {"total": len(posts), "by_status": counts, "scheduled_next_7d": sched_week},
        "manual": store.get("kpis", {}),
        "budget": store.get("budget", {"monthly": 0, "entries": []}),
    }
    spent = sum(float(x.get("amount", 0) or 0) for x in out["budget"].get("entries", []))
    out["budget"]["spent"] = round(spent, 2)
    out["budget"]["remaining"] = round(float(out["budget"].get("monthly", 0) or 0) - spent, 2)

    # live subscriber numbers from the CTI worker, if reachable
    c = creds or {}
    wu, tok = (c.get("CTI_WORKER_URL") or "").rstrip("/"), c.get("CTI_ADMIN_TOKEN")
    if requests and wu and tok:
        try:
            r = requests.get(f"{wu}/admin/subscribers",
                             headers={"Authorization": f"Bearer {tok}",
                                      "User-Agent": "Mozilla/5.0 (marketing-desk)"}, timeout=8)
            subs = r.json().get("subscribers", [])
            out["audience"] = {
                "subscribers_total": len(subs),
                "active": sum(1 for s in subs if s.get("status") == "active"),
                "by_tier": _tally(s.get("tier") for s in subs),
            }
        except Exception as e:
            out["audience"] = {"error": str(e)[:120]}
    return out


def _tally(it):
    d: dict[str, int] = {}
    for v in it:
        d[v or "?"] = d.get(v or "?", 0) + 1
    return d


# --------------------------------------------------------------------------- API glue
def posts_payload(root: Path) -> dict:
    posts = scan_posts(root)
    store = store_load(root)
    posted = _posted_urls(root)
    rows = []
    for p in posts:
        rec = store["posts"].get(p["id"], {})
        st = rec.get("status") or ("posted" if (p.get("url") or "").strip().lower() in posted
                                   else ("queued" if p["origin"] == "queue" else "draft"))
        rows.append({
            **{k: p.get(k) for k in ("id", "title", "url", "score", "cve_ids", "origin", "source")},
            "status": st,
            "scheduled_for": rec.get("scheduled_for", ""),
            "notes": rec.get("notes", ""),
            "edited": bool(rec.get("edited")),
            "formats": {pl: format_for(p, pl, rec) for pl in PLATFORMS},
            "counts": {pl: len(format_for(p, pl, rec)) for pl in PLATFORMS},
        })
    return {"posts": rows, "platforms": {k: v[0] for k, v in PLATFORMS.items()}, "statuses": STATUSES}


def export_bundle(root: Path) -> dict:
    posts = scan_posts(root)
    store = store_load(root)
    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "company_page": COMPANY_URL,
        "posts": [bundle(p, store["posts"].get(p["id"], {})) for p in posts],
    }


# --------------------------------------------------------------------------- page
def render_page(root: Path, creds: dict | None = None) -> str:
    return _PAGE_HTML


_PAGE_HTML = r"""
<style>
.mkt{--b:#24344d;--c:#151f32;--m:#94a3b8;--ac:#3b82f6}
.mkt h2{font-size:14px;text-transform:uppercase;letter-spacing:1px;color:var(--ac);margin:22px 0 10px}
.mkt .kgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.mkt .k{background:var(--c);border:1px solid var(--b);border-radius:8px;padding:12px 14px}
.mkt .k b{display:block;font-size:22px;color:#fff}.mkt .k span{font-size:11px;color:var(--m);text-transform:uppercase;letter-spacing:.5px}
.mkt .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.mkt .card{background:var(--c);border:1px solid var(--b);border-radius:8px;margin:8px 0;overflow:hidden}
.mkt .card>.hd{padding:12px 14px;cursor:pointer;display:flex;gap:10px;align-items:center}
.mkt .card>.hd:hover{background:#1c2942}
.mkt .card .t{font-weight:600;color:#fff;flex:1}
.mkt .pill{font-size:11px;padding:2px 8px;border-radius:20px;border:1px solid var(--b);color:var(--m)}
.mkt .pill.posted{color:#4ade80;border-color:#166534}.mkt .pill.scheduled{color:#fcd34d;border-color:#78350f}
.mkt .pill.approved{color:#93c5fd;border-color:#1e3a8a}.mkt .pill.skip{color:#f87171;border-color:#7f1d1d}
.mkt .bd{padding:0 14px 14px;border-top:1px solid var(--b);display:none}
.mkt .card.open .bd{display:block}
.mkt .tabs{display:flex;gap:4px;flex-wrap:wrap;margin:12px 0 8px}
.mkt .tabs button{background:#0b1120;border:1px solid var(--b);color:var(--m);padding:5px 10px;border-radius:6px;font-size:12px;cursor:pointer}
.mkt .tabs button.on{color:#fff;border-color:var(--ac)}
.mkt textarea{width:100%;min-height:150px;background:#0b1120;border:1px solid var(--b);color:#e2e8f0;border-radius:6px;padding:10px;font:13px/1.5 ui-monospace,monospace;resize:vertical}
.mkt .mini{font-size:11px;color:var(--m)}
.mkt select,.mkt input[type=datetime-local],.mkt input[type=number],.mkt input[type=text]{background:#0b1120;border:1px solid var(--b);color:#e2e8f0;border-radius:6px;padding:6px 8px;font-size:13px}
.mkt button.act{background:var(--ac);border:0;color:#fff;padding:6px 12px;border-radius:6px;cursor:pointer;font-size:13px}
.mkt button.ghost{background:#0b1120;border:1px solid var(--b);color:#e2e8f0;padding:6px 12px;border-radius:6px;cursor:pointer;font-size:13px}
.mkt table{width:100%;border-collapse:collapse;font-size:13px}.mkt td,.mkt th{border-bottom:1px solid var(--b);padding:6px 8px;text-align:left}
</style>
<div class=mkt>
  <div class=row style="justify-content:space-between">
    <div class=mini id=srcline>loading…</div>
    <div class=row>
      <button class=ghost onclick="loadAll()">↻ Refresh</button>
      <button class=act onclick="exportBundle()">⬇ Export upload pack</button>
    </div>
  </div>

  <h2>Performance</h2>
  <div class=kgrid id=kpis></div>

  <h2>Budget</h2>
  <div id=budget></div>

  <h2>Content pipeline</h2>
  <div class=row style="margin-bottom:6px">
    <label class=mini>filter</label>
    <select id=flt onchange=render()><option value="">all</option>
      <option>draft</option><option>queued</option><option>scheduled</option>
      <option>approved</option><option>posted</option><option>skip</option></select>
  </div>
  <div id=list></div>
</div>

<script>
let DATA={posts:[],platforms:{}},KPI={};
const $=s=>document.querySelector(s);
async function loadAll(){
  DATA=await (await fetch('/marketing/api/posts')).json();
  KPI=await (await fetch('/marketing/api/kpis')).json();
  $('#srcline').textContent=DATA.posts.length+' posts from drafts/ · pending.json · decision.json';
  renderKpis();renderBudget();render();
}
function renderKpis(){
  const c=KPI.content||{},bs=c.by_status||{},a=KPI.audience||{},m=KPI.manual||{};
  const cell=(l,v)=>`<div class=k><span>${l}</span><b>${v??'—'}</b></div>`;
  $('#kpis').innerHTML=[
    cell('Total content',c.total),
    cell('Queued',bs.queued),cell('Scheduled 7d',c.scheduled_next_7d),
    cell('Posted',bs.posted),
    cell('Subscribers',a.subscribers_total),cell('Active subs',a.active),
    cell('Followers (man.)',m.followers),cell('Impressions 30d',m.impressions_30d),
    cell('Leads 30d',m.leads_30d),cell('CAC £',m.cac)
  ].join('');
}
function renderBudget(){
  const b=KPI.budget||{entries:[]};
  $('#budget').innerHTML=`<div class=row>
    <div class=k><span>Monthly</span><b>£${b.monthly||0}</b></div>
    <div class=k><span>Spent</span><b>£${b.spent||0}</b></div>
    <div class=k><span>Remaining</span><b>£${b.remaining||0}</b></div></div>
    <div class=row style="margin-top:8px">
      <input type=number id=bmon placeholder="monthly £" value="${b.monthly||''}" style="width:110px">
      <button class=ghost onclick=saveBudget()>set monthly</button>
      <span class=mini>|</span>
      <input type=text id=bd placeholder="line item" style="width:150px">
      <input type=number id=ba placeholder="£" style="width:80px">
      <button class=ghost onclick=addSpend()>+ spend</button>
    </div>
    <table style="margin-top:6px"><tr><th>item</th><th>£</th><th>when</th></tr>
    ${(b.entries||[]).map(e=>`<tr><td>${esc(e.item)}</td><td>${e.amount}</td><td class=mini>${(e.at||'').slice(0,10)}</td></tr>`).join('')}</table>`;
}
async function saveBudget(){await post('/marketing/api/kpis',{monthly:$('#bmon').value});loadAll();}
async function addSpend(){await post('/marketing/api/kpis',{spend_item:$('#bd').value,spend_amount:$('#ba').value});loadAll();}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function render(){
  const f=$('#flt').value;
  $('#list').innerHTML=DATA.posts.filter(p=>!f||p.status===f).map(card).join('')||'<p class=mini>nothing here</p>';
}
function card(p){
  const pls=Object.keys(DATA.platforms);
  return `<div class=card id="c-${p.id}">
    <div class=hd onclick="document.getElementById('c-${p.id}').classList.toggle('open')">
      <span class="pill ${p.status}">${p.status}</span>
      <span class=t>${esc(p.title)}</span>
      ${p.score?`<span class=pill>score ${p.score}</span>`:''}
      <span class=pill>${p.origin}</span>
    </div>
    <div class=bd>
      <div class=row style="margin:10px 0">
        <label class=mini>status</label>
        <select onchange="save('${p.id}',{status:this.value})">
          ${DATA.statuses.map(s=>`<option ${s===p.status?'selected':''}>${s}</option>`).join('')}</select>
        <label class=mini>schedule</label>
        <input type=datetime-local value="${(p.scheduled_for||'').slice(0,16)}"
          onchange="save('${p.id}',{scheduled_for:this.value,status:'scheduled'})">
        ${p.url?`<a href="${p.url}" target=_blank class=mini>source ↗</a>`:''}
      </div>
      <div class=tabs id="tabs-${p.id}">
        ${pls.map((pl,i)=>`<button class="${i?'':'on'}" onclick="tab('${p.id}','${pl}')">${pl} · ${p.counts[pl]}/${DATA.platforms[pl]}</button>`).join('')}
      </div>
      <textarea id="ta-${p.id}">${esc(p.formats[pls[0]])}</textarea>
      <div class=row style="margin-top:8px">
        <button class=act onclick="copy('${p.id}')">Copy</button>
        <button class=ghost onclick="dl('${p.id}')">Download .txt</button>
        <button class=ghost onclick="saveEdit('${p.id}')">Save as edited base</button>
        <span class=mini id="msg-${p.id}"></span>
      </div>
    </div></div>`;
}
let CUR={};
function tab(id,pl){
  CUR[id]=pl;
  const p=DATA.posts.find(x=>x.id===id);
  document.querySelectorAll(`#tabs-${id} button`).forEach(b=>b.classList.toggle('on',b.textContent.startsWith(pl+' ')));
  $('#ta-'+id).value=p.formats[pl];
}
function copy(id){navigator.clipboard.writeText($('#ta-'+id).value);flash(id,'copied');}
function dl(id){const p=DATA.posts.find(x=>x.id===id);const pl=CUR[id]||Object.keys(DATA.platforms)[0];
  const blob=new Blob([$('#ta-'+id).value],{type:'text/plain'});const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);a.download=`${id}.${pl}.txt`;a.click();}
async function saveEdit(id){await save(id,{edited:$('#ta-'+id).value});flash(id,'saved — regenerating');loadAll();}
async function save(id,f){await post('/marketing/api/post/'+id,f);const p=DATA.posts.find(x=>x.id===id);if(p)Object.assign(p,f.status?{status:f.status}:{},f.scheduled_for?{scheduled_for:f.scheduled_for}:{});flash(id,'saved');}
function flash(id,m){const el=$('#msg-'+id);if(el){el.textContent=m;setTimeout(()=>el.textContent='',2000);}}
async function post(u,o){const b=new URLSearchParams();for(const k in o)b.append(k,o[k]);
  return fetch(u,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:b});}
async function exportBundle(){
  const d=await (await fetch('/marketing/api/bundle')).json();
  const blob=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='securitycyber-upload-pack-'+new Date().toISOString().slice(0,10)+'.json';a.click();
}
loadAll();
</script>
"""
