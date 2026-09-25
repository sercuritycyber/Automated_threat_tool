#!/usr/bin/env python3
"""Admin Center — the operator's live command surface.

pulse() aggregates every domain (pipeline, LinkedIn, subscribers, queue) into
one JSON snapshot; console_head() renders the interactive command bar + live
status strip + attention panel that sits at the top of the dashboard and
auto-refreshes.  One operator, one screen, actions inline.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _jsonl(p: Path):
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _rel(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        s = (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return "—"
    if s < 0:
        s = -s
        for u, n in (("d", 86400), ("h", 3600), ("m", 60)):
            if s >= n:
                return f"in {int(s // n)}{u}"
        return "soon"
    for u, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{int(s // n)}{u} ago"
    return "just now"


def _li_token_days(root: Path, creds: dict) -> dict:
    """Days until the LinkedIn access token expires. 5-minute file cache so the
    console poll doesn't hammer LinkedIn."""
    cache = root / "state" / "li_token.json"
    c = _read_json(cache) or {}
    if c.get("checked_at", 0) > time.time() - 300 and "expires_at" in c:
        exp = c["expires_at"]
    else:
        exp = None
        tok = creds.get("LINKEDIN_ACCESS_TOKEN")
        cid = creds.get("LINKEDIN_CLIENT_ID")
        sec = creds.get("LINKEDIN_CLIENT_SECRET")
        if tok and cid and sec:
            try:
                data = urllib.parse.urlencode(
                    {"token": tok, "client_id": cid, "client_secret": sec}).encode()
                r = urllib.request.urlopen(urllib.request.Request(
                    "https://www.linkedin.com/oauth/v2/introspectToken", data=data), timeout=8)
                j = json.load(r)
                exp = j.get("expires_at") if j.get("active") else None
            except Exception:
                exp = c.get("expires_at")
        try:
            cache.write_text(json.dumps({"checked_at": time.time(), "expires_at": exp}))
        except Exception:
            pass
    if not exp:
        return {"days": None, "expires": None}
    days = int((exp - time.time()) // 86400)
    return {"days": days, "expires": datetime.fromtimestamp(exp, timezone.utc).date().isoformat()}


def push_alert(post: dict, creds: dict) -> dict:
    """Fan a single operator-published post out to subscribers (RSS / email /
    Discord) via the Worker, so a manual publish never leaves subscribers behind
    the social feed. Non-fatal."""
    wu = (creds.get("CTI_WORKER_URL") or "").rstrip("/")
    sec = creds.get("CTI_INGEST_SECRET")
    if not (wu and sec):
        return {"ok": False, "error": "worker URL / ingest secret not set"}
    body = {"posts": [{k: post.get(k) for k in
                       ("title", "url", "source", "score", "cve_ids",
                        "subscriber_text", "action", "post_text", "short_text")
                       if post.get(k) not in (None, "")}]}
    try:
        req = urllib.request.Request(wu + "/internal/ingest",
                                     data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {sec}",
                                              "Content-Type": "application/json",
                                              "User-Agent": "Mozilla/5.0 (admin-center)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"ok": True, "resp": r.read().decode()[:120]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:150]}


def _worker(creds: dict, path: str):
    if not requests:
        return None
    wu = (creds.get("CTI_WORKER_URL") or "").rstrip("/")
    tok = creds.get("CTI_ADMIN_TOKEN")
    if not (wu and tok):
        return None
    try:
        r = requests.get(wu + path, headers={"Authorization": f"Bearer {tok}",
                         "User-Agent": "Mozilla/5.0 (admin-center)"}, timeout=8)
        return r.json()
    except Exception:
        return None


def pulse(root: Path, creds: dict, *, settings: dict | None = None,
          next_run: str = "", targets_ready: bool = True) -> dict:
    settings = settings or {}
    pub = settings.get("publish", {})
    last = _read_json(root / "state" / "last_run.json") or {}
    hist = _jsonl(root / "state" / "history.jsonl")
    now = time.time()

    dry = str((creds.get("CTI_DRY_RUN") or pub.get("dry_run", True))).lower() in ("1", "true", "yes", "on")
    mode = pub.get("mode", "auto")
    state = ("dry-run" if dry else "review" if mode == "review"
             else "live" if targets_ready else "no-network")

    def _posts_since(hrs):
        cut = now - hrs * 3600
        n = 0
        for h in hist:
            try:
                if datetime.fromisoformat(h.get("posted_at", "").replace("Z", "+00:00")).timestamp() >= cut:
                    n += 1
            except Exception:
                pass
        return n

    li = _li_token_days(root, creds)
    author = creds.get("LINKEDIN_AUTHOR_URN", "")
    li_target = ("company page" if author.startswith("urn:li:organization")
                 else "personal profile" if author.startswith("urn:li:person") else "—")

    q = _read_json(root / "state" / "pending.json") or {}
    queue = q.get("queue", []) if isinstance(q.get("queue"), list) else []

    subs = _worker(creds, "/admin/subscribers")
    slist = subs.get("subscribers", []) if isinstance(subs, dict) else []
    by_tier: dict[str, int] = {}
    for s in slist:
        by_tier[s.get("tier") or "?"] = by_tier.get(s.get("tier") or "?", 0) + 1

    # ---- attention: what the operator needs to act on ----
    attn = []
    if last.get("decision") == "error":
        attn.append({"level": "bad", "text": f"Last pipeline run errored: {str(last.get('error'))[:120]}",
                     "do": "Check Logs"})
    if li.get("days") is not None and li["days"] < 14:
        attn.append({"level": "warn" if li["days"] > 3 else "bad",
                     "text": f"LinkedIn token expires in {li['days']}d ({li['expires']})",
                     "do": "Refresh the token"})
    if not targets_ready:
        attn.append({"level": "bad", "text": "No publishing network is configured", "do": "Secrets"})
    for s in slist:
        if s.get("status") == "unconfirmed" and s.get("tier") in ("individual", "team"):
            attn.append({"level": "warn", "text": f"Paid subscriber never confirmed: {s.get('email')}",
                         "do": "Nudge or check the address"})
    if queue:
        attn.append({"level": "warn", "text": f"{len(queue)} post(s) waiting in the hold queue",
                     "do": "Review / approve"})
    man = root / "state" / "manual_post.md"
    if man.exists():
        age = _rel(datetime.fromtimestamp(man.stat().st_mtime, tz=timezone.utc).isoformat())
        attn.append({"level": "warn",
                     "text": f"Post(s) queued for manual publishing (delivery failed, {age} old)",
                     "do": "state/manual_post.md", "dismiss": "/api/manual_post/clear"})

    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "pipeline": {
            "state": state,
            "last_run": last.get("run_at", ""),
            "last_run_rel": _rel(last.get("run_at", "")),
            "last_decision": last.get("decision", "—"),
            "next_run": next_run,
            "next_run_rel": _rel(next_run) if next_run else "—",
            "candidates": (_read_json(root / "state" / "candidates.json") or {}).get("counts", {}).get("total"),
        },
        "linkedin": {
            "target": li_target,
            "posts_24h": _posts_since(24),
            "posts_7d": _posts_since(24 * 7),
            "last_post_rel": _rel(hist[-1]["posted_at"]) if hist else "never",
            "token_days": li.get("days"),
            "token_expires": li.get("expires"),
        },
        "audience": {
            "total": len(slist),
            "active": sum(1 for s in slist if s.get("status") == "active"),
            "unconfirmed": sum(1 for s in slist if s.get("status") == "unconfirmed"),
            "by_tier": by_tier,
            "sent_24h": (subs.get("sent24h") if isinstance(subs, dict) else None),
        },
        "queue": len(queue),
        "attention": attn,
        "recent": [
            {"when": _rel(h.get("posted_at", "")), "title": (h.get("title") or "")[:64],
             "score": h.get("score"), "url": h.get("post_url") or h.get("url"),
             "where": ", ".join(h.get("targets", [])) or "linkedin"}
            for h in hist[-6:][::-1]
        ],
    }


# --------------------------------------------------------------------------- head
def console_head() -> str:
    import re
    import panel_theme as PT
    return re.sub(r"@@(\w+)@@", lambda m: PT.icon(m.group(1)), _CONSOLE_HEAD)


_CONSOLE_HEAD = r"""
<style>
.ac{margin:-6px 0 22px}
.ac .bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.ac .bar button{background:var(--panel2);border:1px solid var(--line);color:var(--fg);
  border-radius:8px;padding:8px 13px;font:inherit;font-weight:600;cursor:pointer;display:flex;gap:7px;align-items:center}
.ac .bar button:hover{border-color:var(--accent)}
.ac .bar button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.ac .bar button:disabled{opacity:.5;cursor:default}
.ac .strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:14px}
.ac .cell{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 13px}
.ac .cell .n{font-size:18px;font-weight:700;line-height:1.15}
.ac .cell .l{color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;margin-top:3px}
.ac .cell.good{border-color:rgba(63,185,80,.45)} .ac .cell.warn{border-color:rgba(210,153,34,.5)}
.ac .cell.bad{border-color:rgba(248,81,73,.5)}
.ac .attn{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:6px 14px}
.ac .attn .row{padding:8px 0;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:baseline}
.ac .attn .row:last-child{border-bottom:0}
.ac .attn .dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.ac .dot.bad{background:var(--bad)} .ac .dot.warn{background:var(--warn)} .ac .dot.good{background:var(--good)}
.ac .attn .do{margin-left:auto;color:var(--dim);font-size:11.5px}
.ac .attn button.dismiss{font-size:11px;padding:3px 9px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--dim);cursor:pointer}
.ac .attn button.dismiss:hover{color:var(--fg);border-color:var(--dim)}
.ac .ok{color:var(--good);font-size:12.5px;padding:8px 0}
.ac #acToast{position:fixed;right:20px;bottom:20px;background:var(--panel2);border:1px solid var(--accent);
  border-radius:8px;padding:10px 16px;font-size:13px;display:none;z-index:50}
.ac .muted{color:var(--dim);font-size:11.5px}
</style>
<div class="ac" id="ac">
  <div class="bar">
    <button class="primary" data-a="/run" data-c="Run the pipeline now?">@@play@@ Run pipeline</button>
    <button id="acArm" data-a="/toggle-armed" data-c="Flip live / paused?">@@pause@@ <span>Live / pause</span></button>
    <button data-a="/action/approve-next" data-c="Approve &amp; post the next queued item?">@@check@@ Approve next</button>
    <form method="post" action="/fire-top" style="margin:0" onsubmit="return confirm('Draft the top-ranked candidate now and send it to the review queue?')"><button type="submit">@@zap@@ Fire top candidate</button></form>
    <button data-a="/action/sync-stripe-links">@@link@@ Sync Stripe links</button>
  </div>
  <div class="strip" id="acStrip"><div class="muted">loading live status…</div></div>
  <div class="attn" id="acAttn"></div>
</div>
<div id="acToast"></div>
<script>
(function(){
  const strip=document.getElementById('acStrip'),attn=document.getElementById('acAttn'),toast=document.getElementById('acToast');
  function t(msg){toast.textContent=msg;toast.style.display='block';setTimeout(()=>toast.style.display='none',3500);}
  function cell(l,n,cls){return `<div class="cell ${cls||''}"><div class="n">${n}</div><div class="l">${l}</div></div>`;}
  async function pulse(){
    let p; try{ p=await (await fetch('/api/pulse')).json(); }catch(e){ return; }
    const pl=p.pipeline,li=p.linkedin,au=p.audience;
    const stCls={'live':'good','dry-run':'warn','review':'','no-network':'bad'}[pl.state]||'';
    const arm=document.getElementById('acArm');
    if(arm){ const dry=pl.state==='dry-run';
      arm.querySelector('span').textContent=dry?'Go live':'Pause posting';
      arm.dataset.c=dry?'Switch to LIVE — cleared stories post for real?':'Pause posting (dry-run) — drafts only, nothing sent?'; }
    strip.innerHTML=[
      cell('pipeline',pl.state,stCls),
      cell('last run',pl.last_run_rel+' · '+pl.last_decision),
      cell('next run',pl.next_run_rel),
      cell('LinkedIn ('+li.target+')',li.posts_24h+' today / '+li.posts_7d+' wk'),
      cell('LI token',li.token_days==null?'—':(li.token_days+'d left'), li.token_days!=null&&li.token_days<14?(li.token_days<3?'bad':'warn'):''),
      cell('subscribers',au.active+' active'+(au.unconfirmed?' · '+au.unconfirmed+' pending':'')),
      cell('alerts sent 24h',au.sent_24h==null?'—':au.sent_24h),
      cell('hold queue',p.queue, p.queue?'warn':'')
    ].join('');
    if(!p.attention.length){ attn.innerHTML='<div class="ok">✓ Nothing needs your attention.</div>'; }
    else attn.innerHTML=p.attention.map(a=>
      `<div class="row"><span class="dot ${a.level}"></span><span>${a.text}</span><span class="do">${a.do||''}</span>`
      + (a.dismiss?`<button class="dismiss" data-dismiss="${a.dismiss}">Dismiss</button>`:'')
      + `</div>`).join('');
    attn.querySelectorAll('button[data-dismiss]').forEach(b=>b.addEventListener('click',async()=>{
      b.disabled=true;
      try{
        const r=await fetch(b.dataset.dismiss,{method:'POST'});
        let j={}; try{ j=await r.json(); }catch(e){}
        t(j.message||(r.ok?'dismissed':'failed'));
      }catch(e){ t('error: '+e); }
      pulse();
    }));
  }
  document.querySelectorAll('#ac .bar button[data-a]').forEach(b=>{
    b.addEventListener('click',async()=>{
      if(b.dataset.c && !confirm(b.dataset.c)) return;
      b.disabled=true;
      try{
        const r=await fetch(b.dataset.a,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'}});
        let j={}; try{ j=await r.json(); }catch(e){}
        t(j.message || j.detail || (r.ok?'done':'failed'));
      }catch(e){ t('error: '+e); }
      b.disabled=false; pulse();
    });
  });
  pulse(); setInterval(pulse,20000);
})();
</script>
"""
