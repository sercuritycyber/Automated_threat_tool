#!/usr/bin/env python3
"""Dark-web Desk — Admin Center view of the onion collector (darkweb/).

Read-mostly oversight plus manual override: the collector normally runs on a
timer, but from here a human can check Tor, kick a collect/match on demand,
review what the matcher flagged, and enable/disable a site.

It reads the collector's SQLite DB and site registry directly, and launches
`darkweb/harvest.py` as a background subprocess for the write actions — the
panel never blocks on Tor. Nothing here posts or publishes.
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DW = ROOT / "darkweb"
sys.path.insert(0, str(DW))

import yaml  # noqa: E402

SITES = DW / "sites.yaml"
WATCHLIST = DW / "watchlist.yaml"
HARVEST = DW / "harvest.py"
RUNLOG = ROOT / "state" / "darkweb_desk.log"

TABS = [("/darkweb", "Overview"), ("/darkweb/hits", "Hits"),
        ("/darkweb/sites", "Sites"), ("/darkweb/log", "Activity")]

CSS = """<style>
.dw-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px}
.dw-tabs a{padding:6px 12px;border:1px solid var(--line);border-radius:8px;color:var(--dim);font-size:13px}
.dw-tabs a.on{border-color:var(--accent);color:#fff;background:rgba(59,130,246,.16)}
.dw-hit{border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:10px;background:var(--panel)}
.dw-hit .ctx{color:var(--dim);font-size:12.5px;margin-top:6px;word-break:break-word}
.dw-k{font-family:ui-monospace,Menlo,monospace;font-size:12px}
</style>"""


def e(v) -> str:
    return html.escape("" if v is None else str(v))


def defang(s: str) -> str:
    s = str(s)
    return (s.replace("http://", "hxxp://").replace("https://", "hxxps://")
             .replace(".onion", "[.]onion"))


def _load(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        return {}
    except Exception as ex:
        return {"_error": str(ex)}


def _settings() -> dict:
    cfg = dict(_load(SITES).get("darkweb") or {})
    cfg.setdefault("db", "state/darkweb.db")
    cfg.setdefault("proxy_host", "127.0.0.1")
    cfg.setdefault("proxy_port", 9050)
    return cfg


def _db_path() -> Path:
    p = Path(_settings()["db"]).expanduser()
    return p if p.is_absolute() else (ROOT / p)


def _store():
    from store import Store  # local import; may be unavailable if not deployed
    return Store(_db_path())


def _running() -> bool:
    lk = _db_path().with_suffix(_db_path().suffix + ".lock")
    if not lk.exists():
        return False
    return (time.time() - lk.stat().st_mtime) < 3600


def _configured() -> bool:
    return SITES.exists() and bool(_load(SITES).get("sites"))


# --------------------------------------------------------------------------- #
#  Actions (background subprocess) — the override controls
# --------------------------------------------------------------------------- #
SERVICE = "cti-darkweb.service"


def _log(line: str) -> None:
    RUNLOG.parent.mkdir(parents=True, exist_ok=True)
    with RUNLOG.open("a") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} : {line} ===\n")


def _start_service() -> bool:
    """Trigger the hardened collector unit so the fetch/parse runs sandboxed
    (no shell, locked-down user). Returns True if a start was accepted."""
    for cmd in (["systemctl", "start", "--no-block", SERVICE],
                ["sudo", "-n", "systemctl", "start", "--no-block", SERVICE]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                _log(f"started {SERVICE} via: {' '.join(cmd)}")
                return True
        except Exception:
            continue
    return False


def _spawn(subcmd: list[str]) -> None:
    """Fallback for hosts without the systemd unit (dev/laptop). The collector
    core still spawns no shell of its own; this only launches the interpreter."""
    _log(f"direct spawn: harvest.py {' '.join(subcmd)}")
    with RUNLOG.open("a") as log:
        subprocess.Popen(
            [sys.executable, str(HARVEST), *subcmd],
            stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT),
            start_new_session=True,
        )


def action(kind: str) -> str:
    if not HARVEST.exists():
        return "collector not deployed on this host"
    if kind == "collect":
        if _running():
            return "a collect is already running"
        if _start_service():
            return "collect started under the hardened service (sandboxed)"
        _spawn(["run", "--json", "state/darkweb_hits.jsonl"])
        return "collect + match started (direct — no systemd unit on this host)"
    if kind == "check":
        _spawn(["check"])
        return "Tor check started — see Activity"
    if kind == "match":
        _spawn(["match"])
        return "match started"
    return "unknown action"


def toggle_site(name: str) -> str:
    data = _load(SITES)
    for s in data.get("sites") or []:
        if (s.get("name") or s.get("base")) == name:
            s["enabled"] = not s.get("enabled", True)
            SITES.write_text(yaml.safe_dump(data, sort_keys=False))
            return f"{name} {'enabled' if s['enabled'] else 'disabled'}"
    return f"site {name} not found"


def mark_reviewed(hit_id: int) -> str:
    try:
        with _store() as st:
            st.db.execute("UPDATE hits SET reviewed=1 WHERE id=?", (hit_id,))
            st.db.commit()
        return "hit marked reviewed"
    except Exception as ex:
        return f"error: {ex}"


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
def _tabs(active: str) -> str:
    return CSS + '<div class="dw-tabs">' + "".join(
        f'<a href="{p}" class="{"on" if p == active else ""}">{e(label)}</a>'
        for p, label in TABS) + "</div>"


def _controls() -> str:
    busy = " disabled" if _running() else ""
    running = '<span class="pill warn">collect running</span>' if _running() else ""
    return f"""<div class="card"><div class="row">
      <form method=post action="/darkweb/act" style="display:inline">
        <input type=hidden name=do value=check>
        <button class="btn ghost" type=submit>Check Tor</button></form>
      <form method=post action="/darkweb/act" style="display:inline">
        <input type=hidden name=do value=collect>
        <button class="btn" type=submit{busy}>Collect now</button></form>
      <form method=post action="/darkweb/act" style="display:inline">
        <input type=hidden name=do value=match>
        <button class="btn ghost" type=submit>Re-match</button></form>
      {running}
    </div>
    <div class="dim" style="margin-top:8px;font-size:12px">Collection runs over Tor and can take
    a few minutes; watch the Activity tab.</div></div>"""


def render_overview() -> str:
    if not _configured():
        return _tabs("/darkweb") + """<div class="card">
        <b>Not configured yet.</b><p class="dim">Copy <span class="dw-k">darkweb/sites.yaml.example</span>
        to <span class="dw-k">darkweb/sites.yaml</span> and add onion addresses you have verified,
        plus your honeypot IOC feed paths. See <span class="dw-k">darkweb/README.md</span>.</p></div>"""
    try:
        with _store() as st:
            stats = st.stats()
            recent = st.hits()[:5]
    except Exception as ex:
        stats, recent = {}, []
        err = f'<div class="card"><span class="pill bad">DB error</span> {e(ex)}</div>'
    else:
        err = ""
    tiles = "".join(
        f'<div class="tile"><div class="n">{e(stats.get(k, 0))}</div><div class="l">{e(lab)}</div></div>'
        for k, lab in [("sites", "sites"), ("pages", "pages"), ("snapshots", "snapshots"),
                       ("hits", "hits"), ("unreviewed_hits", "unreviewed")])
    hits_html = "".join(_hit_card(h, compact=True) for h in recent) or \
        '<p class="dim">No hits yet.</p>'
    return (_tabs("/darkweb") + err + _controls()
            + f'<div class="tiles">{tiles}</div>'
            + '<h2>Latest hits</h2>' + hits_html
            + '<p class="dim" style="font-size:12px">Full list on the Hits tab.</p>')


def _hit_card(h, compact: bool = False) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(h["found_at"]))
    kind = h["kind"]
    pill = {"watchlist": "warn"}.get(kind, "bad" if kind.startswith("honeypot") else "dim")
    review = "" if h["reviewed"] else (
        f'<form method=post action="/darkweb/act" style="display:inline;float:right">'
        f'<input type=hidden name=do value=review><input type=hidden name=hit value="{h["id"]}">'
        f'<button class="btn ghost" type=submit style="padding:3px 10px;font-size:12px">Mark reviewed</button></form>')
    seen = '' if not h["reviewed"] else '<span class="pill dim">reviewed</span> '
    return f"""<div class="dw-hit">
      {review}{seen}<span class="pill {pill}">{e(kind)}</span>
      <b class="dw-k">{e(h['indicator'])}</b>
      <span class="dim" style="font-size:12px"> · {e(ts)} · {e(h['site'])}</span>
      <div class="dw-k" style="font-size:11.5px;margin-top:4px">{e(defang(h['url']))}</div>
      <div class="ctx">…{e(h['context'])}…</div></div>"""


def render_hits() -> str:
    if not _configured():
        return render_overview()
    try:
        with _store() as st:
            hits = st.hits()
    except Exception as ex:
        return _tabs("/darkweb/hits") + f'<div class="card"><span class="pill bad">DB error</span> {e(ex)}</div>'
    if not hits:
        return _tabs("/darkweb/hits") + '<div class="card dim">No hits recorded yet. Run a collect, then a match.</div>'
    body = "".join(_hit_card(h) for h in hits)
    return _tabs("/darkweb/hits") + f'<p class="dim">{len(hits)} hits, newest first.</p>' + body


def render_sites() -> str:
    data = _load(SITES)
    sites = data.get("sites") or []
    if not sites:
        return render_overview()
    last: dict[str, tuple] = {}
    try:
        with _store() as st:
            for r in st.db.execute(
                "SELECT site, MAX(last_seen) ls, last_status FROM pages GROUP BY site"):
                last[r["site"]] = (r["ls"], r["last_status"])
    except Exception:
        pass
    rows = ""
    for s in sites:
        name = s.get("name") or s.get("base", "?")
        on = s.get("enabled", True)
        ls, status = last.get(name, (None, None))
        seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(ls)) if ls else "never"
        pill = '<span class="pill good">enabled</span>' if on else '<span class="pill dim">disabled</span>'
        auth = f'<span class="pill dim">auth: {e(s.get("cookie_env"))}</span>' if s.get("cookie_env") else ""
        rows += f"""<tr><td>{pill}<br>{auth}</td>
          <td><b>{e(name)}</b><br><span class="dw-k" style="font-size:11px">{e(defang(s.get('base','')))}</span></td>
          <td>{len(s.get('paths') or ['/'])} paths</td>
          <td>{e(seen)}<br><span class="dim">{e(status) if status else ''}</span></td>
          <td><form method=post action="/darkweb/act">
            <input type=hidden name=do value=toggle><input type=hidden name=site value="{e(name)}">
            <button class="btn ghost" type=submit style="padding:4px 10px;font-size:12px">{'Disable' if on else 'Enable'}</button>
          </form></td></tr>"""
    return (_tabs("/darkweb/sites")
            + '<p class="dim">Addresses are edited in <span class="dw-k">darkweb/sites.yaml</span> '
              '(sensitive — phishing-cloned; not editable here). Enable/disable is available for override.</p>'
            + f'<table><tr><th>State</th><th>Site</th><th>Scope</th><th>Last seen</th><th></th></tr>{rows}</table>')


def render_log() -> str:
    try:
        text = RUNLOG.read_text()[-8000:]
    except FileNotFoundError:
        text = "(no activity yet)"
    return _tabs("/darkweb/log") + _controls() + f"<pre>{e(text)}</pre>"
