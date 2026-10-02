#!/usr/bin/env python3
"""Dark-web Desk — Admin Center view of the onion-address aggregator (darkweb/).

Oversight + manual override for a tool that CRAWLS index/search sources and
AGGREGATES a list of valid .onion addresses (BreachForums/XSS/Dread/…) for a
human to investigate. It does not scrape forum content.

CSAM is blocked fail-closed in the collector and can't be disabled; this desk
only ever shows non-blocked addresses and a count of how many were CSAM-blocked.

It reads the collector's SQLite DB directly and launches the hardened
`cti-darkweb.service` for the write actions (crawl/checkup). Nothing here posts.
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

SOURCES = DW / "sources.yaml"
HARVEST = DW / "harvest.py"
RUNLOG = ROOT / "state" / "darkweb_desk.log"
SERVICE = "cti-darkweb.service"

TABS = [("/darkweb", "Overview"), ("/darkweb/addresses", "Addresses"),
        ("/darkweb/sources", "Sources"), ("/darkweb/wallets", "Wallets"),
        ("/darkweb/log", "Activity")]

CSS = """<style>
.dw-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px}
.dw-tabs a{padding:6px 12px;border:1px solid var(--line);border-radius:8px;color:var(--dim);font-size:13px}
.dw-tabs a.on{border-color:var(--accent);color:#fff;background:rgba(59,130,246,.16)}
.dw-k{font-family:ui-monospace,Menlo,monospace;font-size:12px;word-break:break-all}
.dw-safe{border:1px solid rgba(63,185,80,.4);background:rgba(63,185,80,.08);border-radius:10px;padding:10px 14px;margin-bottom:14px;font-size:13px}
.dw-alert{border:1px solid var(--accent);background:rgba(59,130,246,.12);border-radius:10px;padding:12px 14px;margin-bottom:14px}
.dw-alert b{color:#fff}
</style>"""


def e(v) -> str:
    return html.escape("" if v is None else str(v))


def defang(s: str) -> str:
    return str(s).replace(".onion", "[.]onion")


def _load(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        return {}
    except Exception as ex:
        return {"_error": str(ex)}


def _settings() -> dict:
    cfg = dict(_load(SOURCES).get("darkweb") or {})
    cfg.setdefault("db", "state/onions.db")
    cfg.setdefault("proxy_host", "127.0.0.1")
    cfg.setdefault("proxy_port", 9050)
    return cfg


def _db_path() -> Path:
    p = Path(_settings()["db"]).expanduser()
    return p if p.is_absolute() else (ROOT / p)


def _store():
    from store import Store
    return Store(_db_path())


def _running() -> bool:
    lk = _db_path().with_suffix(_db_path().suffix + ".lock")
    return lk.exists() and (time.time() - lk.stat().st_mtime) < 3600


def _configured() -> bool:
    return SOURCES.exists() and bool(_load(SOURCES).get("sources"))


# --------------------------------------------------------------------------- #
#  Actions
# --------------------------------------------------------------------------- #
def _log(line: str) -> None:
    RUNLOG.parent.mkdir(parents=True, exist_ok=True)
    with RUNLOG.open("a") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} : {line} ===\n")


def _start_service() -> bool:
    """Trigger the hardened --user collector unit so the run is sandboxed
    (NoExecPaths=/ — can't exec a shell). Returns True if a start was accepted."""
    for cmd in (["systemctl", "--user", "start", "--no-block", SERVICE],
                ["systemctl", "start", "--no-block", SERVICE]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                _log(f"started {SERVICE} via: {' '.join(cmd)}")
                return True
        except Exception:
            continue
    return False


def _spawn(subcmd: list[str]) -> None:
    _log(f"direct spawn: harvest.py {' '.join(subcmd)}")
    with RUNLOG.open("a") as log:
        subprocess.Popen([sys.executable, str(HARVEST), *subcmd],
                         stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT),
                         start_new_session=True)


def action(kind: str) -> str:
    if not HARVEST.exists():
        return "collector not deployed on this host"
    if kind == "crawl":
        if _running():
            return "a run is already in progress"
        if _start_service():
            return "crawl + checkup started under the hardened service (sandboxed)"
        _spawn(["run", "--json", "state/onions.jsonl"])
        return "crawl + checkup started (direct — no systemd unit here)"
    if kind == "checkup":
        _spawn(["checkup"])
        return "liveness checkup started"
    if kind == "check":
        _spawn(["check"])
        return "Tor check started — see Activity"
    return "unknown action"


def add_address(raw: str, tag: str = "") -> str:
    """Manually track an onion you found. Safety-gated; stored without fetching
    (validate it with 'Re-check liveness', which runs the checkup)."""
    import re
    m = re.search(r"\b([a-z2-7]{56}\.onion)\b", (raw or "").strip(), re.I)
    if not m:
        return "no valid v3 .onion address found in that input"
    addr = m.group(1).lower()
    try:
        import safety as safetymod
        sf = safetymod.load((_load(SOURCES).get("safety")), DW)
        if sf.address_blocked(addr):
            return "address is on the CSAM blocklist — refused"
        from store import Store
        with Store(_db_path()) as st:
            if st.is_blocked(addr):
                return "address was previously CSAM-blocked — refused"
            tags = [tag] if tag else []
            new = st.add_sighting(addr, "manual", tag or "manually added",
                                  tag or "manually added", tags)
        return (f"{'added' if new else 'already tracked'}: {addr[:16]}… "
                f"— click 'Re-check liveness' to validate")
    except Exception as ex:
        return f"error: {ex}"


def ack_alerts() -> str:
    try:
        with _store() as st:
            return f"acknowledged {st.ack_all_alerts()} alert(s)"
    except Exception as ex:
        return f"error: {ex}"


def toggle_source(name: str) -> str:
    data = _load(SOURCES)
    for s in data.get("sources") or []:
        if (s.get("name") or s.get("url")) == name:
            s["enabled"] = not s.get("enabled", True)
            SOURCES.write_text(yaml.safe_dump(data, sort_keys=False))
            return f"{name} {'enabled' if s['enabled'] else 'disabled'}"
    return f"source {name} not found"


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
def _tabs(active: str) -> str:
    return CSS + '<div class="dw-tabs">' + "".join(
        f'<a href="{p}" class="{"on" if p == active else ""}">{e(label)}</a>'
        for p, label in TABS) + "</div>"


def _alert_banner() -> str:
    try:
        with _store() as st:
            rows = st.alerts(unacked_only=True)
    except Exception:
        return ""
    if not rows:
        return ""
    items = ""
    for r in rows[:12]:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))
        tag = f' <span class="pill good">{e(r["tag"])}</span>' if r["tag"] else ""
        title = f' — {e(r["title"])}' if r["title"] else ""
        items += (f'<div style="margin-top:6px"><b>BACK UP</b>{tag} '
                  f'<span class="dw-k">{e(defang(r["address"]))}</span>{title} '
                  f'<span class="dim" style="font-size:11px">· {e(ts)}</span></div>')
    return (f'<div class="dw-alert">🔔 <b>{len(rows)} tracked site(s) came back online</b>'
            f'{items}<form method=post action="/darkweb/act" style="margin-top:10px">'
            f'<input type=hidden name=do value=ack>'
            f'<button class="btn ghost" type=submit style="padding:4px 12px;font-size:12px">Acknowledge all</button>'
            f'</form></div>')


def _safe_banner(stats: dict) -> str:
    n = stats.get("blocked_csam", 0)
    return (f'<div class="dw-safe">🛡️ CSAM filter active (fail-closed, cannot be disabled). '
            f'<b>{e(n)}</b> address(es) blocked and never stored or shown.</div>')


def _controls() -> str:
    busy = " disabled" if _running() else ""
    running = '<span class="pill warn">run in progress</span>' if _running() else ""
    return f"""<div class="card"><div class="row">
      <form method=post action="/darkweb/act" style="display:inline">
        <input type=hidden name=do value=check><button class="btn ghost" type=submit>Check Tor</button></form>
      <form method=post action="/darkweb/act" style="display:inline">
        <input type=hidden name=do value=crawl><button class="btn" type=submit{busy}>Crawl + validate</button></form>
      <form method=post action="/darkweb/act" style="display:inline">
        <input type=hidden name=do value=checkup><button class="btn ghost" type=submit>Re-check liveness</button></form>
      {running}</div>
      <div class="dim" style="margin-top:8px;font-size:12px">Runs over Tor; can take minutes. Watch Activity.</div></div>"""


def render_overview() -> str:
    if not _configured():
        return _tabs("/darkweb") + """<div class="card"><b>Not configured yet.</b>
        <p class="dim">Copy <span class="dw-k">darkweb/sources.yaml.example</span> to
        <span class="dw-k">darkweb/sources.yaml</span> and enable discovery sources.
        See <span class="dw-k">darkweb/README.md</span>.</p></div>"""
    try:
        with _store() as st:
            stats = st.stats()
            recent = st.listing(status="up")[:8]
    except Exception as ex:
        return _tabs("/darkweb") + f'<div class="card"><span class="pill bad">DB error</span> {e(ex)}</div>'
    tiles = "".join(
        f'<div class="tile"><div class="n">{e(stats.get(k, 0))}</div><div class="l">{e(lab)}</div></div>'
        for k, lab in [("onions", "addresses"), ("up", "live"), ("down", "down"),
                       ("unchecked", "unchecked"), ("tagged", "tagged"), ("sources", "sources")])
    live = "".join(_addr_row(r) for r in recent) or '<tr><td class="dim" colspan=4>No live addresses yet — run crawl.</td></tr>'
    return (_tabs("/darkweb") + _alert_banner() + _safe_banner(stats) + _controls()
            + f'<div class="tiles">{tiles}</div>'
            + '<h2>Live addresses</h2><table><tr><th>Status</th><th>Address</th><th>Tags</th><th>Title</th></tr>'
            + live + '</table><p class="dim" style="font-size:12px">Full list on the Addresses tab.</p>')


def _addr_row(r) -> str:
    st = r["status"]
    pill = {"up": "good", "down": "bad", "unknown": "dim"}.get(st, "dim")
    tags = e(r["tags"] or "")
    return (f'<tr><td><span class="pill {pill}">{e(st)}</span></td>'
            f'<td class="dw-k">{e(defang(r["address"]))}</td>'
            f'<td>{tags}</td><td class="dim">{e(r["title"] or "")}</td></tr>')


def render_addresses() -> str:
    if not _configured():
        return render_overview()
    try:
        with _store() as st:
            stats = st.stats()
            rows = st.listing()
    except Exception as ex:
        return _tabs("/darkweb/addresses") + f'<div class="card"><span class="pill bad">DB error</span> {e(ex)}</div>'
    body = "".join(_addr_full_row(r) for r in rows)
    table = ('<div class="card dim">No addresses yet. Run a crawl, or add one you found below.</div>'
             if not rows else
             f'<p class="dim">{len(rows)} addresses (CSAM-blocked ones are never listed).</p>'
             '<table><tr><th>Status</th><th>Address</th><th>Tags</th><th>Sources</th><th>Title</th></tr>'
             + body + '</table>')
    return _tabs("/darkweb/addresses") + _alert_banner() + _safe_banner(stats) + _add_form() + table


def _add_form() -> str:
    return """<div class="card"><b>Track an onion you found</b>
      <form method=post action="/darkweb/add" class="row" style="margin-top:8px;align-items:flex-end">
        <div style="flex:2;min-width:240px"><label>Address or URL</label>
          <input type=text name=address placeholder="xxxxxxxx…d.onion"></div>
        <div style="flex:1;min-width:120px"><label>Tag <small>(site name)</small></label>
          <input type=text name=tag placeholder="e.g. sh-psa"></div>
        <button class="btn" type=submit>Add</button>
      </form>
      <div class="dim" style="font-size:12px;margin-top:6px">Stored safely (CSAM-gated), no fetch.
      Then hit <b>Re-check liveness</b> to validate up/down.</div></div>"""


def _addr_full_row(r) -> str:
    st = r["status"]
    pill = {"up": "good", "down": "bad", "unknown": "dim"}.get(st, "dim")
    return (f'<tr><td><span class="pill {pill}">{e(st)}</span></td>'
            f'<td class="dw-k">{e(defang(r["address"]))}</td>'
            f'<td>{e(r["tags"] or "")}</td>'
            f'<td class="dim" style="font-size:11px">{e(r["sources"] or "")}</td>'
            f'<td class="dim">{e(r["title"] or "")}</td></tr>')


def render_sources() -> str:
    data = _load(SOURCES)
    sources = data.get("sources") or []
    targets = ", ".join(str(t) for t in (data.get("targets") or []))
    if not sources:
        return render_overview()
    rows = ""
    for s in sources:
        name = s.get("name") or s.get("url", "?")
        on = s.get("enabled", True)
        pill = '<span class="pill good">enabled</span>' if on else '<span class="pill dim">disabled</span>'
        rows += f"""<tr><td>{pill}</td><td><b>{e(name)}</b></td>
          <td class="dw-k" style="font-size:11px">{e(defang(s.get('url','')))}</td>
          <td><form method=post action="/darkweb/act">
            <input type=hidden name=do value=toggle><input type=hidden name=src value="{e(name)}">
            <button class="btn ghost" type=submit style="padding:4px 10px;font-size:12px">{'Disable' if on else 'Enable'}</button>
          </form></td></tr>"""
    return (_tabs("/darkweb/sources")
            + f'<div class="card">Targets: <b>{e(targets) or "(none)"}</b></div>'
            + '<p class="dim">Sources are crawled for addresses. Edit URLs in '
              '<span class="dw-k">darkweb/sources.yaml</span>; enable/disable here.</p>'
            + f'<table><tr><th>State</th><th>Source</th><th>URL</th><th></th></tr>{rows}</table>')


def _btc(sats) -> str:
    try:
        return f"{int(sats) / 100_000_000:.6f} BTC"
    except Exception:
        return "?"


def ack_chain_alerts() -> str:
    try:
        import chainwatch
        cfg = chainwatch._settings(chainwatch._load_cfg(DW / "chainwatch.yaml"))
        with chainwatch.Store(chainwatch._resolve(cfg["db"])) as st:
            return f"acknowledged {st.ack_all()} wallet alert(s)"
    except Exception as ex:
        return f"error: {ex}"


def render_wallets() -> str:
    try:
        import chainwatch
    except Exception as ex:
        return _tabs("/darkweb/wallets") + f'<div class="card"><span class="pill bad">unavailable</span> {e(ex)}</div>'
    if not (DW / "chainwatch.yaml").exists():
        return _tabs("/darkweb/wallets") + """<div class="card"><b>Internal blockchain watcher — not configured.</b>
        <p class="dim">Internal only; never posted publicly. Copy
        <span class="dw-k">darkweb/chainwatch.yaml.example</span> to
        <span class="dw-k">darkweb/chainwatch.yaml</span>, then add a verified address:<br>
        <span class="dw-k">python3 darkweb/chainwatch.py add &lt;btc-addr&gt; --label shinyhunters-warchest --threshold-btc 0.25</span></p>
        <p class="dim">BTC only (mempool.space over Tor). Monero can't be watched on-chain.</p></div>"""
    try:
        cfg = chainwatch._settings(chainwatch._load_cfg(DW / "chainwatch.yaml"))
        with chainwatch.Store(chainwatch._resolve(cfg["db"])) as st:
            wallets = st.wallets()
            alerts = st.alerts(unacked_only=True)
            recent = st.alerts(unacked_only=False)[:15]
    except Exception as ex:
        return _tabs("/darkweb/wallets") + f'<div class="card"><span class="pill bad">DB error</span> {e(ex)}</div>'
    banner = ""
    if alerts:
        items = "".join(
            f'<div style="margin-top:6px"><b>{e(a["kind"].upper())}</b> '
            f'<span class="pill bad">{e(_btc(a["amount_sats"]))}</span> '
            f'{e(a["label"])} — <span class="dim">{e(a["detail"])}</span></div>' for a in alerts[:12])
        banner = (f'<div class="dw-alert">💸 <b>{len(alerts)} wallet movement alert(s)</b>{items}'
                  f'<form method=post action="/darkweb/act" style="margin-top:10px">'
                  f'<input type=hidden name=do value=ackchain>'
                  f'<button class="btn ghost" type=submit style="padding:4px 12px;font-size:12px">Acknowledge all</button></form></div>')
    if not wallets:
        wrows = '<tr><td class="dim" colspan=5>No wallets watched. Add via CLI (see Wallets setup).</td></tr>'
    else:
        wrows = "".join(
            f'<tr><td><b>{e(w["label"] or "")}</b></td><td class="dw-k">{e(w["address"])}</td>'
            f'<td>{e(_btc(w["balance_sats"]) if w["balance_sats"] is not None else "?")}</td>'
            f'<td>{e(w["tx_count"] if w["tx_count"] is not None else "?")}</td>'
            f'<td class="dim">thr {e(_btc(w["threshold_sats"]))}</td></tr>' for w in wallets)
    arows = "".join(
        f'<tr><td>{e(time.strftime("%Y-%m-%d %H:%M", time.localtime(a["created_at"])))}</td>'
        f'<td>{e(a["kind"])}</td><td>{e(a["label"])}</td><td class="dim">{e(a["detail"])}</td></tr>'
        for a in recent) or '<tr><td class="dim" colspan=4>No alerts yet.</td></tr>'
    return (_tabs("/darkweb/wallets") + banner
            + '<div class="dw-safe">🔒 Internal only — wallet data and alerts are never posted publicly.</div>'
            + '<h2>Watched wallets</h2><table><tr><th>Label</th><th>Address</th><th>Balance</th><th>Txs</th><th></th></tr>'
            + wrows + '</table>'
            + '<h2>Recent movement alerts</h2><table><tr><th>When</th><th>Kind</th><th>Label</th><th>Detail</th></tr>'
            + arows + '</table>')


def render_log() -> str:
    try:
        text = RUNLOG.read_text()[-8000:]
    except FileNotFoundError:
        text = "(no activity yet)"
    return _tabs("/darkweb/log") + _controls() + f"<pre>{e(text)}</pre>"
