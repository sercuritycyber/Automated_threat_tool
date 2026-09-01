#!/usr/bin/env python3
"""Blue Team CTI — local control panel.

A single-file FastAPI app. Localhost only. No external assets, no database —
it reads/writes the same files the CLI pipeline uses:

  config/settings.yaml   config/feeds.yaml   config/env
  state/*.json           drafts/*.md         logs/run-*.log

Run:  uvicorn web.app:app --host 127.0.0.1 --port 8799
or:   python3 web/app.py
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config"
STATE = ROOT / "state"
DRAFTS = ROOT / "drafts"
LOGS = ROOT / "logs"
for d in (STATE, DRAFTS, LOGS):
    d.mkdir(exist_ok=True)

SECRET_KEYS = [
    ("LINKEDIN_ACCESS_TOKEN", "LinkedIn access token", "scope: w_member_social + openid profile"),
    ("LINKEDIN_AUTHOR_URN", "Author URN", "optional — urn:li:person:… or urn:li:organization:…"),
    ("LINKEDIN_CLIENT_ID", "OAuth client ID", "optional — enables auto token refresh"),
    ("LINKEDIN_CLIENT_SECRET", "OAuth client secret", "optional"),
    ("LINKEDIN_REFRESH_TOKEN", "OAuth refresh token", "optional"),
    ("NTFY_URL", "Notification URL", "optional — ntfy.sh-style topic for alerts"),
]

app = FastAPI(title="Blue Team CTI")


# ----------------------------------------------------------------------------- helpers
def load_yaml(p: Path) -> dict:
    try:
        return yaml.safe_load(p.read_text()) or {}
    except OSError:
        return {}


HEADERS = {
    "settings.yaml": "# Edited by the web UI — inline comments are not preserved.\n"
                     "# Field reference: README.md · Blue Team CTI control panel\n",
    "feeds.yaml": "# Edited by the web UI. weight 0-1 nudges the pre-score / breaks duplicate ties.\n",
}


def save_yaml(p: Path, data: dict) -> None:
    hdr = HEADERS.get(p.name, "")
    p.write_text(hdr + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def load_env() -> dict:
    out: dict[str, str] = {}
    f = CFG / "env"
    if f.exists():
        for ln in f.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def save_env(updates: dict[str, str]) -> None:
    f = CFG / "env"
    lines = f.read_text().splitlines() if f.exists() else []
    seen = set()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                lines[i] = f"{k}={updates[k]}"
                seen.add(k)
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    f.write_text("\n".join(lines) + "\n")
    os.chmod(f, 0o600)


def read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def mask(v: str) -> str:
    if not v:
        return ""
    return f"set · …{v[-4:]}" if len(v) > 8 else "set"


def rel_time(iso: str) -> str:
    if not iso:
        return "never"
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    s = (datetime.now(timezone.utc) - t).total_seconds()
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{int(s // n)}{unit} ago"
    return "just now"


def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


# ----------------------------------------------------------------------------- layout
CSS = """
:root{--bg:#0d1117;--panel:#161b22;--panel2:#1c2330;--line:#30363d;--fg:#e6edf3;
--dim:#8b949e;--accent:#3b82f6;--good:#3fb950;--warn:#d29922;--bad:#f85149;--radius:10px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{display:flex;min-height:100vh}
.side{width:210px;background:var(--panel);border-right:1px solid var(--line);padding:18px 0;flex-shrink:0}
.brand{font-weight:700;font-size:15px;padding:0 18px 14px;letter-spacing:.02em}
.brand small{display:block;color:var(--dim);font-weight:400;font-size:11px;margin-top:2px}
.nav a{display:block;padding:9px 18px;color:var(--fg);border-left:2px solid transparent}
.nav a:hover{background:var(--panel2);text-decoration:none}
.nav a.on{border-left-color:var(--accent);background:var(--panel2);color:#fff}
.main{flex:1;padding:26px 34px;max-width:1000px}
h1{font-size:20px;margin:0 0 18px}h2{font-size:15px;margin:26px 0 10px;color:var(--dim);
text-transform:uppercase;letter-spacing:.06em;font-weight:600}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
padding:16px 18px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:14px}
.stat .n{font-size:24px;font-weight:700}.stat .l{color:var(--dim);font-size:12px;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
input[type=text],input[type=number],input[type=password],textarea,select{
width:100%;background:var(--bg);border:1px solid var(--line);color:var(--fg);
border-radius:7px;padding:8px 10px;font:inherit}
textarea{min-height:120px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
label{display:block;margin:12px 0 4px;font-size:13px}label small{color:var(--dim)}
.btn{display:inline-block;background:var(--accent);color:#fff;border:0;border-radius:7px;
padding:8px 15px;font:inherit;font-weight:600;cursor:pointer}
.btn:hover{filter:brightness(1.1);text-decoration:none}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--fg)}
.btn.good{background:var(--good)}.btn.bad{background:var(--bad)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.pill{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600}
.pill.good{background:rgba(63,185,80,.15);color:var(--good)}
.pill.warn{background:rgba(210,153,34,.15);color:var(--warn)}
.pill.bad{background:rgba(248,81,73,.15);color:var(--bad)}
.pill.dim{background:var(--panel2);color:var(--dim)}
.flash{background:rgba(59,130,246,.12);border:1px solid var(--accent);border-radius:8px;
padding:10px 14px;margin-bottom:16px}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;
overflow:auto;font-size:12.5px;white-space:pre-wrap;word-break:break-word}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.dim{color:var(--dim)}
"""

NAV = [
    ("/", "Dashboard"),
    ("/review", "Review queue"),
    ("/candidates", "Candidates"),
    ("/drafts", "Drafts"),
    ("/settings", "Settings"),
    ("/feeds", "Feeds"),
    ("/secrets", "Secrets"),
    ("/logs", "Logs"),
]


def page(title: str, body: str, active: str = "", flash: str = "") -> HTMLResponse:
    nav = "".join(
        f'<a href="{p}" class="{"on" if p == active else ""}">{e(label)}</a>'
        for p, label in NAV
    )
    fl = f'<div class="flash">{e(flash)}</div>' if flash else ""
    return HTMLResponse(f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{e(title)} · Blue Team CTI</title><style>{CSS}</style></head><body>
<div class=wrap><div class=side>
<div class=brand>Blue Team CTI<small>securitycyber.uk</small></div>
<div class=nav>{nav}</div></div>
<div class=main><h1>{e(title)}</h1>{fl}{body}</div></div></body></html>""")


def flash_from(req: Request) -> str:
    return req.query_params.get("m", "")


# ----------------------------------------------------------------------------- dashboard
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    settings = load_yaml(CFG / "settings.yaml")
    pub = settings.get("publish", {})
    last = read_json(STATE / "last_run.json") or {}
    cand = read_json(STATE / "candidates.json") or {}
    counts = cand.get("counts", {})
    env = load_env()
    pending = read_json(STATE / "pending.json")

    dry = str(env.get("CTI_DRY_RUN", pub.get("dry_run", True))).lower() in ("1", "true", "yes", "on")
    mode = pub.get("mode", "auto")
    tok_ok = bool(env.get("LINKEDIN_ACCESS_TOKEN"))

    mode_pill = f'<span class="pill {"warn" if mode == "review" else "good"}">{e(mode)}</span>'
    dry_pill = ('<span class="pill bad">DRY-RUN — nothing posts</span>' if dry
                else '<span class="pill good">LIVE — will post</span>')
    tok_pill = ('<span class="pill good">token set</span>' if tok_ok
                else '<span class="pill warn">no LinkedIn token</span>')

    hist = []
    hp = STATE / "history.jsonl"
    if hp.exists():
        hist = [json.loads(x) for x in hp.read_text().splitlines() if x.strip()][-10:][::-1]
    hist_rows = "".join(
        f"<tr><td>{e(rel_time(h.get('posted_at','')))}</td>"
        f"<td>{'🧪 dry' if h.get('dry_run') else '✅ live'}</td>"
        f"<td>{e(h.get('score'))}</td>"
        f"<td><a href='{e(h.get('url'))}' target=_blank>{e((h.get('title') or '')[:80])}</a></td></tr>"
        for h in hist
    ) or "<tr><td colspan=4 class=dim>nothing posted yet</td></tr>"

    pending_banner = ""
    if pending:
        sel = pending.get("decision", {}).get("selected", {})
        pending_banner = (
            f'<div class="card" style="border-color:var(--warn)">'
            f'<b>A post is waiting for your approval</b> — score {e(sel.get("score"))}: '
            f'{e((sel.get("title") or "")[:90])} '
            f'<a class="btn good" style="margin-left:8px" href="/review">Review &amp; approve →</a></div>'
        )

    last_line = (f'{e(last.get("decision","—"))} · {e(rel_time(last.get("run_at","")))}'
                 + (f' · score {e(last.get("score"))}' if last.get("score") is not None else ""))

    body = f"""
{pending_banner}
<div class=card>
  <div class=row style="justify-content:space-between">
    <div class=row>{mode_pill} {dry_pill} {tok_pill}</div>
    <form method=post action=/run><button class=btn>▶ Run pipeline now</button></form>
  </div>
</div>

<div class=grid>
  <div class=stat><div class=n>{e(counts.get('candidates','—'))}</div><div class=l>candidates last run</div></div>
  <div class=stat><div class=n>{e(counts.get('clusters','—'))}</div><div class=l>story clusters</div></div>
  <div class=stat><div class=n>{e(counts.get('raw_items','—'))}</div><div class=l>raw feed items</div></div>
  <div class=stat><div class=n>{e(len(hist))}</div><div class=l>recent posts shown</div></div>
</div>

<h2>Last run</h2>
<div class=card>{last_line}
{('<br><span class=dim>' + e(last.get('reason','')) + '</span>') if last.get('reason') else ''}
<br><span class=dim>ingest generated {e(rel_time(cand.get('generated_at','')))}, lookback {e(cand.get('lookback_hours','?'))}h,
threshold {e(cand.get('min_score','?'))}</span></div>

<h2>Recent posts</h2>
<div class=card style="padding:0">
<table><tr><th>when</th><th>mode</th><th>score</th><th>headline</th></tr>{hist_rows}</table></div>
"""
    return page("Dashboard", body, "/", flash_from(request))


@app.post("/run")
def run_now():
    log = LOGS / f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-ui.log"
    with log.open("w") as fh:
        subprocess.Popen(["bash", str(ROOT / "bin" / "run.sh")],
                         cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True)
    return RedirectResponse("/?m=Pipeline+started+—+check+Logs+in+a+minute", 303)


# ----------------------------------------------------------------------------- review queue
@app.get("/review", response_class=HTMLResponse)
def review(request: Request):
    pending = read_json(STATE / "pending.json")
    if not pending:
        dec = read_json(STATE / "decision.json") or {}
        if dec.get("decision") == "post" and dec.get("selected"):
            pending = {"held_at": dec.get("run_at", ""), "decision": dec, "_from_decision": True}
    if not pending:
        return page("Review queue",
                    "<div class=card>Nothing waiting. Posts appear here when "
                    "<span class=mono>publish.mode = review</span> and the analyst clears the bar.</div>",
                    "/review", flash_from(request))

    d = pending["decision"]
    sel = d.get("selected", {})
    settings = load_yaml(CFG / "settings.yaml")
    dry = str(load_env().get("CTI_DRY_RUN", settings.get("publish", {}).get("dry_run", True))).lower() \
        in ("1", "true", "yes", "on")
    brk = "".join(f"<li>{e(b)}</li>" for b in sel.get("score_breakdown", []))
    considered = "".join(
        f"<tr><td>{e(c.get('score'))}</td><td><a href='{e(c.get('url'))}' target=_blank>"
        f"{e((c.get('title') or '')[:70])}</a></td><td class=dim>{e(c.get('note'))}</td></tr>"
        for c in d.get("considered", [])
    )
    warn = ('<div class="flash" style="border-color:var(--bad);background:rgba(248,81,73,.1)">'
            'Global <b>dry-run is ON</b> — approving will simulate only. Turn it off in '
            '<a href=/settings>Settings</a> to post for real.</div>') if dry else ""

    body = f"""
{warn}
<div class=card>
  <div class=row style="justify-content:space-between">
    <div><span class="pill good">score {e(sel.get('score'))}</span>
    <span class="pill dim">threshold {e(d.get('min_score'))}</span>
    <span class="pill dim">confidence {e(sel.get('confidence'))}</span></div>
    <span class=dim>held {e(rel_time(pending.get('held_at','')))}</span>
  </div>
  <p><b>{e(sel.get('title'))}</b><br>
  <a href="{e(sel.get('url'))}" target=_blank>{e(sel.get('source'))} ↗</a>
  · {e(', '.join(sel.get('cve_ids', []) or []))}</p>
  <details><summary class=dim>score breakdown</summary><ul>{brk}</ul></details>
</div>

<form method=post action=/review/approve>
<label>Post text <small>— edit freely; character count enforced on save</small></label>
<textarea name=post_text rows=16>{e(sel.get('post_text'))}</textarea>
<div class=row style="margin-top:12px">
  <button class="btn good" onclick="return confirm('Post this to LinkedIn now?')">✓ Approve &amp; post</button>
  <button class="btn ghost" formaction=/review/save formnovalidate>Save edits</button>
  <button class="btn bad" formaction=/review/reject formnovalidate
    onclick="return confirm('Discard this post?')">✗ Reject</button>
</div>
</form>

<h2>Also considered</h2>
<div class=card style="padding:0"><table>
<tr><th>score</th><th>headline</th><th>why it lost</th></tr>{considered or '<tr><td colspan=3 class=dim>—</td></tr>'}
</table></div>
"""
    return page("Review queue", body, "/review", flash_from(request))


def _write_pending_text(text: str) -> None:
    p = STATE / "pending.json"
    pending = read_json(p)
    if not pending:
        dec = read_json(STATE / "decision.json") or {}
        pending = {"held_at": datetime.now(timezone.utc).isoformat(), "decision": dec}
    pending["decision"].setdefault("selected", {})["post_text"] = text
    p.write_text(json.dumps(pending, indent=2))


@app.post("/review/save")
def review_save(post_text: str = Form("")):
    _write_pending_text(post_text.strip())
    return RedirectResponse("/review?m=Edits+saved", 303)


@app.post("/review/reject")
def review_reject():
    (STATE / "pending.json").unlink(missing_ok=True)
    return RedirectResponse("/review?m=Post+rejected+and+discarded", 303)


@app.post("/review/approve")
def review_approve(post_text: str = Form("")):
    _write_pending_text(post_text.strip())
    # publisher wants a bare decision doc at --approve PATH
    pending = read_json(STATE / "pending.json") or {}
    appr = STATE / "approved.json"
    appr.write_text(json.dumps(pending.get("decision", {}), indent=2))
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "publish_linkedin.py"), "--approve", str(appr)],
        cwd=ROOT, capture_output=True, text=True, env=_env_with_config(),
    )
    out = (proc.stdout + proc.stderr).strip()
    ok = proc.returncode == 0 and ("published:" in out or "DRY RUN" in out)
    if "published:" in out:
        (STATE / "pending.json").unlink(missing_ok=True)
    msg = ("Posted to LinkedIn" if "published:" in out
           else "Dry-run: simulated only" if "DRY RUN" in out
           else f"Publisher exited {proc.returncode}")
    return RedirectResponse(f"/review?m={msg.replace(' ', '+')}+·+{out.splitlines()[-1][:80].replace(' ', '+') if out else ''}", 303)


def _env_with_config() -> dict:
    env = dict(os.environ)
    env.update(load_env())
    return env


# ----------------------------------------------------------------------------- candidates
@app.get("/candidates", response_class=HTMLResponse)
def candidates(request: Request):
    c = read_json(STATE / "candidates.json")
    if not c:
        return page("Candidates", "<div class=card class=dim>No candidates file yet — run the pipeline.</div>",
                    "/candidates")
    rows = "".join(
        f"<tr><td><b>{e(round(x.get('pre_score',0)))}</b></td>"
        f"<td>{e(x.get('source'))}</td>"
        f"<td><a href='{e(x.get('url'))}' target=_blank>{e((x.get('title') or '')[:95])}</a>"
        f"{('<br><span class=dim>KEV: ' + e(', '.join(x.get('kev_cves',[]))) + '</span>') if x.get('kev_cves') else ''}</td>"
        f"<td class=dim>{e(', '.join(x.get('cve_ids', []) or []))}</td></tr>"
        for x in c.get("candidates", [])
    )
    body = f"""<div class=card>Generated {e(rel_time(c.get('generated_at','')))} ·
counts: {e(json.dumps(c.get('counts',{})))}</div>
<div class=card style="padding:0"><table>
<tr><th>pre-score</th><th>source</th><th>headline</th><th>CVEs</th></tr>{rows}</table></div>"""
    return page("Candidates", body, "/candidates", flash_from(request))


# ----------------------------------------------------------------------------- drafts
@app.get("/drafts", response_class=HTMLResponse)
def drafts(request: Request):
    files = sorted(DRAFTS.glob("*.md"), reverse=True)[:60]
    rows = "".join(
        f"<tr><td><a href='/draft?name={e(f.name)}'>{e(f.name)}</a></td>"
        f"<td class=dim>{e(rel_time(datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()))}</td></tr>"
        for f in files
    ) or "<tr><td class=dim>no drafts yet</td></tr>"
    return page("Drafts", f'<div class=card style="padding:0"><table>{rows}</table></div>',
                "/drafts", flash_from(request))


@app.get("/draft", response_class=HTMLResponse)
def draft(name: str):
    f = DRAFTS / Path(name).name
    if not f.exists():
        return page("Draft", "<div class=card>not found</div>", "/drafts")
    return page(name, f"<pre>{e(f.read_text())}</pre>", "/drafts")


# ----------------------------------------------------------------------------- settings
@app.get("/settings", response_class=HTMLResponse)
def settings_get(request: Request):
    s = load_yaml(CFG / "settings.yaml")
    b, ing, sc, pub, no = (s.get(k, {}) for k in ("brand", "ingest", "scoring", "publish", "notify"))
    timer = (ROOT / "systemd" / "cti-harvester.timer").read_text() if \
        (ROOT / "systemd" / "cti-harvester.timer").exists() else ""
    m = re.search(r"OnCalendar=(.+)", timer)
    oncal = m.group(1).strip() if m else "*-*-* 02,08,14,20:00:00"

    def ck(v):
        return "checked" if v else ""

    body = f"""<form method=post action=/settings>
<h2>Brand / voice</h2><div class=card>
<label>Name</label><input type=text name=brand_name value="{e(b.get('name',''))}">
<label>Site</label><input type=text name=brand_site value="{e(b.get('site',''))}">
<label>Audience <small>— shapes the analyst's tone</small></label>
<input type=text name=brand_audience value="{e(b.get('audience',''))}">
</div>

<h2>Ingest &amp; scoring</h2><div class=card>
<div class=grid>
<div><label>Lookback hours</label><input type=number name=lookback value="{e(ing.get('lookback_hours',12))}"></div>
<div><label>KEV recent days</label><input type=number name=kevdays value="{e(ing.get('kev_recent_days',21))}"></div>
<div><label>Max candidates</label><input type=number name=maxc value="{e(ing.get('max_candidates',40))}"></div>
<div><label>Min score to publish</label><input type=number name=minscore value="{e(sc.get('min_score',80))}"></div>
</div></div>

<h2>Publishing</h2><div class=card>
<label>Mode</label><select name=mode>
<option value=review {'selected' if pub.get('mode')=='review' else ''}>review — hold for my approval</option>
<option value=auto {'selected' if pub.get('mode')=='auto' else ''}>auto — post automatically</option>
</select>
<label style="margin-top:12px"><input type=checkbox name=dry_run {ck(pub.get('dry_run',True))}>
&nbsp;Dry-run (master safety switch — nothing is ever posted while this is on)</label>
<label>Max post characters</label><input type=number name=maxchars value="{e(pub.get('max_post_chars',2900))}">
<label>LinkedIn API version</label><input type=text name=apiver value="{e(pub.get('linkedin_api_version','202506'))}">
<label>Visibility</label><select name=visibility>
<option {'selected' if pub.get('visibility')=='PUBLIC' else ''}>PUBLIC</option>
<option {'selected' if pub.get('visibility')=='CONNECTIONS' else ''}>CONNECTIONS</option></select>
</div>

<h2>Schedule</h2><div class=card>
<label>systemd OnCalendar <small>— e.g. <span class=mono>*-*-* 02,08,14,20:00:00</span> = every 6h</small></label>
<input type=text name=oncal value="{e(oncal)}">
<p class=dim>After saving: <span class=mono>systemctl --user restart cti-harvester.timer</span></p>
</div>

<h2>Notifications</h2><div class=card>
<label>ntfy URL <small>— leave blank to disable</small></label>
<input type=text name=ntfy value="{e(no.get('ntfy_url',''))}">
<label style="margin-top:10px"><input type=checkbox name=on_success {ck(no.get('on_success',True))}>
&nbsp;also ping on a successful post</label>
</div>

<button class=btn>Save settings</button></form>"""
    return page("Settings", body, "/settings", flash_from(request))


@app.post("/settings")
async def settings_post(request: Request):
    f = await request.form()
    s = load_yaml(CFG / "settings.yaml")
    s.setdefault("brand", {}).update(
        name=f.get("brand_name", ""), site=f.get("brand_site", ""),
        audience=f.get("brand_audience", ""))
    s.setdefault("ingest", {}).update(
        lookback_hours=int(f.get("lookback", 12)),
        kev_recent_days=int(f.get("kevdays", 21)),
        max_candidates=int(f.get("maxc", 40)),
        feed_timeout_seconds=int(s.get("ingest", {}).get("feed_timeout_seconds", 20)))
    s.setdefault("scoring", {})["min_score"] = int(f.get("minscore", 80))
    s.setdefault("publish", {}).update(
        mode=f.get("mode", "review"),
        dry_run=bool(f.get("dry_run")),
        max_post_chars=int(f.get("maxchars", 2900)),
        linkedin_api_version=f.get("apiver", "202506"),
        visibility=f.get("visibility", "PUBLIC"))
    s.setdefault("notify", {}).update(
        ntfy_url=f.get("ntfy", ""), on_success=bool(f.get("on_success")))
    save_yaml(CFG / "settings.yaml", s)

    # schedule
    tf = ROOT / "systemd" / "cti-harvester.timer"
    if tf.exists() and f.get("oncal"):
        txt = re.sub(r"OnCalendar=.+", f"OnCalendar={f.get('oncal').strip()}", tf.read_text())
        tf.write_text(txt)
    return RedirectResponse("/settings?m=Saved", 303)


# ----------------------------------------------------------------------------- feeds
@app.get("/feeds", response_class=HTMLResponse)
def feeds_get(request: Request):
    feeds = load_yaml(CFG / "feeds.yaml").get("feeds", [])
    rows = ""
    for i, fd in enumerate(feeds):
        rows += f"""<tr>
<td><input type=text name=name_{i} value="{e(fd.get('name',''))}"></td>
<td><input type=text name=url_{i} value="{e(fd.get('url',''))}"></td>
<td style="width:80px"><input type=number step=0.05 min=0 max=1 name=weight_{i} value="{e(fd.get('weight',0.6))}"></td>
<td style="width:60px;text-align:center"><input type=checkbox name=on_{i} {'checked' if fd.get('enabled',True) else ''}></td>
<td style="width:50px;text-align:center"><input type=checkbox name=del_{i}></td>
</tr>"""
    body = f"""<form method=post action=/feeds>
<div class=card style="padding:0"><table>
<tr><th>name</th><th>url</th><th>weight</th><th>on</th><th>del</th></tr>{rows}
<tr>
<td><input type=text name=name_new placeholder="New source"></td>
<td><input type=text name=url_new placeholder="https://…/feed"></td>
<td><input type=number step=0.05 name=weight_new value="0.6"></td>
<td colspan=2></td></tr>
</table></div>
<div class=row><button class=btn>Save feeds</button>
<a class="btn ghost" href="/feeds/test">Test all feeds</a></div>
</form>
<p class=dim>Weight 0–1 nudges the pre-score and breaks ties between duplicate stories.</p>"""
    return page("Feeds", body, "/feeds", flash_from(request))


@app.post("/feeds")
async def feeds_post(request: Request):
    f = await request.form()
    idxs = sorted({int(k.split("_")[1]) for k in f if re.match(r"name_\d+$", k)})
    feeds = []
    for i in idxs:
        if f.get(f"del_{i}"):
            continue
        url = (f.get(f"url_{i}") or "").strip()
        if not url:
            continue
        feeds.append({
            "name": (f.get(f"name_{i}") or "").strip() or url,
            "url": url,
            "weight": float(f.get(f"weight_{i}") or 0.6),
            "enabled": bool(f.get(f"on_{i}")),
        })
    if (f.get("url_new") or "").strip():
        feeds.append({
            "name": (f.get("name_new") or "").strip() or f.get("url_new").strip(),
            "url": f.get("url_new").strip(),
            "weight": float(f.get("weight_new") or 0.6),
            "enabled": True,
        })
    save_yaml(CFG / "feeds.yaml", {"feeds": feeds})
    return RedirectResponse("/feeds?m=Saved", 303)


@app.get("/feeds/test", response_class=HTMLResponse)
def feeds_test(url: str = ""):
    sys.path.insert(0, str(ROOT / "lib"))
    import feedparse  # noqa
    hdr = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}
    targets = [{"name": "(single)", "url": url}] if url else load_yaml(CFG / "feeds.yaml").get("feeds", [])
    out = []
    for fd in targets:
        try:
            r = requests.get(fd["url"], headers=hdr, timeout=20)
            n = len(feedparse.parse(r.content))
            out.append((fd.get("name", fd["url"]), f"{r.status_code} · {n} items",
                        "good" if r.ok and n else "warn"))
        except Exception as ex:  # noqa
            out.append((fd.get("name", fd["url"]), str(ex)[:120], "bad"))
    rows = "".join(f"<tr><td>{e(a)}</td><td><span class='pill {c}'>{e(b)}</span></td></tr>" for a, b, c in out)
    return page("Feed test", f'<div class=card style="padding:0"><table>{rows}</table></div>'
                '<p><a href="/feeds">← back to feeds</a></p>', "/feeds")


# ----------------------------------------------------------------------------- secrets
@app.get("/secrets", response_class=HTMLResponse)
def secrets_get(request: Request):
    env = load_env()
    fields = ""
    for k, label, hint in SECRET_KEYS:
        cur = env.get(k, "")
        ph = mask(cur) or "not set"
        fields += f"""<label>{e(label)} <small>— {e(hint)}</small></label>
<input type=password name={k} placeholder="{e(ph)}" autocomplete=off>"""
    dry = env.get("CTI_DRY_RUN", "")
    body = f"""<div class=card class=dim>Values are write-only. Stored in
<span class=mono>config/env</span> (chmod 600, git-ignored). Leave a field blank to keep the current value.</div>
<form method=post action=/secrets>{fields}
<label style="margin-top:14px">CTI_DRY_RUN override <small>— blank = use settings.yaml</small></label>
<select name=CTI_DRY_RUN>
<option value="" {'selected' if dry=='' else ''}>(use settings.yaml)</option>
<option value=true {'selected' if dry=='true' else ''}>true — never post</option>
<option value=false {'selected' if dry=='false' else ''}>false — allow posting</option>
</select>
<div class=row style="margin-top:14px"><button class=btn>Save secrets</button>
<button class="btn ghost" formaction=/secrets/test formnovalidate>Test LinkedIn token</button></div>
</form>"""
    return page("Secrets", body, "/secrets", flash_from(request))


@app.post("/secrets")
async def secrets_post(request: Request):
    f = await request.form()
    updates = {}
    for k, _, _ in SECRET_KEYS:
        v = (f.get(k) or "").strip()
        if v:
            updates[k] = v
    cd = f.get("CTI_DRY_RUN", "")
    updates["CTI_DRY_RUN"] = cd if cd else load_env().get("CTI_DRY_RUN", "")
    if not updates["CTI_DRY_RUN"]:
        updates.pop("CTI_DRY_RUN")
    if updates:
        save_env(updates)
    return RedirectResponse("/secrets?m=Saved", 303)


@app.post("/secrets/test", response_class=HTMLResponse)
def secrets_test():
    env = load_env()
    tok = env.get("LINKEDIN_ACCESS_TOKEN")
    if not tok:
        return page("LinkedIn test", "<div class=card>No token set.</div>", "/secrets")
    try:
        r = requests.get("https://api.linkedin.com/v2/userinfo",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        if r.ok:
            j = r.json()
            msg = (f"OK — authenticated as <b>{e(j.get('name','?'))}</b><br>"
                   f"URN: <span class=mono>urn:li:person:{e(j.get('sub','?'))}</span>")
        else:
            msg = f"HTTP {r.status_code}: <span class=mono>{e(r.text[:300])}</span>"
    except Exception as ex:  # noqa
        msg = f"error: {e(str(ex))}"
    return page("LinkedIn test", f"<div class=card>{msg}</div><p><a href=/secrets>← back</a></p>", "/secrets")


# ----------------------------------------------------------------------------- logs
@app.get("/logs", response_class=HTMLResponse)
def logs_get(request: Request):
    files = sorted(LOGS.glob("run-*.log"), reverse=True)[:40]
    rows = "".join(
        f"<tr><td><a href='/log?name={e(f.name)}'>{e(f.name)}</a></td>"
        f"<td class=dim>{e(rel_time(datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()))}</td>"
        f"<td class=dim>{f.stat().st_size} B</td></tr>"
        for f in files
    ) or "<tr><td class=dim>no logs yet</td></tr>"
    latest = ""
    if files:
        latest = f"<h2>Latest — {e(files[0].name)}</h2><pre>{e(files[0].read_text()[-8000:])}</pre>"
    return page("Logs", f'<div class=card style="padding:0"><table>{rows}</table></div>{latest}',
                "/logs", flash_from(request))


@app.get("/log", response_class=PlainTextResponse)
def log_get(name: str):
    f = LOGS / Path(name).name
    return PlainTextResponse(f.read_text() if f.exists() else "not found")


# ----------------------------------------------------------------------------- main
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("CTI_UI_PORT", "8799"))
    print(f"Blue Team CTI panel → http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
