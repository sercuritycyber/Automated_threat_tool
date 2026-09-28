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

import hashlib
import hmac
import html
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
import yaml
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config"
STATE = ROOT / "state"
DRAFTS = ROOT / "drafts"
LOGS = ROOT / "logs"
for d in (STATE, DRAFTS, LOGS):
    d.mkdir(exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import publishers  # noqa: E402
from analyst_codex import call_codex  # noqa: E402
import marketing_desk as MKT  # noqa: E402
import admin_center as AC  # noqa: E402
import cve_desk as CVE  # noqa: E402
import panel_theme as PT  # noqa: E402
try:
    import subscribers as SUBS  # noqa: E402
    import subsconfig           # noqa: E402
    _SUBS_OK = True
except Exception:  # noqa — the owner console must still boot without the subs stack
    _SUBS_OK = False

# Secrets page fields: every provider's keys, then the shared extras.
SECRET_KEYS: list[tuple[str, str, str]] = []
for _prov, _keys in publishers.PROVIDER_KEYS.items():
    SECRET_KEYS += _keys
SECRET_KEYS += [("NTFY_URL", "Notification URL", "optional — ntfy.sh-style topic for alerts")]
# Claude API bridge (bin/run.sh): paid fallback when the subscription is unavailable.
CLAUDE_KEYS = [("ANTHROPIC_API_KEY_BRIDGE", "Claude API key",
                "sk-ant-… from console.anthropic.com; used only when the subscription is down, 1 run/day"),
               ("BRIDGE_MAX_USD", "Daily spend cap (USD)", "blank = $5 per bridge run")]
# Backup analyst engines (bin/analyst_antigravity.py, bin/analyst_openai_compat.py).
# Their public posts are held for approval; the model id is pinned in settings.yaml.
BACKUP_AI_KEYS = [("GEMINI_API_KEY", "Gemini API key", "aistudio.google.com; first backup"),
                  ("OPENROUTER_API_KEY", "OpenRouter API key", "openrouter.ai/keys; one key, many models"),
                  ("OPENAI_API_KEY", "OpenAI API key", "platform.openai.com"),
                  ("MISTRAL_API_KEY", "Mistral API key", "console.mistral.ai")]
SECRET_KEYS += BACKUP_AI_KEYS
_AI_MODEL_LISTS = {"OPENROUTER_API_KEY": "https://openrouter.ai/api/v1/key",  # /models is public
                   "OPENAI_API_KEY": "https://api.openai.com/v1/models",
                   "MISTRAL_API_KEY": "https://api.mistral.ai/v1/models"}
SECRET_KEYS += CLAUDE_KEYS

# fields shown as plain text (not masked) so they can be checked / fixed
PLAINTEXT_KEYS = {"LINKEDIN_AUTHOR_URN", "MASTODON_BASE_URL", "BLUESKY_HANDLE", "BLUESKY_PDS_URL",
                  "EMAIL_SMTP_HOST", "EMAIL_SMTP_PORT", "EMAIL_SMTP_USER", "EMAIL_FROM", "EMAIL_TO",
                  "BRIDGE_MAX_USD"}
ALL_TARGETS = list(publishers.PROVIDERS)

app = FastAPI(title="SecurityCyber Admin Center")

# --------------------------------------------------------------------------- idle lock
# One operator (Charlie). Not internet-facing, but a passphrase + 10-minute
# idle lock keeps small hands out if he steps away. Set PANEL_PASSPHRASE in
# config/env; leave it blank to disable the lock entirely.
def _panel_pass() -> str:
    return (load_env().get("PANEL_PASSPHRASE") or os.environ.get("PANEL_PASSPHRASE") or "").strip()


def _panel_sign(ts: str) -> str:
    key = (load_env().get("ADMIN_TOKEN") or os.environ.get("ADMIN_TOKEN") or "sc-panel").encode()
    return hmac.new(key, ts.encode(), hashlib.sha256).hexdigest()[:24]


def _panel_unlocked(request: Request) -> bool:
    if not _panel_pass():
        return True
    raw = request.cookies.get("sc_lock", "")
    if raw.count(".") != 1:
        return False
    ts, sig = raw.split(".", 1)
    if not hmac.compare_digest(sig, _panel_sign(ts)):
        return False
    try:
        return (time.time() - float(ts)) <= PT.IDLE_SECS
    except ValueError:
        return False


def _set_lock_cookie(resp):
    ts = f"{time.time():.0f}"
    resp.set_cookie("sc_lock", f"{ts}.{_panel_sign(ts)}", max_age=PT.IDLE_SECS + 90,
                    httponly=True, samesite="lax", path="/")
    return resp


@app.middleware("http")
async def _idle_lock(request: Request, call_next):
    path = request.url.path
    is_open = path in ("/unlock", "/lock", "/favicon.ico") or path.startswith("/static")
    unlocked = _panel_unlocked(request)
    if path == "/api/pulse" and not unlocked:
        return JSONResponse({"locked": True}, status_code=401)
    if not is_open and not unlocked:
        nxt = path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse(f"/unlock?next={quote(nxt)}", 303)
    resp = await call_next(request)
    # slide the window on real activity; the 20s background poll must not
    if _panel_pass() and unlocked and path != "/api/pulse":
        _set_lock_cookie(resp)
    return resp


@app.get("/unlock", response_class=HTMLResponse)
def unlock_page(request: Request):
    if not _panel_pass():
        return RedirectResponse("/", 303)
    if _panel_unlocked(request):
        return RedirectResponse(request.query_params.get("next") or "/", 303)
    return HTMLResponse(PT.lock_screen(e(request.query_params.get("next") or "/"),
                                      request.query_params.get("e") == "1"))


@app.post("/unlock")
async def unlock_submit(request: Request):
    f = await request.form()
    nxt = str(f.get("next") or "/")
    if _panel_pass() and hmac.compare_digest(str(f.get("word") or "").strip(), _panel_pass()):
        return _set_lock_cookie(RedirectResponse(nxt if nxt.startswith("/") else "/", 303))
    return RedirectResponse(f"/unlock?next={quote(nxt)}&e=1", 303)


@app.post("/lock")
def lock_now():
    r = RedirectResponse("/unlock", 303)
    r.delete_cookie("sc_lock", path="/")
    return r




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


def save_env(updates: dict[str, str | None]) -> None:
    """value None  -> delete the key;  str -> set it."""
    f = CFG / "env"
    lines = f.read_text().splitlines() if f.exists() else []
    out, seen = [], set()
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                seen.add(k)
                if updates[k] is None:
                    continue                       # drop the line
                out.append(f"{k}={updates[k]}")
                continue
        out.append(ln)
    for k, v in updates.items():
        if k not in seen and v is not None:
            out.append(f"{k}={v}")
    f.write_text("\n".join(out) + "\n")
    os.chmod(f, 0o600)


def read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    return out


sys.path.insert(0, str(ROOT / "bin"))
try:
    from lint_post import lint as lint_post_text  # noqa
except ImportError:
    def lint_post_text(t, c=None):
        return {"ok": True, "errors": [], "warnings": [], "stats": {}}
try:
    from postlog import real_posts as _real_posts  # noqa
except ImportError:
    def _real_posts(hours=None):
        return []
try:
    from runlog import read as _runs_read  # noqa
except ImportError:
    def _runs_read(limit=40):
        return []


def next_run_iso() -> str:
    """Next scheduled harvest, from the timer's OnCalendar — '' if it can't tell."""
    tf = ROOT / "systemd" / "cti-harvester.timer"
    if not tf.exists():
        return ""
    m = re.search(r"OnCalendar=(.+)", tf.read_text())
    if not m:
        return ""
    try:
        out = subprocess.run(["systemd-analyze", "calendar", "--iterations=1", m.group(1).strip()],
                             capture_output=True, text=True, timeout=8).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    nm = re.search(r"\(in UTC\):\s*(.+)", out) or re.search(r"Next elapse:\s*(.+)", out)
    if not nm:
        return ""
    try:
        from email.utils import parsedate_to_datetime  # noqa
        dt = datetime.strptime(nm.group(1).strip(), "%a %Y-%m-%d %H:%M:%S %Z")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return ""


def day_key(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return ""


def real_history() -> list[dict]:
    """LinkedIn posts that actually went out (never dry-run sims)."""
    return [r for r in read_jsonl(STATE / "history.jsonl") if not r.get("dry_run")]


def posted_urls() -> set[str]:
    urls = {r.get("url") for r in real_history()}
    urls |= {r.get("url") for r in read_jsonl(STATE / "seen.jsonl")}
    return {u for u in urls if u}


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

/* --- dashboard --- */
.hero{display:flex;flex-wrap:wrap;gap:18px;align-items:center;justify-content:space-between;
background:linear-gradient(135deg,var(--panel),var(--panel2));border:1px solid var(--line);
border-radius:14px;padding:18px 22px;margin-bottom:16px}
.hero .state{font-size:19px;font-weight:700;display:flex;align-items:center;gap:10px}
.hero .sub{color:var(--dim);font-size:12.5px;margin-top:3px}
.beacon{width:11px;height:11px;border-radius:50%;flex-shrink:0;box-shadow:0 0 0 0 currentColor;
animation:pulse 2.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 currentColor}70%{box-shadow:0 0 0 9px transparent}100%{box-shadow:0 0 0 0 transparent}}
.count{font-variant-numeric:tabular-nums;font-weight:700}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px;margin-bottom:16px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .n{font-size:21px;font-weight:700;line-height:1.1}
.tile .l{color:var(--dim);font-size:11px;margin-top:3px;text-transform:uppercase;letter-spacing:.04em}
.tile.good{border-color:rgba(63,185,80,.4)}.tile.warn{border-color:rgba(210,153,34,.45)}
.tile.bad{border-color:rgba(248,81,73,.45)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.bars{display:flex;align-items:flex-end;gap:5px;height:64px;padding-top:4px}
.bars .b{flex:1;background:var(--panel2);border-radius:3px 3px 0 0;min-height:3px;position:relative}
.bars .b.has{background:var(--accent)}
.bars .b span{position:absolute;top:-16px;left:0;right:0;text-align:center;font-size:10px;color:var(--dim)}
.slots{display:flex;gap:6px;flex-wrap:wrap}
.slot{padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600;border:1px solid var(--line);color:var(--dim)}
.slot.past{opacity:.45}.slot.next{border-color:var(--accent);color:#fff;background:rgba(59,130,246,.18)}
.tl{list-style:none;margin:0;padding:0}
.tl li{display:flex;gap:10px;padding:7px 0;border-bottom:1px solid var(--line);font-size:12.5px;align-items:baseline}
.tl li:last-child{border-bottom:0}
.tl .t{color:var(--dim);white-space:nowrap;font-size:11.5px;min-width:64px}
"""

NAV = [
    ("Operate", [("/", "Operations", "gauge"), ("/review", "Review queue", "inbox"),
                 ("/compose", "Compose", "pen"), ("/candidates", "Candidates", "list"),
                 ("/intel", "Intel Desk", "radar"), ("/drafts", "Drafts", "file")]),
    ("Grow", [("/marketing", "Marketing", "megaphone")]),
    ("Customers", [("/service", "CTI service", "shield"), ("/subscribers", "Subscribers", "users"),
                   ("/purchases", "Purchases", "card")]),
    ("Configure", [("/settings", "Settings", "sliders"), ("/feeds", "Feeds", "rss"),
                   ("/secrets", "Secrets", "key")]),
    ("System", [("/d1", "D1 tables", "database"), ("/logs", "Logs", "terminal")]),
]


def page(title: str, body: str, active: str = "", flash: str = "") -> HTMLResponse:
    nav = "".join(
        f'<div class=grp>{e(group)}</div>' + "".join(
            f'<a href="{p}" class="{"on" if p == active else ""}">{PT.icon(ic)}<span>{e(label)}</span></a>'
            for p, label, ic in items)
        for group, items in NAV
    )
    fl = f'<div class="flash">{e(flash)}</div>' if flash else ""
    return HTMLResponse(PT.shell(e(title), body, nav, fl, bool(_panel_pass()), datetime.now()))


def flash_from(req: Request) -> str:
    return req.query_params.get("m", "")


# ----------------------------------------------------------------------------- publishing helpers
def _creds() -> dict:
    d = dict(os.environ)
    d.update(load_env())
    return d


def configured_targets() -> list[str]:
    return publishers.configured(_creds())


def settings_targets() -> list[str]:
    s = load_yaml(CFG / "settings.yaml")
    return list(s.get("publish", {}).get("targets") or ["linkedin"])


def target_checkboxes(name: str, selected: list[str], only_ready: bool = False) -> str:
    ready = set(configured_targets())
    out = []
    for t in ALL_TARGETS:
        lab = publishers.PROVIDER_LABELS.get(t, t)
        dis = " disabled" if (only_ready and t not in ready) else ""
        note = "" if t in ready else " <span class=dim>(no creds)</span>"
        chk = " checked" if t in selected else ""
        out.append(f'<label style="display:inline-flex;gap:5px;align-items:center;margin:0 14px 0 0;font-size:13px">'
                   f'<input type=checkbox name={name} value="{t}"{chk}{dis}> {e(lab)}{note}</label>')
    return "".join(out)


def queue_load() -> dict:
    """pending.json in the new shape {held_at, queue:[...], considered:[...]}.
    Migrates the old single-post shape on read."""
    p = read_json(STATE / "pending.json")
    if not p:
        return {}
    if isinstance(p.get("queue"), list):
        return p
    dec = p.get("decision") or {}
    posts = dec.get("posts") or ([dec["selected"]] if isinstance(dec.get("selected"), dict) else [])
    return {"held_at": p.get("held_at", ""), "manual": p.get("manual", False),
            "queue": [dict(x, min_score=dec.get("min_score", 65),
                           held_because=("manual" if p.get("manual") else "review")) for x in posts],
            "considered": dec.get("considered", [])}


def queue_save(q: dict) -> None:
    (STATE / "pending.json").write_text(json.dumps(q, indent=2))


def queue_append(items: list[dict], manual: bool = False, considered=None) -> int:
    q = queue_load() or {"held_at": datetime.now(timezone.utc).isoformat(), "queue": [], "considered": []}
    have = {x.get("url") for x in q["queue"] if x.get("url")}
    added = 0
    for it in items:
        if it.get("url") and it["url"] in have:
            continue
        q["queue"].append(it)
        added += 1
    if manual:
        q["manual"] = True
    if considered:
        q["considered"] = considered
    q["held_at"] = datetime.now(timezone.utc).isoformat()
    queue_save(q)
    return added


# ----------------------------------------------------------------------------- dashboard
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    settings = load_yaml(CFG / "settings.yaml")
    pub = settings.get("publish", {})
    last = read_json(STATE / "last_run.json") or {}
    cand = read_json(STATE / "candidates.json") or {}
    counts = cand.get("counts", {})
    env = load_env()

    posted = posted_urls()
    q = _prune_queue(queue_load()) if queue_load() else {}
    queue_n = len(q.get("queue", [])) if q else 0

    dry = str(env.get("CTI_DRY_RUN", pub.get("dry_run", True))).lower() in ("1", "true", "yes", "on")
    mode = pub.get("mode", "auto")
    ready = configured_targets()
    tgts = [t for t in (pub.get("targets") or ["linkedin"]) if t in ready]
    tok_ok = bool(ready)

    max_day = int(pub.get("max_per_day", 5))
    sev = int(pub.get("severity_override_score", 90))
    min_score = int(settings.get("scoring", {}).get("min_score", 65))

    all_real = _real_posts()
    used_day = len(_real_posts(24))
    week = len(_real_posts(24 * 7))
    scores7 = [r["score"] for r in _real_posts(24 * 7) if isinstance(r.get("score"), (int, float))]
    avg7 = round(sum(scores7) / len(scores7)) if scores7 else "—"
    last_post_when = rel_time(all_real[-1]["posted_at"]) if all_real else "never"
    cap_cls = "bad" if used_day >= max_day else ("warn" if used_day >= max_day - 1 else "good")

    hist = real_history()[-8:][::-1]
    dry_n = len(read_jsonl(STATE / "dry_runs.jsonl"))
    hist_rows = "".join(
        f"<tr><td>{e(rel_time(h.get('posted_at','')))}</td>"
        f"<td><b>{e(h.get('score'))}</b></td>"
        f"<td><a href='{e(h.get('url'))}' target=_blank>{e((h.get('title') or '')[:70])}</a>"
        f"<br><span class=dim>{e(h.get('source',''))}"
        f"{(' · ' + e(', '.join(h.get('cve_ids', [])))) if h.get('cve_ids') else ''}</span></td>"
        f"<td>{('<a target=_blank href=' + e(h.get('post_url')) + '>view ↗</a>') if h.get('post_url') else '<span class=dim>—</span>'}"
        f"{(' <span class=dim>' + e(', '.join(h.get('targets', []))) + '</span>') if h.get('targets') else ''}</td></tr>"
        for h in hist
    ) or "<tr><td colspan=4 class=dim>nothing has been posted for real yet</td></tr>"

    tnames = ", ".join(publishers.PROVIDER_LABELS.get(t, t) for t in tgts) or "—"

    # ---- hero state ----
    if not ready:
        st_txt, st_col = "NO NETWORK CONFIGURED", "var(--bad)"
        st_sub = "Add credentials for LinkedIn, Mastodon, Bluesky, X or email in Secrets. The pipeline still runs and drafts."
    elif dry:
        st_txt, st_col = "DRY-RUN — drafting only", "var(--warn)"
        st_sub = "Stories are drafted and archived but never sent. Hit “Go live” when you're ready."
    elif mode == "review":
        st_txt, st_col = "ARMED — holding for approval", "var(--accent)"
        st_sub = f"Every story that clears the bar waits in the review queue. Posts go to: {tnames}."
    else:
        st_txt, st_col = "LIVE — posting automatically", "var(--good)"
        st_sub = (f"Auto-posts stories scoring ≥ {min_score} to {tnames}, up to {max_day}/day. "
                  f"A story ≥ {sev}, or a big day, posts past the cap.")

    nxt = next_run_iso()
    target_ms = 0
    if nxt:
        try:
            target_ms = int(datetime.fromisoformat(nxt).timestamp() * 1000)
        except ValueError:
            target_ms = 0
    nxt_abs = ""
    if nxt:
        try:
            nxt_abs = "≈ " + datetime.fromisoformat(nxt).astimezone().strftime("%a %H:%M") + " (+ jitter)"
        except ValueError:
            nxt_abs = ""

    # ---- today's run slots ----
    slots_html = ""
    tfile = ROOT / "systemd" / "cti-harvester.timer"
    mh = re.search(r"OnCalendar=\S+\s+([\d,]+):", tfile.read_text()) if tfile.exists() else None
    if mh:
        hours = sorted({int(x) for x in mh.group(1).split(",") if x.isdigit()})
        now_h = datetime.now().astimezone().hour
        future = [h for h in hours if h > now_h]
        nxt_slot = future[0] if future else None
        for h in hours:
            cls = "next" if h == nxt_slot else ("past" if h <= now_h else "")
            slots_html += f'<span class="slot {cls}">{h:02d}:00</span>'

    # ---- 14-day activity ----
    bycount = Counter(day_key(r["posted_at"]) for r in all_real if r.get("posted_at"))
    days = [(datetime.now().astimezone() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    mx = max([bycount[d] for d in days] + [1])
    bars_html = "".join(
        f'<div class="b {"has" if bycount[d] else ""}" title="{d}: {bycount[d]} post(s)" '
        f'style="height:{max(3, round(bycount[d] / mx * 58))}px">'
        + (f'<span>{bycount[d]}</span>' if bycount[d] else '') + '</div>'
        for d in days
    )

    # ---- run history timeline ----
    def _rbadge(r: dict) -> str:
        if r.get("published"):
            return '<span class="pill good">posted</span>'
        if r.get("error"):
            return '<span class="pill bad">failed</span>'
        if r.get("decision") == "pending_review":
            return '<span class="pill warn">held</span>'
        if r.get("dry_run") and r.get("decision") == "post":
            return '<span class="pill warn">dry-run</span>'
        return '<span class="pill dim">skip</span>'

    runs = _runs_read(14)
    runs_html = "".join(
        f'<li><span class=t>{e(rel_time(r.get("ts", "")))}</span>{_rbadge(r)}'
        f'<span>{e((r.get("title") or r.get("note") or "—")[:78])}</span>'
        f'<span class=dim style="margin-left:auto">{e(r.get("trigger", ""))}</span></li>'
        for r in runs
    ) or '<li class=dim>no runs recorded yet — they show up here after the next harvest</li>'

    # ---- "what happened last" card ----
    lr_d, when = last.get("decision", "—"), rel_time(last.get("run_at", ""))
    n_posted, n_held, n_fail = last.get("posted", 0), last.get("held", 0), last.get("failed", 0)
    if last.get("published"):
        pu = last.get("post_url", "")
        extra = (f" · {n_held} held" if n_held else "") + (f" · {n_fail} failed" if n_fail else "")
        card = (f'<span class="pill good">POSTED</span> {e(n_posted or 1)} item(s) {e(when)} '
                f'to {e(", ".join(last.get("targets", [])) or "the network")}{e(extra)} — {e(last.get("title",""))}'
                + (f' &nbsp;<a class=btn href="{e(pu)}" target=_blank>View ↗</a>' if pu else ''))
        cbord = "var(--good)"
    elif last.get("dry_run"):
        card = (f'<span class="pill warn">DRY-RUN</span> {e(when)} — {e(last.get("simulated", 1))} post(s) '
                f'cleared the bar but <b>nothing was sent</b>. Turn off dry-run in <a href=/settings>Settings</a>.')
        cbord = "var(--warn)"
    elif last.get("error"):
        card = f'<span class="pill bad">FAILED</span> {e(when)} — <span class=mono>{e(last.get("error"))}</span>'
        cbord = "var(--bad)"
    elif lr_d == "pending_review":
        card = (f'<span class="pill warn">HELD</span> {e(when)} — {e(last.get("note") or last.get("title",""))} '
                f'<a class=btn good href=/review>Review →</a>')
        cbord = "var(--warn)"
    elif lr_d == "skip":
        card = f'<span class="pill dim">skipped</span> {e(when)} — <span class=dim>{e(last.get("reason",""))}</span>'
        cbord = "var(--line)"
    else:
        card = f'{e(lr_d)} · {e(when)}'
        cbord = "var(--line)"

    pending_banner = ""
    if queue_n:
        head = q["queue"][0]
        pending_banner = (
            f'<div class="card" style="border-color:var(--warn)">'
            f'<b>{queue_n} post(s) waiting for your approval</b> — '
            f'“{e((head.get("title") or "")[:80])}”{(" +" + str(queue_n - 1) + " more") if queue_n > 1 else ""} '
            f'<a class="btn good" style="margin-left:8px" href="/review">Review &amp; approve →</a></div>'
        )

    cand_n = counts.get("candidates", 0) or 0

    subs_card = ""
    wbase = _worker()[0]
    try:
        if wbase:
            wst = worker_api("GET", "/admin/stats")
            pd = (wst.get("by_status") or {}).get("past_due", 0)
            subs_card = (
                f'<div class=card><div class=row style="justify-content:space-between">'
                f'<div><b>Paid alert service (Worker)</b> · {wst.get("total_active", 0)} active · '
                f'£{(wst.get("mrr_pence", 0) or 0) / 100:,.0f}/mo est · {wst.get("sent_24h", 0)} sent in 24h'
                + (f' · <span class="pill bad">{pd} past due</span>' if pd else '') + '</div>'
                f'<a class="btn ghost" href="/subscribers">Subscribers →</a></div></div>')
        elif _SUBS_OK:
            scfg = subsconfig.load()
            sst = SUBS.stats()
            if scfg.get("enabled") or sst["by_status"]:
                pd = sst["by_status"].get("past_due", 0)
                on = "on" if scfg.get("enabled") else "OFF"
                subs_card = (
                    f'<div class=card><div class=row style="justify-content:space-between">'
                    f'<div><b>Paid alert service: {on}</b> · {sst["total_active"]} active · '
                    f'£{subs_mrr_pence() / 100:,.0f}/mo est · {sst["sent_24h"]} sent in 24h'
                    + (f' · <span class="pill bad">{pd} past due</span>' if pd else '') + '</div>'
                    f'<a class="btn ghost" href="/subscribers">Subscribers →</a></div></div>')
    except Exception:  # noqa
        subs_card = ""
    script = (
        "<script>(function(){var T=" + str(target_ms) + ";"
        "var el=document.getElementById('cd');"
        "function f(){if(!el){return;}if(!T){el.textContent='—';return;}"
        "var s=Math.max(0,Math.round((T-Date.now())/1000));"
        "var h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=s%60;"
        "el.textContent=h>0?(h+'h '+m+'m'):(m>0?(m+'m '+x+'s'):(x+'s'));}"
        "f();setInterval(f,1000);setTimeout(function(){location.reload();},60000);})();</script>"
    )

    body = f"""
{pending_banner}
<div class=hero>
  <div>
    <div class=state><span class=beacon style="color:{st_col};background:{st_col}"></span>{e(st_txt)}</div>
    <div class=sub>{st_sub}</div>
  </div>
  <div style="text-align:right">
    <div class=sub>next automated run</div>
    <div class=state style="justify-content:flex-end"><span class=count id=cd>—</span></div>
    <div class=sub>{e(nxt_abs) or 'schedule unknown'}</div>
  </div>
</div>

<div class=tiles>
  <div class="tile {cap_cls}"><div class=n>{used_day}<span class=dim style="font-size:14px">/{max_day}</span></div><div class=l>posts today · cap</div></div>
  <div class=tile><div class=n>{avg7}</div><div class=l>avg score · 7d</div></div>
  <div class=tile><div class=n>{cand_n}</div><div class=l>candidates</div></div>
  <div class="tile {'bad' if not ready else 'good'}"><div class=n style="font-size:15px;padding-top:4px">{e(', '.join(publishers.PROVIDER_LABELS.get(t,t) for t in tgts)) or 'none'}</div><div class=l>posting to</div></div>
</div>

<div class=card>
  <div class=row style="justify-content:space-between">
    <span class=dim style="font-size:12px">Today's schedule</span>
    <div class=row style="gap:6px">{slots_html or '<span class=dim>no schedule</span>'}</div>
  </div>
</div>
{subs_card}

<h2>What happened last</h2>
<div class=card style="border-color:{cbord}">{card}
<br><span class=dim>ingest {e(rel_time(cand.get('generated_at','')))} · lookback {e(cand.get('lookback_hours','?'))}h
· threshold {e(cand.get('min_score','?'))} · {e(counts.get('candidates','—'))} candidates / {e(counts.get('clusters','—'))} clusters</span></div>

<h2>Activity · last 14 days</h2>
<div class=card><div class=bars>{bars_html}</div></div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start">
<div>
<h2>Recent posts {('· ' + str(dry_n) + ' sims hidden') if dry_n else ''}</h2>
<div class=card style="padding:0">
<table><tr><th>when</th><th>score</th><th>story</th><th></th></tr>{hist_rows}</table></div>
</div>
<div>
<h2>Run history</h2>
<div class=card><ul class=tl>{runs_html}</ul>
<div class=row style="margin-top:10px">
<a class="btn ghost" href="/logs">Full logs →</a>
<form method=post action=/reset-test onsubmit="return confirm('Clear pending post + sim history + this run\\'s candidates/decision? Real posted history is kept.')">
<button class="btn ghost">Clear test data</button></form>
</div></div>
</div>
</div>
{script}
"""
    return page("Operations", AC.console_head() + body, "/", flash_from(request))


@app.post("/reset-test")
def reset_test():
    for f in ("pending.json", "approved.json", "decision.json", "candidates.json", "dry_runs.jsonl"):
        (STATE / f).unlink(missing_ok=True)
    return RedirectResponse("/?m=Test+data+cleared+(real+post+history+kept)", 303)


@app.post("/run")
def run_now():
    log = LOGS / f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-ui.log"
    env = _env_with_config()
    env["CTI_TRIGGER"] = "ui"
    with log.open("w") as fh:
        subprocess.Popen(["bash", str(ROOT / "bin" / "run.sh")],
                         cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True, env=env)
    return RedirectResponse("/?m=Pipeline+started+—+refreshing+shortly", 303)


@app.post("/toggle-armed")
def toggle_armed():
    s = load_yaml(CFG / "settings.yaml")
    env = load_env()
    # the env override wins over settings.yaml, so clear it and flip the file
    now_dry = str(env.get("CTI_DRY_RUN", s.get("publish", {}).get("dry_run", True))).lower() \
        in ("1", "true", "yes", "on")
    s.setdefault("publish", {})["dry_run"] = not now_dry
    save_yaml(CFG / "settings.yaml", s)
    if "CTI_DRY_RUN" in env:
        save_env({"CTI_DRY_RUN": None})
    msg = "Now LIVE — the next cleared story posts for real." if now_dry \
        else "Paused — dry-run only, nothing will be sent."
    return RedirectResponse("/?m=" + quote(msg), 303)


@app.post("/fire-top")
def fire_top():
    c = read_json(STATE / "candidates.json") or {}
    posted = posted_urls()
    pool = [x for x in c.get("candidates", []) if x.get("url") and x["url"] not in posted]
    if not pool:
        return RedirectResponse("/?m=" + quote("No fresh candidates to fire — run the pipeline first"), 303)
    top = max(pool, key=lambda x: x.get("pre_score", 0))
    it, err = _analyst_draft_one(top)
    if err:
        return RedirectResponse("/?m=" + quote("Fire failed — " + err), 303)
    queue_append([it], manual=True)
    return RedirectResponse("/review?m=" + quote(
        f"Drafted “{(top.get('title') or '')[:50]}” — review and approve below."), 303)


# ----------------------------------------------------------------------------- review queue
def _prune_queue(q: dict) -> dict:
    """drop items already posted; delete pending.json if empty."""
    posted = posted_urls()
    q["queue"] = [x for x in q.get("queue", []) if x.get("url") not in posted]
    if not q["queue"]:
        (STATE / "pending.json").unlink(missing_ok=True)
    else:
        queue_save(q)
    return q


@app.get("/review", response_class=HTMLResponse)
def review(request: Request):
    q = _prune_queue(queue_load()) if queue_load() else {}
    settings = load_yaml(CFG / "settings.yaml")
    dry = str(load_env().get("CTI_DRY_RUN", settings.get("publish", {}).get("dry_run", True))).lower() \
        in ("1", "true", "yes", "on")
    deftargets = settings_targets()

    items = q.get("queue", []) if q else []
    if not items:
        return page("Review queue",
                    "<div class=card>Nothing waiting. Posts land here when "
                    "<span class=mono>publish.mode = review</span>, when the daily cap holds an extra, "
                    "when a draft fails the quality check, or when you fire one from Candidates. "
                    "You can also write one on the <a href=/compose>Compose</a> page.</div>",
                    "/review", flash_from(request))

    lr = read_json(STATE / "last_run.json") or {}
    prev = ""
    if lr.get("error"):
        prev = (f'<div class="flash" style="border-color:var(--bad)">Last publish attempt reported: '
                f'<span class=mono>{e(lr.get("error"))}</span></div>')
    elif lr.get("dry_run"):
        prev = ('<div class="flash" style="border-color:var(--warn)">Last run was a <b>DRY-RUN</b> — '
                'nothing was sent. Turn off dry-run in <a href=/settings>Settings</a> to post for real.</div>')
    warn = ('<div class="flash" style="border-color:var(--bad);background:rgba(248,81,73,.1)">'
            'Global <b>dry-run is ON</b> — approving still sends nothing. Turn it off in '
            '<a href=/settings>Settings</a> first.</div>') if dry else ""

    reason_label = {"review": "review mode", "cap": "over the daily cap", "quality": "failed quality check",
                    "manual": "you fired this", "runaway": "runaway guard", "compose": "you wrote this"}
    blocks = []
    for i, it in enumerate(items):
        ptext = it.get("post_text", "") or ""
        ql = lint_post_text(ptext, it.get("cve_ids") or [])
        st = ql.get("stats", {})
        qbits = "".join(f'<li><span class="pill bad">fix</span> {e(x)}</li>' for x in ql["errors"])
        qbits += "".join(f'<li><span class="pill warn">nit</span> {e(x)}</li>' for x in ql["warnings"])
        qhtml = ('<span class="pill good">quality ✓</span>' if not qbits
                 else f'<ul style="margin:6px 0 0;padding-left:18px">{qbits}</ul>')
        why = reason_label.get(it.get("held_because", "review"), it.get("held_because", ""))
        tsel = it.get("targets") or deftargets
        fold = 215
        cut = ptext.rfind(" ", 0, fold) if len(ptext) > fold else len(ptext)
        blocks.append(f"""
<div class=card>
  <div class=row style="justify-content:space-between">
    <div><span class="pill {'good' if (it.get('score') or 0) >= 90 else 'dim'}">score {e(it.get('score','—'))}</span>
    <span class="pill dim">held: {e(why)}</span>
    <span class="pill dim">{e(it.get('confidence','') or '')}</span></div>
    <span class=dim>#{i + 1} of {len(items)}</span>
  </div>
  <p style="margin:8px 0 4px"><b>{e(it.get('title',''))}</b>
  {(' &nbsp;<a href="' + e(it.get('url')) + '" target=_blank>source ↗</a>') if it.get('url') else ''}
  <span class=dim>{e(', '.join(it.get('cve_ids', []) or []))}</span></p>
  <div style="font-size:12.5px;margin-bottom:6px">{qhtml}
  <span class=dim>· {st.get('chars','?')} chars · hook {st.get('hook_chars','?')} · {st.get('hashtags','?')} tags</span></div>
  <div class=card style="background:var(--bg);white-space:pre-wrap;max-width:560px;font-size:13px;margin:0 0 10px">{e(ptext[:cut])}<span class=dim> …more</span>{e(ptext[cut:])}</div>
  <form method=post action=/review/act>
    <input type=hidden name=idx value="{i}">
    <textarea name=post_text rows=14>{e(ptext)}</textarea>
    <label style="margin-top:8px">Short version <small>— for X / Bluesky (≤ 260 chars)</small></label>
    <input type=text name=short_text value="{e(it.get('short_text',''))}">
    <div style="margin:8px 0"><b class=dim style="font-size:12px">POST TO:</b><br>{target_checkboxes('targets', tsel, only_ready=True)}</div>
    <div class=row>
      <button class="btn good" name=do value=approve onclick="return confirm('Post this now to the ticked networks?')">✓ Approve &amp; post</button>
      <button class="btn ghost" name=do value=save formnovalidate>Save edits</button>
      <button class="btn ghost" name=do value=rewrite formnovalidate onclick="return confirm('Ask the editor to tighten this? ~30s.')">↻ Rewrite</button>
      <button class="btn bad" name=do value=reject formnovalidate onclick="return confirm('Discard this one?')">✗ Reject</button>
    </div>
  </form>
</div>""")

    considered = "".join(
        f"<tr><td>{e(c.get('score'))}</td><td><a href='{e(c.get('url'))}' target=_blank>"
        f"{e((c.get('title') or '')[:70])}</a></td><td class=dim>{e(c.get('note'))}</td></tr>"
        for c in q.get("considered", [])
    )
    allbtn = (f'<form method=post action=/review/approve-all onsubmit="return confirm(\'Post all '
              f'{len(items)} queued items now to their ticked networks?\')">'
              f'<button class="btn good">✓ Approve &amp; post all {len(items)}</button></form>'
              if len(items) > 1 else "")

    body = f"""{prev}{warn}
<div class=row style="justify-content:space-between;margin-bottom:12px">
  <div>{len(items)} post(s) waiting · held {e(rel_time(q.get('held_at','')))}</div>
  <div class=row>{allbtn}
  <form method=post action=/review/reject-all onsubmit="return confirm('Discard ALL queued posts?')">
  <button class="btn ghost">Clear queue</button></form></div>
</div>
{''.join(blocks)}
{('<h2>Also considered this run</h2><div class=card style="padding:0"><table><tr><th>score</th><th>headline</th><th>why it lost</th></tr>' + considered + '</table></div>') if considered else ''}
"""
    return page("Review queue", body, "/review", flash_from(request))


def _run_publish_approved(posts: list[dict]) -> tuple[str, bool]:
    """write approved.json and shell the publisher; returns (message, published)."""
    (STATE / "approved.json").write_text(json.dumps({"posts": posts}, indent=2))
    proc = subprocess.run([sys.executable, str(ROOT / "bin" / "publish_linkedin.py"),
                           "--approve", str(STATE / "approved.json")],
                          cwd=ROOT, capture_output=True, text=True, env=_env_with_config())
    (STATE / "approved.json").unlink(missing_ok=True)
    lr = read_json(STATE / "last_run.json") or {}
    if lr.get("published"):
        n = lr.get("posted", 1)
        tg = ", ".join(lr.get("targets", [])) or "the network"
        return f"✓ Posted {n} item(s) to {tg}", True
    if lr.get("dry_run"):
        return "Dry-run only — nothing sent. Turn off dry-run in Settings, then approve again.", False
    if lr.get("error"):
        return f"Failed: {lr['error'][:200]}", False
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    return (tail[-1][:200] if tail else f"publisher exited {proc.returncode}"), False


@app.post("/review/act")
def review_act(idx: int = Form(0), do: str = Form("save"),
               post_text: str = Form(""), short_text: str = Form(""),
               targets: list[str] = Form(default=[])):
    q = queue_load()
    if not q or idx >= len(q.get("queue", [])):
        return RedirectResponse("/review?m=Item+not+found", 303)
    it = q["queue"][idx]
    it["post_text"] = post_text.strip()
    it["short_text"] = short_text.strip()
    if targets:
        it["targets"] = targets

    if do == "reject":
        q["queue"].pop(idx)
        if q["queue"]:
            queue_save(q)
        else:
            (STATE / "pending.json").unlink(missing_ok=True)
        return RedirectResponse("/review?m=Rejected", 303)

    if do == "save":
        queue_save(q)
        return RedirectResponse("/review?m=Saved", 303)

    if do == "rewrite":
        queue_save(q)
        new = _rewrite_text(it["post_text"])
        if new:
            it["post_text"] = new
            queue_save(q)
            return RedirectResponse("/review?m=Rewritten+—+check+it", 303)
        return RedirectResponse("/review?m=Rewrite+failed+—+kept+the+original", 303)

    # approve
    queue_save(q)
    msg, published = _run_publish_approved([dict(it, targets=it.get("targets") or targets or None)])
    if published:
        _prune_queue(queue_load() or {})
    return RedirectResponse(("/" if published else "/review") + "?m=" + quote(msg), 303)


@app.post("/review/approve-all")
def review_approve_all():
    q = queue_load()
    items = q.get("queue", []) if q else []
    if not items:
        return RedirectResponse("/review?m=Nothing+to+post", 303)
    msg, published = _run_publish_approved([dict(it, targets=it.get("targets") or None) for it in items])
    if published:
        _prune_queue(queue_load() or {})
    return RedirectResponse(("/" if published else "/review") + "?m=" + quote(msg), 303)


@app.post("/review/reject-all")
def review_reject_all():
    (STATE / "pending.json").unlink(missing_ok=True)
    return RedirectResponse("/review?m=Queue+cleared", 303)


REWRITE_PROMPT = """You are the social editor for SecurityCyber (securitycyber.uk),
an independent UK blue-team advisory. Rewrite the security post below to a
professional standard. Keep every fact, CVE id and version number exactly as
given - do not add facts.

Structure (blank line between every block):

   <hook: ONE sentence, HARD LIMIT 140 chars, active voice, second person. Lead
   with what goes wrong if the reader does nothing. Never open with the patch
   ("Critical flaws have been patched" is banned) and never with a vendor name.>

   <one sentence of why it lands on the reader's desk today>

   The quick breakdown:

   . What: <flaw class or activity, with CVE/CVSS if there is one>
   . Impact: <what the attacker actually gets>
   . Affected: <exact versions, and the fixed version>
   . Do this: <the patch, plus the stop-gap if patching is slow>

   What to look for: <1-2 plain-English lines. NOT a detection query, NOT IOCs.>

   <GAP LINE: one factual line naming what the subscriber brief on this story
   carries that this post does not - the detection rule, the IOC set, the
   runbook, the lead time. Never promise something the brief does not contain.>

   <PLUG: one plain line ending with the bare domain cti.securitycyber.uk>

   #CyberSecurity #ThreatIntel <+1 topical>

Hard rules:
- NO "https://" URL anywhere in the body. A link in the body gets the post's
  reach suppressed. The bare domain cti.securitycyber.uk is the only route -
  it is not auto-linked, so it costs nothing.
- Never write "link in the comments": our token cannot create one.
- EXACTLY 3 hashtags, all on the last line, each a complete real tag.
  #CyberSecurity and #ThreatIntel always, plus one topical (#BlueTeam,
  #VulnerabilityManagement, #SysAdmin, #MSP, #ICS). Three reach further than five.
- Each bullet is one idea under 160 chars. Every sentence under 25 words.
  Active voice, British spelling, second person. No emoji, no markdown.
- Banned: "in today's threat landscape", "attackers are increasingly", "threat
  actors are increasingly", "now more than ever", "less than a coffee",
  "enterprise-grade", "peace of mind", "Thoughts?", "Agree?", and any
  like/share/comment ask.
- 600-1600 characters total.

Output ONLY the rewritten post text, nothing else.

--- CURRENT POST ---
%s
"""


def _rewrite_text(cur: str) -> str:
    """Ask the configured analyst to tighten a post. Returns "" on any failure."""
    cur = (cur or "").strip()
    if not cur:
        return ""
    try:
        if (load_yaml(CFG / "settings.yaml").get("analyst") or {}).get("engine") == "codex":
            new, _ = call_codex(REWRITE_PROMPT % cur, timeout=120, web=False)
        else:
            proc = subprocess.run(["claude", "-p", REWRITE_PROMPT % cur, "--allowedTools", "",
                                   "--settings", str(ROOT / "config" / "claude-untrusted.json")],
                                   cwd=ROOT, capture_output=True, text=True, timeout=120,
                                   env=_env_with_config())
            new = proc.stdout.strip() if proc.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError, RuntimeError, ValueError):
        return ""
    # The new format carries no "https://" by design, so the old
    # `"http" in new` guard would reject every good rewrite. Accept on
    # length plus either the route domain or the breakdown block.
    ok = len(new) >= 200 and ("securitycyber.uk" in new or "breakdown" in new.lower())
    return new if ok else ""


DRAFT_ONE_PROMPT = """You are the CTI analyst and social editor for SecurityCyber (securitycyber.uk).
The operator has hand-picked ONE story to publish now. Verify it, then draft the post.

STORY
title:     %(title)s
url:       %(url)s
source:    %(source)s
published: %(published)s
CVEs:      %(cves)s
KEV:       %(kev)s
summary:   %(summary)s

STEPS
1. WebFetch the url and read the article. Confirm CVE id(s), CVSS, affected
   products/versions, exploitation status (in-the-wild / PoC only / theoretical),
   and the fix or mitigation. If WebFetch fails, work from the summary above and
   set confidence "low".
2. Score it 0-100 honestly: +40 confirmed exploitation or zero-day, +25 recent
   CISA KEV (+12 older KEV), +15 CVSS>=9 or vendor Critical, +15 widely deployed
   or critical infrastructure, +15 supply-chain / MSP / identity blast radius,
   +10 public PoC or named ransomware use, +5 clear patch exists. Penalties: -40
   vendor marketing or roundup, -20 unconfirmed. Cap at 100.
3. Pick a lane: "breaking" (live emergency - CVSS 9+ with confirmed
   exploitation, KEV, or a vendor patch-now advisory), "intel" (a real
   vulnerability, campaign, breach or TTP a defender can act on - the usual
   case), or "trend" (one pattern across several stories, not one story).

4. Draft the LinkedIn post. Plain text, 600-1600 chars, blank line between
   every block:

      <hook: ONE sentence, HARD LIMIT 140 chars, active voice, second person.
      Lead with what goes wrong if the reader does nothing. Never open with the
      patch, never with a vendor name, never with meta-commentary.>

      <one sentence of why it lands on the reader's desk today>

      The quick breakdown:

      . What: <flaw class, with CVE and CVSS if there is one>
      . Impact: <what the attacker actually gets>
      . Affected: <products + version ranges, and the fixed version>
      . Do this: <the one patch/mitigation action, plus the stop-gap>

      What to look for: <1-2 plain-English lines a defender can act on. NOT a
      detection query, NOT an IOC list - those stay paid-only.>

      <GAP LINE: one factual line naming what the subscriber brief on this
      story carries that this post does not. Be specific and true.>

      <PLUG: one plain line ending with the bare domain cti.securitycyber.uk>

      #CyberSecurity #ThreatIntel <+1 topical>

   NO "https://" URL anywhere in the body - it suppresses the post's reach. The
   bare domain cti.securitycyber.uk is the route, and it is not auto-linked.
   Never write "link in the comments": our token cannot create one.
   EXACTLY 3 complete hashtags on the last line (#CyberSecurity and
   #ThreatIntel always, plus one topical). Three reach further than five.
   Every sentence <=25 words, active voice, British spelling, second person.
   At most one em-dash per sentence. No emoji, no markdown. Facts ONLY from the
   source - never invent a CVE, CVSS, version, IOC or attribution. Attribute
   exploitation and PoC claims ("Huntress reports...", "CISA added...").
   Banned: "In today's threat landscape", "attackers are increasingly", "threat
   actors are increasingly", "Now more than ever", "Thoughts?", "Agree?", and
   any ask to like/share/comment.

   Also write a `short_text`: one or two sentences, <= 260 characters, ending
   with https://cti.securitycyber.uk - this goes to X / Bluesky, which do not
   penalise links.

OUTPUT
Print ONLY this JSON on one line - no code fence, no commentary:
{"title":"...","url":"...","source":"...","published":"...","cve_ids":["..."],"score":<int>,"score_breakdown":["+40 ...","..."],"confidence":"high|medium|low","lane":"breaking|intel|trend","post_text":"<full post, real \\n newlines, NO https:// in it>","short_text":"<= 260 chars, ends with https://cti.securitycyber.uk"}
"""


def _analyst_draft_one(cand: dict) -> tuple[dict | None, str]:
    """Hand one operator-picked candidate to the configured analyst; returns (post_item, "")."""
    prompt = DRAFT_ONE_PROMPT % {
        "title": cand.get("title", ""),
        "url": cand.get("url", ""),
        "source": cand.get("source", ""),
        "published": cand.get("published", ""),
        "cves": ", ".join(cand.get("cve_ids", []) or []) or "none listed",
        "kev": ", ".join(cand.get("kev_cves", []) or []) or "none",
        "summary": (cand.get("summary", "") or "")[:1200],
    }
    try:
        if (load_yaml(CFG / "settings.yaml").get("analyst") or {}).get("engine") == "codex":
            raw, _ = call_codex(prompt, timeout=240)
        else:
            proc = subprocess.run(
                ["claude", "-p", prompt, "--allowedTools", "WebFetch",
                 "--settings", str(ROOT / "config" / "claude-untrusted.json")],
                cwd=ROOT, capture_output=True, text=True, timeout=240, env=_env_with_config())
            if proc.returncode:
                return None, f"analyst exited {proc.returncode}"
            raw = proc.stdout
    except (subprocess.TimeoutExpired, OSError, RuntimeError, ValueError) as ex:
        return None, f"analyst call failed: {str(ex)[:160]}"
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None, "analyst returned no JSON"
    try:
        it = json.loads(m.group(0))
    except json.JSONDecodeError as ex:
        return None, f"analyst JSON was malformed: {str(ex)[:120]}"
    it = it.get("selected", it) if isinstance(it.get("selected"), dict) else it
    if not (it.get("post_text") or "").strip():
        return None, "analyst produced no post_text"
    it.setdefault("title", cand.get("title", ""))
    it.setdefault("url", cand.get("url", ""))
    it.setdefault("source", cand.get("source", ""))
    it.setdefault("published", cand.get("published", ""))
    it.setdefault("cve_ids", cand.get("cve_ids", []) or [])
    it.setdefault("confidence", "medium")
    it.setdefault("score", 0)
    it.setdefault("score_breakdown", [])
    it["held_because"] = "manual"
    it["min_score"] = int(load_yaml(CFG / "settings.yaml").get("scoring", {}).get("min_score", 65))
    return it, ""


@app.post("/candidates/fire")
def candidates_fire(url: str = Form("")):
    url = url.strip()
    c = read_json(STATE / "candidates.json") or {}
    cand = next((x for x in c.get("candidates", []) if x.get("url") == url), None)
    if not cand:
        return RedirectResponse("/candidates?m=" + quote("Couldn't find that candidate — re-run ingest first"), 303)
    if url in posted_urls():
        return RedirectResponse("/candidates?m=" + quote("That story has already been posted"), 303)
    it, err = _analyst_draft_one(cand)
    if err:
        return RedirectResponse("/candidates?m=" + quote("Manual fire failed — " + err), 303)
    queue_append([it], manual=True)
    return RedirectResponse("/review?m=" + quote(
        "Drafted from your pick — edit / pick targets / approve below. Manual posts ignore the daily cap."), 303)


def _env_with_config() -> dict:
    env = dict(os.environ)
    env.update(load_env())
    return env


# ----------------------------------------------------------------------------- compose
@app.get("/compose", response_class=HTMLResponse)
def compose_get(request: Request):
    dry = str(load_env().get("CTI_DRY_RUN", load_yaml(CFG / "settings.yaml")
                             .get("publish", {}).get("dry_run", True))).lower() in ("1", "true", "yes", "on")
    ready = configured_targets()
    warn = ('<div class="flash" style="border-color:var(--warn)">Dry-run is ON — '
            '“Post now” will simulate only. Turn it off in <a href=/settings>Settings</a>.</div>') if dry else ""
    nocreds = "" if ready else ('<div class="flash" style="border-color:var(--bad)">No network has '
                                'credentials yet — add them in <a href=/secrets>Secrets</a>.</div>')
    body = f"""{warn}{nocreds}
<div class=card>Write anything and send it now, or drop it in the review queue. This bypasses the
analyst, the schedule, the score threshold and the daily cap — it's your channel.</div>
<form method=post action=/compose>
<label>Post text</label>
<textarea name=post_text rows=16 placeholder="If you run an internet-facing …, patch to … today: …&#10;&#10;• Vulnerability: CVE-…&#10;• Affected: …&#10;• Do now: …&#10;&#10;https://…&#10;&#10;#BlueTeam #CyberSecurity"></textarea>
<label>Short version <small>— optional, for X / Bluesky (≤ 260 chars). Left blank, the long post is auto-trimmed.</small></label>
<input type=text name=short_text>
<label>Title <small>— for your history log only</small></label>
<input type=text name=title placeholder="manual post">
<div style="margin:12px 0"><b class=dim style="font-size:12px">POST TO:</b><br>
{target_checkboxes('targets', settings_targets(), only_ready=True)}</div>
<div class=row>
  <button class="btn good" name=do value=now onclick="return confirm('Send this now to the ticked networks?')">Post now</button>
  <button class="btn ghost" name=do value=hold formnovalidate>Hold for review</button>
</div>
</form>"""
    return page("Compose", body, "/compose", flash_from(request))


@app.post("/compose")
def compose_post(post_text: str = Form(""), short_text: str = Form(""), title: str = Form(""),
                 do: str = Form("hold"), targets: list[str] = Form(default=[])):
    text = post_text.strip()
    if len(text) < 20:
        return RedirectResponse("/compose?m=" + quote("Post text is too short"), 303)
    item = {"title": title.strip() or "manual post", "url": "", "source": "compose",
            "cve_ids": re.findall(r"CVE-\d{4}-\d{4,7}", text), "score": 100, "confidence": "operator",
            "post_text": text, "short_text": short_text.strip(), "held_because": "compose",
            "targets": targets or None}
    if do == "hold":
        queue_append([item], manual=True)
        return RedirectResponse("/review?m=" + quote("Added to the review queue"), 303)
    msg, published = _run_publish_approved([item])
    return RedirectResponse(("/" if published else "/compose") + "?m=" + quote(msg), 303)


# ----------------------------------------------------------------------------- candidates
@app.get("/candidates", response_class=HTMLResponse)
def candidates(request: Request):
    c = read_json(STATE / "candidates.json")
    if not c:
        return page("Candidates", "<div class=card class=dim>No candidates file yet — run the pipeline.</div>",
                    "/candidates")
    posted = posted_urls()

    def _row(x):
        u = x.get("url", "")
        kev = ('<br><span class=dim>KEV: ' + e(', '.join(x.get('kev_cves', []))) + '</span>') \
            if x.get('kev_cves') else ''
        if u in posted:
            act = '<span class=dim>posted ✓</span>'
        else:
            act = (
                f'<form method=post action=/candidates/fire '
                f'onsubmit="return confirm(\'Draft a post for this story now and send it to the '
                f'review queue? A manual fire ignores the daily cap and the schedule.\')">'
                f'<input type=hidden name=url value="{e(u)}">'
                f'<button class="btn ghost" style="padding:4px 10px;font-size:12px">Draft &amp; review →</button>'
                f'</form>'
            )
        return (
            f"<tr><td><b>{e(round(x.get('pre_score',0)))}</b></td>"
            f"<td>{e(x.get('source'))}</td>"
            f"<td><a href='{e(u)}' target=_blank>{e((x.get('title') or '')[:95])}</a>{kev}</td>"
            f"<td class=dim>{e(', '.join(x.get('cve_ids', []) or []))}</td>"
            f"<td>{act}</td></tr>"
        )

    rows = "".join(_row(x) for x in c.get("candidates", []))
    body = f"""<div class=card>Generated {e(rel_time(c.get('generated_at','')))} ·
counts: {e(json.dumps(c.get('counts',{})))}
<br><span class=dim>Hit <b>Draft &amp; review</b> on any story to push it into the review queue right now —
manual fires skip the schedule and the daily post cap. You still approve the draft before it goes live.</span></div>
<div class=card style="padding:0"><table>
<tr><th>pre-score</th><th>source</th><th>headline</th><th>CVEs</th><th>fire</th></tr>{rows}</table></div>"""
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

    engine = (s.get("analyst") or {}).get("engine", "claude")
    engine_options = "".join(f'<option value="{x}" {"selected" if engine == x else ""}>{label}</option>'
                             for x, label in (("codex", "Codex — ChatGPT subscription"),
                                              ("claude", "Claude — legacy engine"),
                                              ("antigravity", "Gemini — backup engine")))
    body = f"""<form method=post action=/settings>
<h2>Analyst</h2><div class=card><label>Engine</label>
<select name=analyst_engine>{engine_options}</select>
<small>Codex uses the VM's ChatGPT login. If unavailable, the scheduled run uses
subscriber-only deterministic fallback; it does not spend Claude API credits.</small></div>
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
<div><label>Min score to publish <small>— 65 = solid signal, 80 = KEV-only</small></label><input type=number name=minscore value="{e(sc.get('min_score',65))}"></div>
<div><label>Max posts per day <small>— rolling 24h; extras are held</small></label><input type=number name=maxday value="{e(pub.get('max_per_day',5))}"></div>
<div><label>Severity override score <small>— a story scoring ≥ this posts even past the daily cap</small></label><input type=number name=sevoverride value="{e(pub.get('severity_override_score',90))}"></div>
</div></div>

<h2>Publishing</h2><div class=card>
<label>Mode</label><select name=mode>
<option value=review {'selected' if pub.get('mode')=='review' else ''}>review — hold for my approval</option>
<option value=auto {'selected' if pub.get('mode')=='auto' else ''}>auto — post automatically</option>
</select>
<label style="margin-top:12px"><input type=checkbox name=dry_run {ck(pub.get('dry_run',True))}>
&nbsp;Dry-run (master safety switch — nothing is ever posted while this is on)</label>
<label style="margin-top:12px">Post to <small>— every enabled network with credentials gets each post</small></label>
<div>{target_checkboxes('targets', list(pub.get('targets') or ['linkedin']))}</div>
<label>Max post characters</label><input type=number name=maxchars value="{e(pub.get('max_post_chars',2900))}">
<label>Runaway guard <small>— never auto-post more than this many in one run</small></label>
<input type=number name=runaway value="{e(pub.get('runaway_guard',20))}">
</div>

<h2>Per-network</h2><div class=card>
<label>LinkedIn API version</label><input type=text name=apiver value="{e(pub.get('linkedin_api_version','202509'))}">
<label>LinkedIn visibility</label><select name=visibility>
<option {'selected' if pub.get('visibility')=='PUBLIC' else ''}>PUBLIC</option>
<option {'selected' if pub.get('visibility')=='CONNECTIONS' else ''}>CONNECTIONS</option></select>
<label>Mastodon visibility</label><select name=mastovis>
{''.join(f'<option {"selected" if pub.get("mastodon_visibility","public")==v else ""}>{v}</option>' for v in ('public','unlisted','private'))}
</select>
<label>Email subject prefix</label><input type=text name=emailprefix value="{e(pub.get('email_subject_prefix','[Blue Team CTI]'))}">
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
    engine = f.get("analyst_engine")
    if engine in {"codex", "claude", "antigravity"}:
        s.setdefault("analyst", {})["engine"] = engine
    s.setdefault("brand", {}).update(
        name=f.get("brand_name", ""), site=f.get("brand_site", ""),
        audience=f.get("brand_audience", ""))
    s.setdefault("ingest", {}).update(
        lookback_hours=int(f.get("lookback", 12)),
        kev_recent_days=int(f.get("kevdays", 21)),
        max_candidates=int(f.get("maxc", 40)),
        feed_timeout_seconds=int(s.get("ingest", {}).get("feed_timeout_seconds", 20)))
    s.setdefault("scoring", {})["min_score"] = int(f.get("minscore", 65))
    tgts = [t for t in f.getlist("targets") if t in publishers.PROVIDERS] or ["linkedin"]
    s.setdefault("publish", {}).update(
        mode=f.get("mode", "review"),
        dry_run=bool(f.get("dry_run")),
        targets=tgts,
        max_per_day=int(f.get("maxday", 5)),
        severity_override_score=int(f.get("sevoverride", 90)),
        runaway_guard=int(f.get("runaway", 20)),
        max_post_chars=int(f.get("maxchars", 2900)),
        linkedin_api_version=f.get("apiver", "202509"),
        visibility=f.get("visibility", "PUBLIC"),
        mastodon_visibility=f.get("mastovis", "public"),
        email_subject_prefix=f.get("emailprefix", "[Blue Team CTI]"))
    s.setdefault("notify", {}).update(
        ntfy_url=f.get("ntfy", ""), on_success=bool(f.get("on_success")))
    save_yaml(CFG / "settings.yaml", s)

    # schedule — only write a spec systemd can actually parse
    tf = ROOT / "systemd" / "cti-harvester.timer"
    oncal = (f.get("oncal") or "").strip()
    if tf.exists() and oncal:
        cur = re.search(r"OnCalendar=(.+)", tf.read_text())
        if not cur or cur.group(1).strip() != oncal:
            try:
                ok = subprocess.run(["systemd-analyze", "calendar", oncal],
                                    capture_output=True, timeout=10).returncode == 0
            except (FileNotFoundError, subprocess.TimeoutExpired):
                ok = True  # can't validate here — trust the operator
            if not ok:
                return RedirectResponse(
                    "/settings?m=" + quote(f"Saved — but the schedule '{oncal}' is not a valid "
                                           "systemd OnCalendar spec, so it was left unchanged."), 303)
            tf.write_text(re.sub(r"OnCalendar=.+", f"OnCalendar={oncal}", tf.read_text()))
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
def _secret_field(k: str, label: str, hint: str, env: dict) -> str:
    cur = env.get(k, "")
    plain = k in PLAINTEXT_KEYS
    typ = "text" if plain else "password"
    val = f'value="{e(cur)}"' if plain else ""
    ph = "" if plain else f'placeholder="{e(mask(cur) or "not set")}"'
    hh = f' <small>— {e(hint)}</small>' if hint else ""
    return (f'<label style="margin-top:10px">{e(label)}{hh}</label>'
            f'<input type={typ} name={k} {val} {ph} autocomplete=off>'
            f'<label style="margin:2px 0 0;font-size:12px"><input type=checkbox name=clear_{k}> clear</label>')


@app.get("/secrets", response_class=HTMLResponse)
def secrets_get(request: Request):
    env = load_env()
    ready = set(configured_targets())
    sections = ""
    for prov, keys in publishers.PROVIDER_KEYS.items():
        badge = ('<span class="pill good">configured</span>' if prov in ready
                 else '<span class="pill dim">not set up</span>')
        rows = "".join(_secret_field(k, lb, ht, env) for k, lb, ht in keys)
        sections += (f'<h2>{e(publishers.PROVIDER_LABELS.get(prov, prov))} {badge}</h2>'
                     f'<div class=card>{rows}'
                     f'<div style="margin-top:10px"><button class="btn ghost" formaction=/secrets/test '
                     f'name=only value={prov} formnovalidate>Test {e(publishers.PROVIDER_LABELS.get(prov, prov))}</button></div>'
                     f'</div>')
    claude_badge = ('<span class="pill good">configured</span>' if env.get("ANTHROPIC_API_KEY_BRIDGE")
                    else '<span class="pill dim">not set up</span>')
    sections += (f'<h2>Claude API (bridge) {claude_badge}</h2>'
                 f'<div class=card>{"".join(_secret_field(k, lb, ht, env) for k, lb, ht in CLAUDE_KEYS)}'
                 f'<div style="margin-top:10px"><button class="btn ghost" formaction=/secrets/test '
                 f'name=only value=claude formnovalidate>Test Claude API</button></div></div>')
    sections += (f'<h2>Backup AI analysts</h2><div class=card><small>Used only when Claude is '
                 f'unavailable. Their public posts wait for your approval; subscribers are served either way.</small>'
                 f'{"".join(_secret_field(k, lb, ht, env) for k, lb, ht in BACKUP_AI_KEYS)}'
                 f'<div style="margin-top:10px"><button class="btn ghost" formaction=/secrets/test '
                 f'name=only value=backup_ai formnovalidate>Test backup keys</button></div></div>')
    extra = "".join(_secret_field(k, lb, ht, env) for k, lb, ht in
                    [("NTFY_URL", "Notification URL", "ntfy.sh-style topic for pipeline alerts")])
    dry = env.get("CTI_DRY_RUN", "")
    body = f"""<div class=card class=dim>Stored in <span class=mono>config/env</span>
(chmod 600, git-ignored). Password fields are write-only — leave blank to keep the current value,
tick <b>clear</b> to remove it. Plain fields (URLs, handles, recipients) are shown so you can check them.</div>
<form method=post action=/secrets>
{sections}
<h2>Other</h2><div class=card>{extra}
<label style="margin-top:12px">CTI_DRY_RUN override <small>— blank = use settings.yaml</small></label>
<select name=CTI_DRY_RUN>
<option value="" {'selected' if dry=='' else ''}>(use settings.yaml)</option>
<option value=true {'selected' if dry=='true' else ''}>true — never post</option>
<option value=false {'selected' if dry=='false' else ''}>false — allow posting</option>
</select></div>
<button class=btn>Save all secrets</button>
</form>"""
    return page("Secrets", body, "/secrets", flash_from(request))


@app.post("/secrets")
async def secrets_post(request: Request):
    f = await request.form()
    updates: dict = {}
    for k, _, _ in SECRET_KEYS:
        if f.get(f"clear_{k}"):
            updates[k] = None
            continue
        v = (f.get(k) or "").strip()
        if v:
            updates[k] = v
    cd = f.get("CTI_DRY_RUN", "")
    updates["CTI_DRY_RUN"] = cd if cd in ("true", "false") else None
    if updates:
        save_env(updates)
    return RedirectResponse("/secrets?m=Saved", 303)


def _test_linkedin(env: dict) -> str:
    tok = env.get("LINKEDIN_ACCESS_TOKEN")
    if not tok:
        return "No LINKEDIN_ACCESS_TOKEN set."
    try:
        r = requests.get("https://api.linkedin.com/v2/userinfo",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=15)
    except Exception as ex:  # noqa
        return f"error: {e(str(ex))}"
    if not r.ok:
        return f"HTTP {r.status_code}: <span class=mono>{e(r.text[:300])}</span>"
    j = r.json()
    real = f"urn:li:person:{j.get('sub','?')}"
    msg = f"OK — <b>{e(j.get('name','?'))}</b>, posts as <span class=mono>{e(real)}</span>"
    pinned = env.get("LINKEDIN_AUTHOR_URN", "").strip()
    if pinned and pinned != real and not pinned.startswith("urn:li:organization:"):
        msg += (f'<br><span class="pill bad">mismatch</span> pinned URN <span class=mono>{e(pinned)}</span> '
                f'is not this token — clear it or set it to <span class=mono>{e(real)}</span>.')
    return msg


def _test_mastodon(env: dict) -> str:
    base = (env.get("MASTODON_BASE_URL") or "").rstrip("/")
    tok = env.get("MASTODON_ACCESS_TOKEN")
    if not (base and tok):
        return "need MASTODON_BASE_URL + MASTODON_ACCESS_TOKEN"
    try:
        r = requests.get(f"{base}/api/v1/accounts/verify_credentials",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=15)
    except Exception as ex:  # noqa
        return f"error: {e(str(ex))}"
    if not r.ok:
        return f"HTTP {r.status_code}: <span class=mono>{e(r.text[:200])}</span>"
    return f"OK — <b>@{e(r.json().get('username','?'))}</b> on {e(base)}"


def _test_bluesky(env: dict) -> str:
    h, pw = (env.get("BLUESKY_HANDLE") or "").lstrip("@"), env.get("BLUESKY_APP_PASSWORD")
    pds = (env.get("BLUESKY_PDS_URL") or "https://bsky.social").rstrip("/")
    if not (h and pw):
        return "need BLUESKY_HANDLE + BLUESKY_APP_PASSWORD"
    try:
        r = requests.post(f"{pds}/xrpc/com.atproto.server.createSession",
                          json={"identifier": h, "password": pw}, timeout=15)
    except Exception as ex:  # noqa
        return f"error: {e(str(ex))}"
    if not r.ok:
        return f"HTTP {r.status_code}: <span class=mono>{e(r.text[:200])}</span>"
    return f"OK — session for <b>{e(r.json().get('handle', h))}</b>"


def _test_x(env: dict) -> str:
    need = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]
    if not all(env.get(k) for k in need):
        return "missing: " + ", ".join(k for k in need if not env.get(k))
    return ("all four keys are set. X has no cheap read check on the free tier — "
            "the first real post confirms it works.")


def _test_email(env: dict) -> str:
    import smtplib
    import ssl
    host = env.get("EMAIL_SMTP_HOST")
    to = env.get("EMAIL_TO")
    if not (host and to):
        return "need EMAIL_SMTP_HOST + EMAIL_TO"
    port = int(env.get("EMAIL_SMTP_PORT") or 587)
    try:
        if port == 465:
            s = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(host, port, timeout=15)
            s.ehlo()
            try:
                s.starttls(context=ssl.create_default_context())
            except smtplib.SMTPNotSupportedError:
                pass
        if env.get("EMAIL_SMTP_USER") and env.get("EMAIL_SMTP_PASSWORD"):
            s.login(env["EMAIL_SMTP_USER"], env["EMAIL_SMTP_PASSWORD"])
        s.quit()
    except Exception as ex:  # noqa
        return f"connect/login failed: {e(str(ex)[:200])}"
    return f"OK — connected to {e(host)}:{port}, recipients: <span class=mono>{e(to)}</span>"


def _test_claude(env: dict) -> str:
    key = env.get("ANTHROPIC_API_KEY_BRIDGE")
    if not key:
        return "No ANTHROPIC_API_KEY_BRIDGE set."
    try:
        r = requests.get("https://api.anthropic.com/v1/models",
                         headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=15)
    except Exception as ex:  # noqa
        return f"error: {e(str(ex))}"
    if not r.ok:
        return f"HTTP {r.status_code}: <span class=mono>{e(r.text[:300])}</span>"
    n = len(r.json().get("data") or [])
    return (f"OK: key accepted, {n} models visible. This check is free; "
            f"credit balance is only on console.anthropic.com.")


def _test_backup_ai(env: dict) -> str:
    """Model listing only: free, and shows the ids to pin in settings.yaml."""
    out = []
    g = env.get("GEMINI_API_KEY")
    if g:
        try:
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                             params={"key": g}, timeout=15)
            out.append(f"Gemini: {'OK' if r.ok else 'HTTP ' + str(r.status_code)}")
        except Exception as ex:  # noqa
            out.append(f"Gemini: error {e(str(ex)[:100])}")
    for k, url in _AI_MODEL_LISTS.items():
        key = env.get(k)
        if not key:
            continue
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=15)
        except Exception as ex:  # noqa
            out.append(f"{k}: error {e(str(ex)[:100])}")
            continue
        if not r.ok:
            out.append(f"{k}: HTTP {r.status_code}")
            continue
        data = r.json().get("data")
        out.append(f"{k}: OK" + (f", {len(data)} models" if isinstance(data, list) else ", key accepted"))
    return "<br>".join(e(x) if "<" not in x else x for x in out) or "No backup keys set."


_TESTS = {"linkedin": _test_linkedin, "mastodon": _test_mastodon, "bluesky": _test_bluesky,
          "x": _test_x, "email": _test_email, "claude": _test_claude,
          "backup_ai": _test_backup_ai}


@app.post("/secrets/test", response_class=HTMLResponse)
def secrets_test(only: str = Form("linkedin")):
    env = load_env()
    fn = _TESTS.get(only, _test_linkedin)
    msg = fn(env)
    return page(f"{publishers.PROVIDER_LABELS.get(only, only)} test",
                f"<div class=card>{msg}</div><p><a href=/secrets>← back to Secrets</a></p>", "/secrets")


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


# ----------------------------------------------------------------------------- subscribers (paid service)
def _worker():
    """(base_url, admin_token) when the CTI Alerts Worker is configured, else (None, None)."""
    env = load_env()
    base = (env.get("CTI_WORKER_URL") or "").rstrip("/")
    tok = env.get("CTI_ADMIN_TOKEN") or ""
    return (base or None, tok or None)


def worker_api(method: str, path: str, body=None):
    base, tok = _worker()
    r = requests.request(method, f"{base}{path}",
                         headers={"Authorization": f"Bearer {tok}"} if tok else {},
                         json=body, timeout=20)
    r.raise_for_status()
    ct = r.headers.get("content-type", "")
    return r.json() if ct.startswith("application/json") else r.text


def _subs_guard() -> HTMLResponse | None:
    if _worker()[0]:
        return None
    if not _SUBS_OK:
        return page("Subscribers",
                    "<div class=card>The subscription stack isn't importable — check "
                    "<span class=mono>lib/subscribers.py</span> and its deps.</div>", "/subscribers")
    return None


def _worker_subscribers_view(request: Request) -> HTMLResponse | None:
    """Render /subscribers from the Worker admin API. Returns None to fall through to local."""
    base, _ = _worker()
    if not base:
        return None
    qs = request.query_params
    try:
        st = worker_api("GET", "/admin/stats")
        params = []
        if qs.get("status"):
            params.append("status=" + quote(qs["status"]))
        if qs.get("q"):
            params.append("q=" + quote(qs["q"]))
        subs = worker_api("GET", "/admin/subscribers" + ("?" + "&".join(params) if params else ""))["subscribers"]
    except Exception as ex:  # noqa
        return page("Subscribers",
                    f'<div class="flash" style="border-color:var(--bad)">Can\'t reach the CTI Alerts Worker at '
                    f'<span class=mono>{e(base)}</span> — {e(str(ex)[:200])}</div>'
                    f'<p class=dim>Check <span class=mono>CTI_WORKER_URL</span> / '
                    f'<span class=mono>CTI_ADMIN_TOKEN</span> on the Secrets page.</p>', "/subscribers")

    def _r(s):
        sc = {"active": "good", "past_due": "bad", "cancelled": "dim", "paused": "warn",
              "unconfirmed": "warn", "pending_payment": "dim"}.get(s["status"], "dim")
        return (f"<tr><td><a href='/subscribers/{s['id']}'>#{s['id']}</a></td>"
                f"<td>{e(s['email'])}</td><td>{e(s['tier'])} <span class=dim>{e(s.get('cadence',''))}</span></td>"
                f"<td><span class='pill {sc}'>{e(s['status'])}</span></td>"
                f"<td class=dim>{e(rel_time(s['created_at']))}</td></tr>")

    filt = "".join(
        f'<a class="btn ghost" style="padding:4px 10px;font-size:12px" href="/subscribers?status={k}">{k} ({v})</a> '
        for k, v in sorted((st.get("by_status") or {}).items()))
    probs = st.get("problems") or []
    ready = ('' if not probs else
             '<div class="flash" style="border-color:var(--warn)"><b>Before go-live:</b><ul>'
             + "".join(f"<li>{e(x)}</li>" for x in probs) + "</ul></div>")
    body = f"""
<div class="flash" style="border-color:var(--good)">Connected to the CTI Alerts Worker ·
<span class=mono>{e(base)}</span> · config lives in <span class=mono>worker/wrangler.toml</span></div>
{ready}
<div class=tiles>
  <div class=tile><div class=n>{st.get('total_active', 0)}</div><div class=l>active subscribers</div></div>
  <div class=tile><div class=n>£{st.get('mrr_pence', 0) / 100:,.0f}</div><div class=l>est. MRR</div></div>
  <div class=tile><div class=n>{st.get('sent_24h', 0)}</div><div class=l>alerts sent · 24h</div></div>
  <div class="tile {'bad' if (st.get('by_status') or {}).get('past_due') else ''}"><div class=n>{(st.get('by_status') or {}).get('past_due', 0)}</div><div class=l>past due</div></div>
</div>
<div class=card>
  <form class=row method=get action=/subscribers>
    <input type=text name=q value="{e(qs.get('q',''))}" placeholder="search email" style="max-width:280px">
    <button class=btn>Search</button><a class="btn ghost" href="/subscribers">clear</a>
    <a class="btn ghost" href="/subscribers/add">+ add / comp</a>
  </form>
  <div style="margin-top:8px">{filt or '<span class=dim>no subscribers yet</span>'}</div>
  <p class=dim style="margin-top:8px">last digest {e(rel_time(st.get('last_digest','') or ''))} ·
  last retention {e(rel_time(st.get('last_retention','') or ''))}</p>
</div>
<div class=card style="padding:0"><table>
<tr><th>id</th><th>email</th><th>tier</th><th>status</th><th>joined</th></tr>
{''.join(_r(s) for s in subs) or '<tr><td colspan=5 class=dim>none</td></tr>'}
</table></div>
"""
    return page("Subscribers", body, "/subscribers", flash_from(request))


def _worker_subscriber_detail(sid: int, request: Request) -> HTMLResponse | None:
    base, _ = _worker()
    if not base:
        return None
    try:
        d = worker_api("GET", f"/admin/subscribers/{sid}")
    except Exception as ex:  # noqa
        return page("Subscriber", f"<div class=card>Worker error: {e(str(ex)[:200])}</div>", "/subscribers")
    if d.get("error"):
        return page("Subscriber", "<div class=card>Not found (may have been erased).</div>", "/subscribers")
    s = d["subscriber"]
    ch = "".join(f"<tr><td>{e(c['kind'])}</td><td class=dim>{e((c['target'] or '')[:50])}</td>"
                 f"<td>{'✓' if c['verified'] else '<span class=dim>unverified</span>'}</td></tr>"
                 for c in s.get("channels", []))
    cons = "".join(f"<tr><td class=dim>{e(rel_time(c['ts']))}</td><td>{e(c['kind'])}</td>"
                   f"<td>{'granted' if c['granted'] else 'withdrawn'}</td><td class=dim>{e(c['ip'] or '')}</td>"
                   f"<td class=dim>{e(c['terms_version'] or '')}</td></tr>" for c in d.get("consents", []))
    devs = "".join(f"<tr><td class=dim>{e(rel_time(x['sent_at']))}</td><td>{e(x['channel_kind'])}</td>"
                   f"<td>{e(x['mode'])}</td><td>{'ok' if x['ok'] else e(x['error'])}</td>"
                   f"<td class=dim>{e((x['post_key'] or '')[:44])}</td></tr>" for x in d.get("deliveries", []))
    m = flash_from(request)
    body = f"""
<div class=card><div class=row style="justify-content:space-between">
<div><b>{e(s['email'])}</b> · <span class="pill dim">{e(s['status'])}</span> · {e(s['tier'])} ({e(s.get('cadence',''))})
· <span class=dim>joined {e(rel_time(s['created_at']))}</span></div>
<div class=dim>{e(s.get('source',''))}{' · ' + e(s['stripe_customer_id']) if s.get('stripe_customer_id') else ''}</div></div>
{('<p class=dim>' + e(s['notes']) + '</p>') if s.get('notes') else ''}</div>

<form method=post action=/subscribers/{sid}/edit><div class=card><div class=grid>
<div><label>Status</label><select name=status>{''.join(f"<option {'selected' if x == s['status'] else ''}>{x}</option>" for x in ('active','paused','past_due','cancelled','unconfirmed'))}</select></div>
<div><label>Score override</label><input type=text name=min_score value="{e(s.get('prefs',{}).get('min_score_override') or '')}"></div>
</div>
<label>Vendors / keywords</label><input type=text name=vendors value="{e(', '.join(s.get('prefs',{}).get('vendors',[])))}">
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=instant {'checked' if s.get('prefs',{}).get('instant') else ''}> instant</label>
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=digest {'checked' if s.get('prefs',{}).get('digest') else ''}> digest</label>
<div style="margin-top:10px"><button class=btn>Save</button></div></div></form>

<h2>Channels</h2><div class=card style="padding:0"><table><tr><th>kind</th><th>target</th><th>ok</th></tr>{ch or '<tr><td colspan=3 class=dim>none</td></tr>'}</table></div>

<div class=row style="margin:14px 0">
<form method=post action=/subscribers/{sid}/test><button class="btn ghost">Send test alert</button></form>
<a class="btn ghost" href="/subscribers/{sid}/export.json">Export (DSAR)</a>
<form method=post action=/subscribers/{sid}/erase onsubmit="return confirm('Erase this subscriber and all their data? Cancels Stripe if linked. Irreversible.')"><button class="btn bad">Erase (DSAR)</button></form>
</div>
<h2>Consent log</h2><div class=card style="padding:0"><table><tr><th>when</th><th>kind</th><th></th><th>ip</th><th>terms</th></tr>{cons or '<tr><td colspan=5 class=dim>none</td></tr>'}</table></div>
<h2>Deliveries</h2><div class=card style="padding:0"><table><tr><th>when</th><th>channel</th><th>mode</th><th>result</th><th>post</th></tr>{devs or '<tr><td colspan=5 class=dim>none</td></tr>'}</table></div>
"""
    return page(f"Subscriber #{sid}", body, "/subscribers", m)


def subs_mrr_pence() -> int:
    """Estimate monthly recurring revenue from active subscribers (per-tier x cadence)."""
    if not _SUBS_OK:
        return 0
    rows = SUBS.db().query(
        "SELECT tier, cadence, COUNT(*) n FROM subscribers "
        "WHERE status IN ('active','paused','past_due') GROUP BY tier, cadence")
    return sum(r["n"] * subsconfig.monthly_pence(r["tier"], r["cadence"] or "monthly") for r in rows)


@app.get("/subscribers", response_class=HTMLResponse)
def subscribers_list(request: Request):
    wv = _worker_subscribers_view(request)
    if wv is not None:
        return wv
    g = _subs_guard()
    if g:
        return g
    SUBS.ensure_schema()
    q = request.query_params.get("q", "").strip()
    status = request.query_params.get("status", "").strip()
    rows = SUBS.list_subscribers(status=status or None, q=q or None, limit=500)
    st = SUBS.stats()
    cfg = subsconfig.load()

    def _r(s):
        chs = ",".join(c["kind"] for c in SUBS.channels_for(s["id"]))
        sc = {"active": "good", "past_due": "bad", "cancelled": "dim",
              "paused": "warn", "unconfirmed": "warn", "pending_payment": "dim"}.get(s["status"], "dim")
        return (f"<tr><td><a href='/subscribers/{s['id']}'>#{s['id']}</a></td>"
                f"<td>{e(s['email'])}</td><td>{e(s['tier'])}</td>"
                f"<td><span class='pill {sc}'>{e(s['status'])}</span></td>"
                f"<td class=dim>{e(chs)}</td>"
                f"<td class=dim>{e(rel_time(s['created_at']))}</td></tr>")

    filt = "".join(
        f'<a class="btn ghost" style="padding:4px 10px;font-size:12px" href="/subscribers?status={k}">{k} ({v})</a> '
        for k, v in sorted(st.get("by_status", {}).items()))
    body = f"""
<div class=tiles>
  <div class=tile><div class=n>{st.get('total_active', 0)}</div><div class=l>active subscribers</div></div>
  <div class=tile><div class=n>£{subs_mrr_pence() / 100:,.0f}</div><div class=l>est. MRR</div></div>
  <div class=tile><div class=n>{st.get('sent_24h', 0)}</div><div class=l>alerts sent · 24h</div></div>
  <div class="tile {'bad' if st.get('by_status', {}).get('past_due') else ''}"><div class=n>{st.get('by_status', {}).get('past_due', 0)}</div><div class=l>past due</div></div>
</div>
<div class=card>
  <form class=row method=get action=/subscribers>
    <input type=text name=q value="{e(q)}" placeholder="search email / name / notes" style="max-width:280px">
    <button class=btn>Search</button>
    <a class="btn ghost" href="/subscribers">clear</a>
    <a class="btn ghost" href="/subscribers/add">+ add / comp</a>
    <a class="btn ghost" href="/subscribers/export.csv">export CSV</a>
    <a class="btn ghost" href="/subscriptions">settings &amp; readiness</a>
  </form>
  <div style="margin-top:8px">{filt or '<span class=dim>no subscribers yet</span>'}</div>
</div>
<div class=card style="padding:0"><table>
<tr><th>id</th><th>email</th><th>tier</th><th>status</th><th>channels</th><th>joined</th></tr>
{''.join(_r(s) for s in rows) or '<tr><td colspan=6 class=dim>none</td></tr>'}
</table></div>
{'' if subsconfig.load().get('enabled') else '<div class="flash" style="border-color:var(--warn)">subscriptions.enabled is <b>false</b> — the public site and delivery are off. <a href=/subscriptions>Set it up →</a></div>'}
"""
    return page("Subscribers", body, "/subscribers", flash_from(request))


@app.get("/subscribers/add", response_class=HTMLResponse)
def subscribers_add_form(request: Request):
    g = _subs_guard()
    if g:
        return g
    tiers = "".join(f"<option>{t}</option>" for t in subsconfig.load()["tiers"])
    body = f"""<div class=card>Add a subscriber by hand — for a comp / test / manually-billed account.
No payment is taken. They still confirm by email before alerts start (unless you tick “skip”).</div>
<form method=post action=/subscribers/add>
<label>Email</label><input type=email name=email required>
<label>Tier</label><select name=tier>{tiers}</select>
<label>Note <small>— why this is a manual account</small></label><input type=text name=note>
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=skip_confirm> Skip email confirmation (activate now)</label>
<div style="margin-top:12px"><button class=btn>Add subscriber</button></div>
</form>"""
    return page("Add subscriber", body, "/subscribers")


@app.post("/subscribers/add")
def subscribers_add(email: str = Form(""), tier: str = Form("individual"),
                    note: str = Form(""), skip_confirm: str = Form("")):
    email = email.strip().lower()
    if "@" not in email:
        return RedirectResponse("/subscribers/add?m=" + quote("Invalid email"), 303)
    if _worker()[0]:
        try:
            worker_api("POST", "/admin/add", {"email": email, "tier": tier, "note": note,
                                              "skip_confirm": bool(skip_confirm)})
        except Exception as ex:  # noqa
            return RedirectResponse("/subscribers/add?m=" + quote(f"Worker error: {ex}"), 303)
        return RedirectResponse("/subscribers?m=" + quote("Added"), 303)
    if _subs_guard():
        return RedirectResponse("/subscribers", 303)
    sid = SUBS.create_pending(email, tier, source="comp")
    SUBS.db().execute("UPDATE subscribers SET notes = ? WHERE id = ?", [note or "manual add", sid])
    if skip_confirm:
        SUBS.set_status(sid, "active", "manual activate by operator")
        SUBS.log_consent(sid, "signup", True, detail="operator manual add, confirmation skipped")
    else:
        SUBS.set_status(sid, "unconfirmed", "manual add")
        t = None
        try:
            import tokens as _tok
            t = _tok.sign({"sub": sid, "act": "confirm"}, ttl=14 * 86400)
        except Exception:  # noqa
            pass
        if t:
            site = subsconfig.load().get("site_url", "")
            _channels_send_email(email, "Confirm your Blue Team CTI alerts",
                                 f"An account was created for you.\n\nConfirm to start alerts:\n{site}/confirm?t={t}")
    return RedirectResponse(f"/subscribers/{sid}?m=" + quote("Added"), 303)


def _channels_send_email(to: str, subject: str, body: str) -> None:
    try:
        import channels as _ch
        _ch.send("email", to, subject, body, creds=_env_with_config())
    except Exception:  # noqa
        pass


@app.get("/subscribers/export.csv", response_class=PlainTextResponse)
def subscribers_export_csv():
    if _subs_guard():
        return PlainTextResponse("subs stack unavailable", status_code=503)
    import csv
    import io
    SUBS.ensure_schema()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "email", "name", "tier", "status", "source", "created_at",
                "confirmed_at", "cancelled_at", "stripe_customer_id", "channels", "vendors"])
    for s in SUBS.list_subscribers(limit=100000):
        p = SUBS.get_prefs(s["id"])
        chs = "|".join(f"{c['kind']}:{'v' if c['verified'] else 'u'}" for c in SUBS.channels_for(s["id"]))
        w.writerow([s["id"], s["email"], s.get("name", ""), s["tier"], s["status"], s["source"],
                    s["created_at"], s.get("confirmed_at", ""), s.get("cancelled_at", ""),
                    s.get("stripe_customer_id", ""), chs, "|".join(p["vendors"])])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=subscribers.csv"})


@app.get("/subscribers/{sid}", response_class=HTMLResponse)
def subscriber_detail(sid: int, request: Request):
    wv = _worker_subscriber_detail(sid, request)
    if wv is not None:
        return wv
    g = _subs_guard()
    if g:
        return g
    SUBS.ensure_schema()
    s = SUBS.get(sid)
    if not s:
        return page("Subscriber", "<div class=card>Not found (may have been erased).</div>", "/subscribers")
    p = s["prefs"]
    cfg = subsconfig.load()
    tiers = "".join(f"<option {'selected' if t == s['tier'] else ''}>{t}</option>" for t in cfg["tiers"])
    ch_rows = "".join(
        f"<tr><td>{e(c['kind'])}</td><td class=dim>{e(c['target'][:50])}</td>"
        f"<td>{'✓' if c['verified'] else '<span class=dim>unverified</span>'}</td>"
        f"<td><form method=post action=/subscribers/{sid}/channel/remove style=display:inline>"
        f"<input type=hidden name=cid value={c['id']}><button class='btn ghost' style='padding:2px 8px;font-size:12px'>×</button></form></td></tr>"
        for c in s["channels"])
    cons = "".join(f"<tr><td class=dim>{e(rel_time(c['ts']))}</td><td>{e(c['kind'])}</td>"
                   f"<td>{'granted' if c['granted'] else 'withdrawn'}</td><td class=dim>{e(c['ip'] or '')}</td>"
                   f"<td class=dim>{e(c['terms_version'] or '')}</td></tr>"
                   for c in SUBS.consents_for(sid))
    devs = "".join(f"<tr><td class=dim>{e(rel_time(d['sent_at']))}</td><td>{e(d['channel_kind'])}</td>"
                   f"<td>{e(d['mode'])}</td><td>{'ok' if d['ok'] else e(d['error'])}</td>"
                   f"<td class=dim>{e((d['post_key'] or '')[:48])}</td></tr>"
                   for d in SUBS.deliveries_for(sid, 30))
    evs = "".join(f"<tr><td class=dim>{e(rel_time(ev['ts']))}</td><td>{e(ev['kind'])}</td>"
                  f"<td class=dim>{e((ev['detail'] or '')[:70])}</td></tr>"
                  for ev in SUBS.events_for(sid, 40))
    m = flash_from(request)
    body = f"""
<div class=card>
  <div class=row style="justify-content:space-between">
    <div><b>{e(s['email'])}</b> · <span class="pill dim">{e(s['status'])}</span> · {e(s['tier'])}
    · <span class=dim>joined {e(rel_time(s['created_at']))}</span></div>
    <div class=dim>{e(s['source'])}{' · ' + e(s['stripe_customer_id']) if s.get('stripe_customer_id') else ''}</div>
  </div>
  {('<p class=dim>' + e(s['notes']) + '</p>') if s.get('notes') else ''}
</div>

<form method=post action=/subscribers/{sid}/edit>
<div class=card><div class=grid>
<div><label>Tier</label><select name=tier>{tiers}</select></div>
<div><label>Status</label><select name=status>{''.join(f"<option {'selected' if x == s['status'] else ''}>{x}</option>" for x in SUBS.STATUSES)}</select></div>
<div><label>Score override</label><input type=text name=min_score value="{e('' if p['min_score_override'] is None else p['min_score_override'])}"></div>
</div>
<label>Vendors / keywords</label><input type=text name=vendors value="{e(', '.join(p['vendors']))}">
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=instant {'checked' if p['instant'] else ''}> instant</label>
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=digest {'checked' if p['digest'] else ''}> digest</label>
<div style="margin-top:10px"><button class=btn>Save</button></div>
</div></form>

<h2>Channels</h2>
<div class=card style="padding:0"><table><tr><th>kind</th><th>target</th><th>ok</th><th></th></tr>{ch_rows or '<tr><td colspan=4 class=dim>none</td></tr>'}</table></div>
<form method=post action=/subscribers/{sid}/channel/add class=card>
<div class=row>
<select name=kind style="max-width:150px">{''.join(f'<option>{k}</option>' for k in SUBS.CHANNEL_KINDS)}</select>
<input type=text name=target placeholder="address / URL / chat id">
<label class=row style="gap:6px;font-weight:400"><input type=checkbox name=verified checked> verified</label>
<button class=btn>Add</button>
</div></form>

<div class=row style="margin:14px 0">
<form method=post action=/subscribers/{sid}/test><button class="btn ghost">Send test alert</button></form>
<a class="btn ghost" href="/subscribers/{sid}/export.json">Export (DSAR)</a>
<form method=post action=/subscribers/{sid}/erase onsubmit="return confirm('Erase this subscriber and all their data? Cancels Stripe if linked. Irreversible.')">
<button class="btn bad">Erase (DSAR)</button></form>
</div>

<h2>Consent log</h2><div class=card style="padding:0"><table><tr><th>when</th><th>kind</th><th></th><th>ip</th><th>terms</th></tr>{cons or '<tr><td colspan=5 class=dim>none</td></tr>'}</table></div>
<h2>Deliveries</h2><div class=card style="padding:0"><table><tr><th>when</th><th>channel</th><th>mode</th><th>result</th><th>post</th></tr>{devs or '<tr><td colspan=5 class=dim>none</td></tr>'}</table></div>
<details><summary class=dim>Event history</summary><div class=card style="padding:0"><table>{evs}</table></div></details>
"""
    return page(f"Subscriber #{sid}", body, "/subscribers", m)


@app.post("/subscribers/{sid}/edit")
def subscriber_edit(sid: int, tier: str = Form(""), status: str = Form(""), vendors: str = Form(""),
                    min_score: str = Form(""), instant: str = Form(""), digest: str = Form("")):
    if _worker()[0]:
        try:
            worker_api("POST", f"/admin/subscribers/{sid}/edit", {
                "tier": tier or None, "status": status or None,
                "vendors": [v.strip() for v in re.split(r"[,\n]", vendors) if v.strip()][:60],
                "min_score": min_score.strip(), "instant": bool(instant), "digest": bool(digest)})
        except Exception as ex:  # noqa
            return RedirectResponse(f"/subscribers/{sid}?m=" + quote(f"Worker error: {ex}"), 303)
        return RedirectResponse(f"/subscribers/{sid}?m=" + quote("Saved"), 303)
    if _subs_guard():
        return RedirectResponse("/subscribers", 303)
    if tier in subsconfig.load()["tiers"]:
        SUBS.db().execute("UPDATE subscribers SET tier = ? WHERE id = ?", [tier, sid])
    if status in SUBS.STATUSES and status != SUBS.get(sid, hydrate=False)["status"]:
        SUBS.set_status(sid, status, "operator edit")
    vlist = [v.strip() for v in re.split(r"[,\n]", vendors) if v.strip()][:60]
    mso = int(min_score) if min_score.strip().isdigit() else None
    SUBS.set_prefs(sid, vendors=vlist, min_score_override=mso,
                   instant=bool(instant), digest=bool(digest))
    return RedirectResponse(f"/subscribers/{sid}?m=" + quote("Saved"), 303)


@app.post("/subscribers/{sid}/channel/add")
def subscriber_channel_add(sid: int, kind: str = Form(""), target: str = Form(""), verified: str = Form("")):
    if _subs_guard():
        return RedirectResponse("/subscribers", 303)
    if kind in SUBS.CHANNEL_KINDS and target.strip():
        SUBS.add_channel(sid, kind, target.strip(), verified=bool(verified), label="operator")
    return RedirectResponse(f"/subscribers/{sid}", 303)


@app.post("/subscribers/{sid}/channel/remove")
def subscriber_channel_remove(sid: int, cid: int = Form(0)):
    if not _subs_guard():
        SUBS.remove_channel(cid)
    return RedirectResponse(f"/subscribers/{sid}", 303)


@app.post("/subscribers/{sid}/test")
def subscriber_test(sid: int):
    if _worker()[0]:
        try:
            worker_api("POST", f"/admin/subscribers/{sid}/test", {})
        except Exception as ex:  # noqa
            return RedirectResponse(f"/subscribers/{sid}?m=" + quote(f"Worker error: {ex}"), 303)
        return RedirectResponse(f"/subscribers/{sid}?m=" + quote("Test alert sent"), 303)
    if not _subs_guard():
        subprocess.Popen([sys.executable, str(ROOT / "bin" / "deliver.py"), "--test", str(sid)],
                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=_env_with_config())
    return RedirectResponse(f"/subscribers/{sid}?m=" + quote("Test alert queued"), 303)


@app.get("/subscribers/{sid}/export.json", response_class=PlainTextResponse)
def subscriber_export(sid: int):
    if _worker()[0]:
        try:
            data = worker_api("GET", f"/admin/subscribers/{sid}/export")
        except Exception as ex:  # noqa
            return PlainTextResponse(f"worker error: {ex}", status_code=502)
        return PlainTextResponse(json.dumps(data, indent=2, default=str), media_type="application/json",
                                 headers={"Content-Disposition": f"attachment; filename=subscriber-{sid}.json"})
    if _subs_guard():
        return PlainTextResponse("unavailable", status_code=503)
    data = SUBS.export_subscriber(sid)
    SUBS.log_event("dsar_export_by_operator", sid)
    return PlainTextResponse(json.dumps(data, indent=2, default=str), media_type="application/json",
                             headers={"Content-Disposition": f"attachment; filename=subscriber-{sid}.json"})


@app.post("/subscribers/{sid}/erase", response_class=HTMLResponse)
def subscriber_erase(sid: int):
    if _worker()[0]:
        try:
            worker_api("POST", f"/admin/subscribers/{sid}/erase", {})
        except Exception as ex:  # noqa
            return RedirectResponse("/subscribers?m=" + quote(f"Worker error: {ex}"), 303)
        return RedirectResponse("/subscribers?m=" + quote(f"Subscriber #{sid} erased"), 303)
    if _subs_guard():
        return RedirectResponse("/subscribers", 303)
    s = SUBS.get(sid, hydrate=False)
    if s and s.get("stripe_subscription_id"):
        try:
            import billing
            billing.cancel_subscription(s["stripe_subscription_id"])
        except Exception:  # noqa
            pass
    SUBS.erase_subscriber(sid, "operator DSAR erasure")
    return RedirectResponse("/subscribers?m=" + quote(f"Subscriber #{sid} erased"), 303)


@app.get("/purchases", response_class=HTMLResponse)
def purchases_list(request: Request):
    if not _worker()[0]:
        return page("Purchases",
                    "<div class=card>One-off purchases (threat briefs) are handled by the "
                    "CTI Alerts Worker. Set <span class=mono>CTI_WORKER_URL</span> on the "
                    "<a href=/secrets>Secrets</a> page.</div>", "/purchases")
    try:
        rows = worker_api("GET", "/admin/purchases")["purchases"]
    except Exception as ex:  # noqa
        return page("Purchases", f'<div class="flash" style="border-color:var(--bad)">'
                    f'Worker error: {e(str(ex)[:200])}</div>', "/purchases")

    def _row(p):
        amt = (f"£{p['amount_total'] / 100:,.2f}" if p.get("amount_total") is not None else "—")
        stc = {"paid": "good", "fulfilled": "dim", "pending": "warn"}.get(p["status"], "dim")
        fulfil = ("" if p["status"] == "fulfilled" else
                  f'<form method=post action=/purchases/{quote(p["stripe_session_id"])}/fulfilled style=display:inline>'
                  f'<button class="btn ghost" style="padding:3px 9px;font-size:12px">mark delivered</button></form>')
        return (f"<tr><td class=dim>{e(rel_time(p['created_at']))}</td><td>{e(p.get('email') or '')}</td>"
                f"<td>{e(p.get('item'))}</td><td>{amt}</td>"
                f"<td><span class='pill {stc}'>{e(p['status'])}</span></td>"
                f"<td class=dim>{e((p.get('topic') or '')[:60])}</td><td>{fulfil}</td></tr>")

    paid = sum(1 for p in rows if p["status"] in ("paid", "fulfilled"))
    revenue = sum((p.get("amount_total") or 0) for p in rows if p["status"] in ("paid", "fulfilled"))
    body = f"""
<div class=tiles>
  <div class=tile><div class=n>{paid}</div><div class=l>briefs sold</div></div>
  <div class=tile><div class=n>£{revenue / 100:,.0f}</div><div class=l>one-off revenue</div></div>
  <div class="tile {'warn' if any(p['status'] == 'paid' for p in rows) else ''}"><div class=n>{sum(1 for p in rows if p['status'] == 'paid')}</div><div class=l>awaiting delivery</div></div>
</div>
<div class=card style="padding:0"><table>
<tr><th>when</th><th>email</th><th>item</th><th>amount</th><th>status</th><th>topic</th><th></th></tr>
{''.join(_row(p) for p in rows) or '<tr><td colspan=7 class=dim>no purchases yet</td></tr>'}
</table></div>
"""
    return page("Purchases", body, "/purchases", flash_from(request))


SECRET_HELP = {
    "STRIPE_SECRET_KEY": ("checkout can't run at all", "sk_test_… from dashboard.stripe.com/apikeys"),
    "STRIPE_WEBHOOK_SECRET": ("customers pay but are NEVER provisioned", "whsec_… from the Stripe webhook you point at /webhooks/stripe"),
    "SUBS_TOKEN_SECRET": ("confirm / preferences / unsubscribe links all 500", "any 32+ random bytes"),
    "RESEND_API_KEY": ("email delivery is offered as 'unavailable'", "re_… from resend.com"),
    "INGEST_SECRET": ("the pipeline can't push alerts in", "must equal CTI_INGEST_SECRET in config/env"),
    "ADMIN_TOKEN": ("this dashboard can't read the Worker", "must equal CTI_ADMIN_TOKEN in config/env"),
    "TELEGRAM_BOT_TOKEN": ("Telegram shows as unavailable (optional)", "from @BotFather"),
    "TELEGRAM_BOT_USERNAME": ("Telegram shows as unavailable (optional)", "the bot name, without the @"),
    "TELEGRAM_WEBHOOK_SECRET": ("Telegram replies aren't verified (optional)", "any random string"),
}
CRITICAL_SECRETS = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "SUBS_TOKEN_SECRET",
                    "RESEND_API_KEY", "INGEST_SECRET", "ADMIN_TOKEN")


@app.get("/service", response_class=HTMLResponse)
def service_console(request: Request):
    """Operator console for the public CTI service (the Cloudflare Worker)."""
    base, tok = _worker()
    if not base:
        return page("CTI service",
                    "<div class=card>Set <span class=mono>CTI_WORKER_URL</span> and "
                    "<span class=mono>CTI_ADMIN_TOKEN</span> on the <a href=/secrets>Secrets</a> "
                    "page to manage the live service from here.</div>", "/service")

    # Reachability is checked separately from auth so we can tell them apart.
    health, health_err = "", ""
    try:
        r = requests.get(f"{base}/healthz", timeout=10)
        health = "ok" if r.ok and r.text.strip() == "ok" else f"HTTP {r.status_code} {r.text[:60]}"
    except Exception as ex:  # noqa
        health_err = str(ex)[:160]

    st, api_err = None, ""
    try:
        st = worker_api("GET", "/admin/stats")
    except Exception as ex:  # noqa
        api_err = str(ex)[:200]

    def banner():
        if health_err:
            return ('<div class="flash" style="border-color:var(--bad)"><b>Worker unreachable.</b> '
                    f'<span class=mono>{e(base)}</span> — {e(health_err)}</div>')
        if st is None:
            hint = ""
            if "401" in api_err or "unauthor" in api_err.lower():
                hint = ("<p>The Worker is up but rejected this dashboard's token — "
                        "<span class=mono>ADMIN_TOKEN</span> on the Worker does not match "
                        "<span class=mono>CTI_ADMIN_TOKEN</span> in <span class=mono>config/env</span>. "
                        "Fix by running, in <span class=mono>~/Threatintel/worker</span>:</p>"
                        "<pre class=mono>npx wrangler secret put ADMIN_TOKEN</pre>"
                        "<p>and pasting the value from the <a href=/secrets>Secrets</a> page.</p>")
            return ('<div class="flash" style="border-color:var(--bad)"><b>Worker is up but the admin API '
                    f'failed.</b> {e(api_err)}{hint}</div>')
        return ('<div class="flash" style="border-color:var(--good)">Connected · '
                f'<span class=mono>{e(base)}</span> · healthz <b>{e(health)}</b></div>')

    body = banner()
    if st is None:
        body += ('<div class=card><b>What this page shows once connected</b><ul>'
                 '<li>which Worker secrets are actually live (Stripe, Resend, tokens)</li>'
                 '<li>which delivery channels subscribers can pick right now</li>'
                 '<li>subscribers, MRR, alerts sent, past-due count</li>'
                 '<li>go-live blockers and reminders</li></ul></div>')
        return page("CTI service", body, "/service", flash_from(request))

    secrets = st.get("secrets") or {}
    missing_critical = [k for k in CRITICAL_SECRETS if not secrets.get(k)]
    if missing_critical:
        rows = "".join(
            f"<li><span class=mono>{e(k)}</span> — <b>{e(SECRET_HELP[k][0])}</b><br>"
            f"<span class=dim>{e(SECRET_HELP[k][1])}</span></li>" for k in missing_critical)
        cmds = "\n".join(f"npx wrangler secret put {k}" for k in missing_critical)
        body += ('<div class="flash" style="border-color:var(--bad)">'
                 f'<b>{len(missing_critical)} required secret(s) missing on the Worker.</b>'
                 f'<ul>{rows}</ul>'
                 '<p>From <span class=mono>~/Threatintel/worker</span>:</p>'
                 f'<pre class=mono>{e(cmds)}</pre>'
                 '<p class=dim>Then <span class=mono>npx wrangler deploy</span>.</p></div>')

    def _sec_row(k, present):
        crit = k in CRITICAL_SECRETS
        pill = ('<span class="pill good">set</span>' if present else
                (f'<span class="pill bad">MISSING</span>' if crit else '<span class=pill>not set</span>'))
        return (f"<tr><td class=mono>{e(k)}</td><td>{pill}</td>"
                f"<td class=dim>{e(SECRET_HELP.get(k, ('', ''))[0])}</td></tr>")

    chans = st.get("channels") or {}

    def _chan_row(k, v):
        pill = ('<span class="pill good">available</span>' if v.get("ok")
                else f'<span class="pill bad">unavailable</span>')
        return f"<tr><td>{e(k)}</td><td>{pill}</td><td class=dim>{e(v.get('why') or '')}</td></tr>"

    probs = st.get("problems") or []
    advs = st.get("advisories") or []
    by_status = st.get("by_status") or {}
    mode = st.get("stripe_mode", "unset")
    mode_pill = {"live": "good", "test": "warn", "unset": "bad"}.get(mode, "dim")

    blockers = ("" if not probs else
                '<div class="flash" style="border-color:var(--warn)"><b>Go-live blockers:</b><ul>'
                + "".join(f"<li>{e(x)}</li>" for x in probs) + "</ul></div>")
    reminders = ("" if not advs else
                 '<div class=card><b>Reminders (not blocking)</b><ul>'
                 + "".join(f"<li>{e(x)}</li>" for x in advs) + "</ul></div>")

    body += f"""
<div class=tiles>
  <div class=tile><div class=n>{st.get('total_active', 0)}</div><div class=l>active subscribers</div></div>
  <div class=tile><div class=n>£{st.get('mrr_pence', 0) / 100:,.0f}</div><div class=l>est. MRR</div></div>
  <div class=tile><div class=n>{st.get('sent_24h', 0)}</div><div class=l>alerts sent · 24h</div></div>
  <div class="tile {'bad' if by_status.get('past_due') else ''}"><div class=n>{by_status.get('past_due', 0)}</div><div class=l>past due</div></div>
  <div class=tile><div class=n>{st.get('briefs_24h', 0)}</div><div class=l>briefs · 24h</div></div>
</div>
<div class=card>
  <div class=row>
    <span>Stripe: <span class="pill {mode_pill}">{e(mode)} mode</span></span>
    <span>Selling: <span class="pill {'good' if st.get('live') else 'warn'}">{'LIVE' if st.get('live') else 'test only'}</span></span>
    <a class="btn ghost" href="{e(base)}" target=_blank>open public site ↗</a>
    <a class="btn ghost" href="/d1">D1 tables</a>
    <a class="btn ghost" href="/subscribers">subscribers</a>
  </div>
  <p class=dim style="margin-top:10px">last digest {e(rel_time(st.get('last_digest') or ''))} ·
  last retention {e(rel_time(st.get('last_retention') or ''))}</p>
</div>
{blockers}{reminders}
<div class=grid2>
<div>
<h2 style="font-size:15px">Worker secrets</h2>
<div class=card style="padding:0"><table>
<tr><th>name</th><th>state</th><th>if missing</th></tr>
{''.join(_sec_row(k, secrets.get(k)) for k in SECRET_HELP)}
</table></div>
</div>
<div>
<h2 style="font-size:15px">Delivery channels</h2>
<div class=card style="padding:0"><table>
<tr><th>channel</th><th>state</th><th>why</th></tr>
{''.join(_chan_row(k, v) for k, v in sorted(chans.items()))}
</table></div>
<p class=dim>Turn one off deliberately with <span class=mono>CONFIG.channels_disabled</span> in
<span class=mono>worker/wrangler.toml</span>.</p>
</div>
</div>
<div class=card>
  <b>Subscribers by status</b>
  <div style="margin-top:8px">{''.join(
        f'<a class="btn ghost" style="padding:4px 10px;font-size:12px" href="/subscribers?status={k}">{e(k)} ({v})</a> '
        for k, v in sorted(by_status.items())) or '<span class=dim>none yet</span>'}</div>
</div>
"""
    return page("CTI service", body, "/service", flash_from(request))


@app.get("/d1", response_class=HTMLResponse)
def d1_browser(request: Request, table: str = "", offset: int = 0):
    """Read-only window onto the Worker's D1 database (via /admin/db)."""
    if not _worker()[0]:
        return page("D1 tables",
                    "<div class=card>Set <span class=mono>CTI_WORKER_URL</span> and "
                    "<span class=mono>CTI_ADMIN_TOKEN</span> on the <a href=/secrets>Secrets</a> "
                    "page to browse the Worker's D1 database.</div>", "/d1")
    try:
        tables = worker_api("GET", "/admin/db")["tables"]
    except Exception as ex:  # noqa
        return page("D1 tables", '<div class="flash" style="border-color:var(--bad)">'
                    f'Worker error: {e(str(ex)[:300])}</div>', "/d1")

    tabs = "".join(
        f'<a href="/d1?table={quote(t["name"])}" class="btn ghost" '
        f'style="padding:4px 10px;font-size:12px;{"border-color:var(--accent)" if t["name"] == table else ""}">'
        f'{e(t["name"])} <span class=dim>{t["rows"] if t["rows"] is not None else "?"}</span></a> '
        for t in tables)
    body = f'<div class=card><div class=l style="margin-bottom:10px">tables</div>{tabs}</div>'

    if table:
        try:
            d = worker_api("GET", f"/admin/db/{quote(table)}?limit=100&offset={int(offset)}")
        except Exception as ex:  # noqa
            return page("D1 tables", body + '<div class="flash" style="border-color:var(--bad)">'
                        f'{e(str(ex)[:300])}</div>', "/d1")
        cols = d["columns"]

        def _cell(v):
            s = "" if v is None else str(v)
            return f'<td class=dim>—</td>' if v is None else f"<td>{e(s[:120])}</td>"

        head = "".join(f"<th>{e(c)}</th>" for c in cols)
        rows = "".join("<tr>" + "".join(_cell(r.get(c)) for c in cols) + "</tr>" for r in d["rows"])
        nxt, prv = int(offset) + d["limit"], max(int(offset) - d["limit"], 0)
        pager = (f'<div class=row style="padding:10px">'
                 + (f'<a class="btn ghost" href="/d1?table={quote(table)}&offset={prv}">← prev</a>' if offset else "")
                 + (f'<a class="btn ghost" href="/d1?table={quote(table)}&offset={nxt}">next →</a>'
                    if nxt < (d["total"] or 0) else "")
                 + f'<span class=dim style="margin-left:auto">{d["total"]} rows total</span></div>')
        body += (f'<div class=card style="padding:0;overflow-x:auto"><table>'
                 f"<tr>{head}</tr>{rows or f'<tr><td colspan={len(cols)} class=dim>empty</td></tr>'}"
                 f"</table>{pager}</div>")
    else:
        body += '<div class=card class=dim>Pick a table above.</div>'
    return page("D1 tables", body, "/d1", flash_from(request))


@app.post("/purchases/{session_id}/fulfilled")
def purchase_fulfilled(session_id: str):
    if _worker()[0]:
        try:
            worker_api("POST", f"/admin/purchases/{quote(session_id)}/fulfilled", {})
        except Exception:  # noqa
            pass
    return RedirectResponse("/purchases?m=" + quote("Marked delivered"), 303)


@app.get("/subscriptions", response_class=HTMLResponse)
def subscriptions_settings(request: Request):
    base, _ = _worker()
    if base:
        try:
            st = worker_api("GET", "/admin/stats")
            probs = st.get("problems") or []
        except Exception as ex:  # noqa
            st, probs = {}, [f"can't reach the Worker: {ex}"]
        ready = ('<div class="flash good">Ready to accept subscribers.</div>' if not probs else
                 '<div class="card" style="border-color:var(--warn)"><b>Before go-live:</b><ul>'
                 + "".join(f"<li>{e(x)}</li>" for x in probs) + "</ul></div>")
        body = f"""{ready}
<div class=card><b>This deployment runs on the CTI Alerts Cloudflare Worker.</b>
<p class=dim>Config is not edited here — it lives in <span class=mono>worker/wrangler.toml</span>
(the <span class=mono>[vars]</span> block + the <span class=mono>CONFIG</span> JSON) and in Worker
secrets. Change it in git and redeploy:</p>
<pre>cd worker
npx wrangler deploy                 # or: git push  (Cloudflare auto-builds)
npx wrangler secret put STRIPE_SECRET_KEY
npx wrangler d1 execute cti-subs --remote --file schema.sql   # first time only</pre>
<p class=dim>Full checklist: <span class=mono>worker/README.md</span> and
<span class=mono>docs/legal/compliance-runbook.md</span>.</p>
<p>Worker: <span class=mono>{e(base)}</span> · active subs {e(st.get('total_active', 0))}
· est. MRR £{(st.get('mrr_pence', 0) or 0) / 100:,.0f}</p></div>
"""
        return page("Subscription settings", body, "/subscribers", flash_from(request))
    g = _subs_guard()
    if g:
        return g
    c = subsconfig.load()
    env = load_env()
    probs = subsconfig.problems()
    advs = subsconfig.advisories()
    stripe_state = ("live sk_live_ key" if env.get("STRIPE_SECRET_KEY", "").startswith("sk_live_")
                    else "test sk_test_ key" if env.get("STRIPE_SECRET_KEY", "").startswith("sk_test_")
                    else "not set")
    ready = ('<div class="flash good">Ready to accept subscribers.</div>' if not probs else
             '<div class="card" style="border-color:var(--warn)"><b>Before this can go live:</b><ul>'
             + "".join(f"<li>{e(x)}</li>" for x in probs) + "</ul></div>")
    if advs:
        ready += ('<div class="card"><b>Reminders (not blocking):</b><ul>'
                  + "".join(f"<li>{e(x)}</li>" for x in advs) + "</ul></div>")

    def ck(v):
        return "checked" if v else ""

    ti = c["tiers"]["individual"]
    tt = c["tiers"]["team"]
    body = f"""
{ready}
<form method=post action=/subscriptions>
<h2>Service</h2><div class=card>
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=enabled {ck(c.get('enabled'))}> Subscriptions enabled (public site + delivery on)</label>
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=live {ck(c.get('live'))}> Live billing (unticked = Stripe test mode)</label>
<div class=grid style="margin-top:10px">
<div><label>Brand name</label><input type=text name=brand_name value="{e(c.get('brand_name',''))}"></div>
<div><label>Public site URL</label><input type=text name=site_url value="{e(c.get('site_url',''))}"></div>
<div><label>Public bind (host:port)</label><input type=text name=public_bind value="{e(c.get('public_bind',''))}"></div>
<div><label>From email</label><input type=text name=from_email value="{e(c.get('from_email',''))}"></div>
<div><label>Reply-to / support email</label><input type=text name=support_email value="{e(c.get('support_email',''))}"></div>
<div><label>Contact email (privacy)</label><input type=text name=contact_email value="{e(c.get('contact_email',''))}"></div>
<div><label>Company / sole-trader legal name</label><input type=text name=company_legal_name value="{e(c.get('company_legal_name',''))}"></div>
<div><label>ICO registration number</label><input type=text name=ico_registration value="{e(c.get('ico_registration',''))}"></div>
<div><label>Digest hour (UTC)</label><input type=number name=digest_hour_utc value="{e(c.get('digest_hour_utc',8))}"></div>
<div><label>Retention: cancelled (days)</label><input type=number name=retention_days_cancelled value="{e(c.get('retention_days_cancelled',180))}"></div>
<div><label>Retention: unconfirmed (days)</label><input type=number name=retention_days_unconfirmed value="{e(c.get('retention_days_unconfirmed',7))}"></div>
<div><label>Rate limit / min</label><input type=number name=rate_limit_per_min value="{e(c.get('rate_limit_per_min',20))}"></div>
</div></div>

<h2>Individual tier</h2><div class=card><div class=grid>
<div><label>Monthly Stripe price id</label><input type=text name=i_monthly_price_id value="{e(ti.get('monthly_price_id',''))}"></div>
<div><label>Monthly display</label><input type=text name=i_monthly_display value="{e(ti.get('monthly_display',''))}"></div>
<div><label>Monthly pence <small>— for MRR est.</small></label><input type=number name=i_monthly_pence value="{e(ti.get('monthly_pence',800))}"></div>
<div><label>Weekly Stripe price id</label><input type=text name=i_weekly_price_id value="{e(ti.get('weekly_price_id',''))}"></div>
<div><label>Weekly display</label><input type=text name=i_weekly_display value="{e(ti.get('weekly_display',''))}"></div>
<div><label>Weekly pence</label><input type=number name=i_weekly_pence value="{e(ti.get('weekly_pence',200))}"></div>
<div><label>Instant min score</label><input type=number name=i_instant_min_score value="{e(ti.get('instant_min_score',90))}"></div>
<div><label>Max channels</label><input type=number name=i_max_channels value="{e(ti.get('max_channels',2))}"></div>
</div>
<label>Blurb</label><input type=text name=i_blurb value="{e(ti.get('blurb',''))}"></div>

<h2>Team tier</h2><div class=card><div class=grid>
<div><label>Monthly Stripe price id</label><input type=text name=t_monthly_price_id value="{e(tt.get('monthly_price_id',''))}"></div>
<div><label>Monthly display</label><input type=text name=t_monthly_display value="{e(tt.get('monthly_display',''))}"></div>
<div><label>Monthly pence</label><input type=number name=t_monthly_pence value="{e(tt.get('monthly_pence',1800))}"></div>
<div><label>Weekly Stripe price id</label><input type=text name=t_weekly_price_id value="{e(tt.get('weekly_price_id',''))}"></div>
<div><label>Weekly display</label><input type=text name=t_weekly_display value="{e(tt.get('weekly_display',''))}"></div>
<div><label>Weekly pence</label><input type=number name=t_weekly_pence value="{e(tt.get('weekly_pence',500))}"></div>
<div><label>Instant min score</label><input type=number name=t_instant_min_score value="{e(tt.get('instant_min_score',75))}"></div>
<div><label>Max channels</label><input type=number name=t_max_channels value="{e(tt.get('max_channels',6))}"></div>
</div>
<label>Blurb</label><input type=text name=t_blurb value="{e(tt.get('blurb',''))}">
<label class=row style="gap:8px;font-weight:400"><input type=checkbox name=t_attach_json {ck(tt.get('attach_json'))}> attach machine-readable JSON</label>
</div>

<button class=btn>Save subscription settings</button>
</form>

<h2>Keys &amp; wiring</h2><div class=card>
<p>Stripe key: <b>{e(stripe_state)}</b> · webhook secret: <b>{'set' if env.get('STRIPE_WEBHOOK_SECRET') else 'not set'}</b>
· Telegram bot: <b>{'set' if env.get('TELEGRAM_BOT_TOKEN') else 'not set'}</b></p>
<p class=dim>Set these on the <a href=/secrets>Secrets</a> page (Stripe/Telegram fields) — they live in config/env, not here.</p>
<p class=dim>Deploy the public site with <span class=mono>systemd/install-public.sh</span>. Full checklist: <span class=mono>docs/legal/compliance-runbook.md</span>.</p>
</div>
"""
    return page("Subscription settings", body, "/subscribers", flash_from(request))


@app.post("/subscriptions")
async def subscriptions_save(request: Request):
    if _subs_guard():
        return RedirectResponse("/subscribers", 303)
    f = await request.form()
    s = load_yaml(CFG / "settings.yaml")
    sub = s.setdefault("subscriptions", {})
    for key in ("brand_name", "site_url", "public_bind", "from_email", "support_email",
                "contact_email", "company_legal_name", "ico_registration"):
        sub[key] = f.get(key, "").strip()
    sub["enabled"] = bool(f.get("enabled"))
    sub["live"] = bool(f.get("live"))
    for key in ("digest_hour_utc", "retention_days_cancelled", "retention_days_unconfirmed",
                "rate_limit_per_min"):
        try:
            sub[key] = int(f.get(key, 0))
        except (TypeError, ValueError):
            pass
    tiers = sub.setdefault("tiers", {})
    for short, name in (("i", "individual"), ("t", "team")):
        t = tiers.setdefault(name, {})
        for cad in ("monthly", "weekly"):
            t[f"{cad}_price_id"] = f.get(f"{short}_{cad}_price_id", "").strip()
            t[f"{cad}_display"] = f.get(f"{short}_{cad}_display", "").strip()
            try:
                t[f"{cad}_pence"] = int(f.get(f"{short}_{cad}_pence", 0))
            except (TypeError, ValueError):
                pass
        t["blurb"] = f.get(f"{short}_blurb", "").strip()
        for nk in ("instant_min_score", "max_channels"):
            try:
                t[nk] = int(f.get(f"{short}_{nk}", 0))
            except (TypeError, ValueError):
                pass
    tiers["team"]["attach_json"] = bool(f.get("t_attach_json"))
    save_yaml(CFG / "settings.yaml", s)
    return RedirectResponse("/subscriptions?m=Saved", 303)




# ----------------------------------------------------------------------------- intel desk
@app.get("/intel", response_class=HTMLResponse)
def intel_page(request: Request):
    return page("Intel Desk", CVE.render_page(), "/intel", flash_from(request))


@app.get("/intel/api/search")
def intel_search(q: str = ""):
    return CVE.search(q, load_env().get("CVEFEED_API_KEY", ""))


@app.get("/intel/api/latest")
def intel_latest():
    key = load_env().get("CVEFEED_API_KEY", "")
    return {
        "shipped": CVE.latest_shipped(ROOT),
        "kev": CVE.latest_kev(key),
    }


@app.get("/intel/api/cve/{cve_id}")
def intel_cve(cve_id: str):
    return CVE.detail(cve_id, load_env().get("CVEFEED_API_KEY", ""))


@app.post("/intel/api/draft")
async def intel_draft(request: Request):
    b = await request.json()
    key = load_env().get("CVEFEED_API_KEY", "")
    row = CVE.detail(str(b.get("id", "")), key)
    if row.get("error"):
        return {"error": row["error"]}
    d = CVE.draft_text(row)
    return MKT.handoff(ROOT, title=d["title"], body=d["body"], url=d["url"],
                       short_text=d["short_text"], publish_now=False, creds=load_env())


# ----------------------------------------------------------------------------- admin center
@app.get("/api/pulse")
def api_pulse():
    return AC.pulse(
        ROOT, load_env(),
        settings=load_yaml(CFG / "settings.yaml"),
        next_run=next_run_iso(),
        targets_ready=bool(configured_targets()),
    )


@app.post("/action/approve-next")
def action_approve_next():
    q = queue_load() or {}
    items = q.get("queue", []) if isinstance(q.get("queue"), list) else []
    if not items:
        return {"message": "Hold queue is empty."}
    p = items[0]
    settings = load_yaml(CFG / "settings.yaml")
    try:
        r = publishers.post_linkedin(p.get("post_text", ""), load_env(), settings,
                                     short_text=p.get("short_text"))
    except Exception as ex:  # noqa: BLE001
        r = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
    if r.get("ok"):
        q["queue"] = items[1:]
        queue_save(q)
        with (STATE / "history.jsonl").open("a") as fh:
            fh.write(json.dumps({
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "title": p.get("title"), "url": p.get("url"), "score": p.get("score"),
                "post_id": r.get("id"), "post_url": r.get("url"),
                "targets": ["linkedin"], "author": "organization", "source": "approve",
            }) + "\n")
        fan = AC.push_alert(p, load_env())
        tail = " (also sent to subscribers)" if fan.get("ok") else " (subscriber fan-out failed)"
        return {"message": f"Posted “{(p.get('title') or '')[:44]}”{tail}"}
    return {"message": f"Post failed — {str(r.get('error'))[:140]}"}


@app.post("/api/manual_post/clear")
def manual_post_clear():
    """Dismiss the 'queued for manual publishing' attention item. This just
    clears the flag — state/manual_post.md was informational (the actual
    post either got published by hand already or the underlying issue is
    resolved); nothing here re-attempts delivery."""
    p = STATE / "manual_post.md"
    if not p.exists():
        return {"message": "Nothing to dismiss."}
    p.unlink()
    return {"message": "Dismissed."}


@app.post("/action/sync-stripe-links")
def action_sync_stripe_links():
    env = load_env()
    wu = (env.get("CTI_WORKER_URL") or "").rstrip("/")
    tok = env.get("CTI_ADMIN_TOKEN")
    if not (wu and tok):
        return {"message": "CTI_WORKER_URL / CTI_ADMIN_TOKEN not set in config/env."}
    try:
        r = requests.post(f"{wu}/admin/stripe/links",
                          headers={"Authorization": f"Bearer {tok}",
                                   "User-Agent": "Mozilla/5.0 (admin-center)"}, timeout=25)
        j = r.json()
    except Exception as ex:  # noqa: BLE001
        return {"message": f"error: {ex}"}
    if isinstance(j, dict) and j.get("error"):
        return {"message": j["error"]}
    n = j.get("updated", 0) if isinstance(j, dict) else 0
    return {"message": f"Stripe links synced — {n} redirect(s) updated to /welcome."}


# ----------------------------------------------------------------------------- marketing desk
@app.get("/marketing", response_class=HTMLResponse)
def marketing_page(request: Request):
    return page("Marketing Desk", MKT.render_page(ROOT, load_env()), "/marketing", flash_from(request))


@app.get("/marketing/api/posts")
def marketing_posts():
    return MKT.posts_payload(ROOT)


@app.get("/marketing/api/kpis")
def marketing_kpis():
    return MKT.kpis(ROOT, load_env())


@app.get("/marketing/api/bundle")
def marketing_bundle():
    return MKT.export_bundle(ROOT)


@app.post("/marketing/api/post/{pid}")
async def marketing_update(pid: str, request: Request):
    f = await request.form()
    fields = {}
    for k in ("status", "scheduled_for", "notes", "edited"):
        if k in f and f.get(k) != "":
            v = f.get(k)
            if k == "scheduled_for" and v and "T" in v and len(v) == 16:
                v = v + ":00Z"
            fields[k] = v
    return MKT.update_post(ROOT, pid, **fields)


@app.post("/marketing/api/kpis")
async def marketing_kpis_save(request: Request):
    f = await request.form()
    d = MKT.store_load(ROOT)
    if f.get("monthly"):
        try:
            d["budget"]["monthly"] = float(f["monthly"])
        except (TypeError, ValueError):
            pass
    if f.get("spend_item") and f.get("spend_amount"):
        try:
            d["budget"]["entries"].append({
                "item": str(f["spend_item"])[:80], "amount": float(f["spend_amount"]),
                "at": datetime.now(timezone.utc).isoformat()})
        except (TypeError, ValueError):
            pass
    for k in ("followers", "impressions_30d", "leads_30d", "cac", "mrr"):
        if f.get(k) not in (None, ""):
            d["kpis"][k] = str(f[k])
    MKT.store_save(ROOT, d)
    return {"ok": True}




@app.post("/marketing/api/compose")
async def marketing_compose(request: Request):
    f = await request.form()
    sched = f.get("schedule") or ""
    if sched and "T" in sched and len(sched) == 16:
        sched = sched + ":00Z"
    return MKT.handoff(
        ROOT,
        title=f.get("title") or "",
        body=f.get("body") or "",
        url=f.get("url") or "",
        short_text=f.get("short_text") or "",
        schedule=sched,
        publish_now=(f.get("publish_now") in ("1", "true", "on", "yes")),
        creds=load_env(),
    )


# ----------------------------------------------------------------------------- main
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("CTI_UI_PORT", "8799"))
    host = os.getenv("CTI_UI_HOST", "127.0.0.1")
    print(f"SecurityCyber Admin Center -> http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
