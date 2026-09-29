#!/usr/bin/env python3
"""Honeynet Desk — the Admin Center view of the separate `honeyintel` service.

honeyintel (its own repo at ~/honeyintel, own DB, own timers) turns our honeypot
telemetry in Sentinel into first-party intel. This module only READS its latest
reports/<date>/report.json and serves the artefacts; it never runs the analysis
inline and never publishes. The one write action is asking systemd to start
honeyintel-report.service (collect + analyse) in the background.
"""
from __future__ import annotations

import html
import json
import os
import subprocess
from pathlib import Path

HI_DIR = Path(os.environ.get("HONEYINTEL_DIR", str(Path.home() / "honeyintel")))
REPORTS = HI_DIR / "reports"
ARTEFACTS = {"report.md": "text/markdown", "iocs.csv": "text/csv", "creds.csv": "text/csv",
             "blocklist.txt": "text/plain", "social.txt": "text/plain", "report.json": "application/json"}
STAGES = ["Reconnaissance", "Weaponization", "Delivery", "Exploitation",
          "Installation", "Command & Control", "Actions on Objectives"]
ROLE_TEXT = {"loader": "installed a payload", "shell-access": "got a shell, ran commands",
             "rdp-bruteforce": "Windows RDP/SMB password spraying",
             "brute-forcer": "SSH/Telnet guessing, no shell", "scanner": "connected / banner-grabbed only"}
_CACHE: dict[str, tuple[float, dict]] = {}

TABS = [("/honeynet", "Overview"), ("/honeynet/campaigns", "Campaigns"), ("/honeynet/iocs", "IOC feed"),
        ("/honeynet/credentials", "Credentials"), ("/honeynet/windows", "Windows"),
        ("/honeynet/report", "Report & draft")]

CSS = """<style>
.hn-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px}
.hn-tabs a{padding:6px 12px;border:1px solid var(--line);border-radius:8px;color:var(--dim);font-size:13px}
.hn-tabs a.on{border-color:var(--accent);color:#fff;background:rgba(59,130,246,.16)}
.hn-bars{display:grid;gap:4px}
.hn-bar{display:grid;grid-template-columns:170px 1fr 92px;gap:10px;align-items:center;font-size:12.5px}
.hn-bar .trk{height:14px;background:var(--panel2);border-radius:4px;overflow:hidden}
.hn-bar .fill{height:100%;background:var(--accent);border-radius:0 4px 4px 0;min-width:2px}
.hn-bar .v{text-align:right;color:var(--dim);font-variant-numeric:tabular-nums;white-space:nowrap}
.hn-cols{display:flex;align-items:flex-end;gap:2px;height:120px;padding-top:6px}
.hn-cols .c{flex:1;background:var(--accent);border-radius:4px 4px 0 0;min-height:2px}
.hn-cols .c:hover,.hn-bar:hover .fill{filter:brightness(1.25)}
.hn-axis{display:flex;gap:2px;font-size:10.5px;color:var(--faint);margin-top:4px}
.hn-axis span{flex:1;text-align:center;overflow:hidden;white-space:nowrap}
.hn-cmd{font-size:12px;max-height:260px}
details.hn{border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:10px;background:var(--panel)}
details.hn summary{cursor:pointer;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
@media(max-width:640px){.hn-bar{grid-template-columns:104px 1fr 104px;font-size:12px}}
</style>"""


def e(v) -> str:
    return html.escape("" if v is None else str(v))


def defang(s: str) -> str:
    s = str(s)
    if s.count(".") == 3 and s.replace(".", "").isdigit():
        a, _, b = s.rpartition(".")
        return f"{a}[.]{b}"
    return s.replace("http://", "hxxp://").replace("https://", "hxxps://")


def latest_dir() -> Path | None:
    if not REPORTS.is_dir():
        return None
    dirs = sorted(p for p in REPORTS.iterdir() if (p / "report.json").exists())
    return dirs[-1] if dirs else None


def load() -> tuple[dict | None, Path | None]:
    d = latest_dir()
    if not d:
        return None, None
    f = d / "report.json"
    mt = f.stat().st_mtime
    hit = _CACHE.get(str(f))
    if hit and hit[0] == mt:
        return hit[1], d
    data = json.loads(f.read_text())
    _CACHE.clear()
    _CACHE[str(f)] = (mt, data)
    return data, d


def artefact(name: str) -> tuple[Path, str] | None:
    d = latest_dir()
    if not d or name not in ARTEFACTS or not (d / name).exists():
        return None
    return d / name, ARTEFACTS[name]


def refresh() -> str:
    """Start collect+analyse in the background via systemd (never publishes)."""
    try:
        subprocess.run(["systemctl", "--user", "start", "--no-block", "honeyintel-report.service"],
                       check=True, capture_output=True, text=True, timeout=10)
        return "honeyintel refresh started - reload in a minute or two"
    except Exception as ex:  # noqa: BLE001
        return f"could not start honeyintel-report.service: {ex}"


def service_state() -> dict:
    out = {}
    for unit in ("honeyintel-collect.timer", "honeyintel-report.timer", "honeyintel-report.service"):
        try:
            r = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, timeout=5)
            out[unit] = r.stdout.strip() or "unknown"
        except Exception:  # noqa: BLE001
            out[unit] = "unknown"
    return out


# ------------------------------------------------------------------ building blocks
def tabs(active: str) -> str:
    return CSS + '<div class=hn-tabs>' + "".join(
        f'<a href="{p}" class="{"on" if p == active else ""}">{e(l)}</a>' for p, l in TABS) + "</div>"


def tile(n, label, cls="") -> str:
    v = f"{n:,}" if isinstance(n, int) else e(n)
    return f'<div class="tile {cls}"><div class=n>{v}</div><div class=l>{e(label)}</div></div>'


def hbars(rows: list[tuple[str, int, str]], total: int) -> str:
    """Single-series horizontal bars; value always printed, tooltip on hover."""
    mx = max((v for _, v, _ in rows), default=0) or 1
    out = []
    for label, v, tip in rows:
        pct = 100 * v / mx
        share = f"{100 * v / total:.1f}%" if total else ""
        out.append(f'<div class=hn-bar title="{e(label)}: {v:,} ({share}){(" - " + e(tip)) if tip else ""}">'
                   f'<span>{e(label)}</span><div class=trk><div class=fill style="width:{pct:.2f}%"></div></div>'
                   f'<span class=v>{v:,} · {share}</span></div>')
    return '<div class=hn-bars>' + "".join(out) + "</div>"


def cols(series: dict[str, int]) -> str:
    if not series:
        return '<p class=dim>No data.</p>'
    mx = max(series.values()) or 1
    bars = "".join(f'<div class=c style="height:{100 * v / mx:.1f}%" title="{e(k)}: {v:,} sessions"></div>'
                   for k, v in series.items())
    axis = "".join(f"<span>{e(k[5:])}</span>" for k in series)
    return f'<div class=hn-cols role=img aria-label="Sessions per day">{bars}</div><div class=hn-axis>{axis}</div>'


def empty() -> str:
    return (tabs("/honeynet") + '<div class=card><h3>No honeyintel report yet</h3>'
            f'<p class=dim>Expected reports under <span class=mono>{e(REPORTS)}</span>. '
            'Run <span class=mono>~/honeyintel/bin/honeyintel run</span> or use Refresh.</p>'
            '<form method=post action="/honeynet/refresh"><button class=btn>Refresh now</button></form></div>')


def header(a: dict, d: Path) -> str:
    w = a["window"]
    last = (a.get("last_sensor_event") or "")[:16]
    stale = last and last < w["end"][:16]
    pill = ('<span class="pill warn">sensors offline — last event ' + e(last) + 'Z</span>') if stale else \
           '<span class="pill good">sensors reporting</span>'
    return (f'<div class=row style="justify-content:space-between;margin-bottom:12px">'
            f'<div class=dim>Window {e(w["start"][:10])} → {e(w["end"][:10])} ({w["days"]} days) · '
            f'report <span class=mono>{e(d.name)}</span> · {pill}</div>'
            f'<form method=post action="/honeynet/refresh"><button class="btn ghost sm">Refresh (collect + analyse)</button></form></div>')


# ------------------------------------------------------------------ pages
def render_overview() -> str:
    a, d = load()
    if not a:
        return empty()
    t, f = a["totals"], a["funnel"]
    wn = a["windows"]
    loaders = a["roles"].get("loader", 0)
    body = [tabs("/honeynet"), header(a, d), '<div class=tiles>',
            tile(t["sessions"], "attack sessions"), tile(t["source_ips"], "Linux source IPs"),
            tile(t["logins_ok"], "logins accepted"), tile(f["Installation"], "reached install", "warn"),
            tile(t["campaigns"], "campaigns"), tile(t["unique_payload_hashes"], "payload hashes"),
            tile(t["unique_malicious_ips"], "unique attacker IPs"), tile(wn["failed_logons"], "failed RDP/SMB logons"),
            '</div>']
    rows = [(st + (" (inferred)" if st == "Weaponization" else ""), f[st], "") for st in STAGES]
    body.append('<div class=grid2><div class=card><h3>Cyber Kill Chain — sessions reaching each stage</h3>'
                + hbars(rows, t["sessions"]) +
                '<p class=dim style="margin-top:10px;font-size:12px">Stages mapped from observed actions only. '
                'Weaponization is never directly visible on a honeypot.</p></div>')
    body.append('<div class=card><h3>Sessions per day</h3>' + cols(a["daily_sessions"]) + '</div></div>')

    fam = a["families"]
    roles = a["roles"]
    body.append('<div class=grid2><div class=card><h3>Behaviour classes (sessions)</h3>'
                + (hbars([(k, v, "") for k, v in fam.items()], sum(fam.values())) if fam else '<p class=dim>None classified.</p>')
                + '</div><div class=card><h3>Attacker IPs by role</h3>'
                + hbars([(r, roles.get(r, 0), ROLE_TEXT[r]) for r in ROLE_TEXT if roles.get(r)], sum(roles.values()))
                + f'<p class=dim style="margin-top:10px;font-size:12px">{loaders} IPs installed payloads. '
                  'Blocklist excludes pure scanners (research scanners live there too).</p></div></div>')

    top = a["campaigns"][:5]
    body.append('<div class=card><h3>Top campaigns</h3><table><tr><th>Class</th><th>Tags</th>'
                '<th class=num>Sessions</th><th class=num>IPs</th><th>Deepest stage</th><th>Last seen</th></tr>')
    for c in top:
        body.append(f'<tr><td>{e(c["family"])} <span class="pill dim">{e(c["family_confidence"] or "—")}</span></td>'
                    f'<td class=mono>{e(", ".join(c["tags"]) or "—")}</td><td class=num>{c["sessions"]:,}</td>'
                    f'<td class=num>{len(c["source_ips"])}</td><td>{e(c["stages"][-1])}</td>'
                    f'<td class=dim>{e(c["last_seen"][:16])}</td></tr>')
    body.append('</table><p style="margin-top:10px"><a href="/honeynet/campaigns">All campaigns →</a></p></div>')

    st = service_state()
    body.append('<div class=card><h3>Service</h3><table>' + "".join(
        f'<tr><td class=mono>{e(u)}</td><td><span class="pill {"good" if s == "active" else "dim"}">{e(s)}</span></td></tr>'
        for u, s in st.items()) + '</table><p class=dim style="font-size:12px;margin-top:8px">'
        'honeyintel is a separate service (~/honeyintel). This desk reads its reports; nothing here publishes.</p></div>')
    return "".join(body)


def render_campaigns() -> str:
    a, d = load()
    if not a:
        return empty()
    body = [tabs("/honeynet/campaigns"), header(a, d),
            f'<p class=dim>{len(a["campaigns"])} campaigns. Sessions are merged when they share a loader '
            'fingerprint, payload/C2 host, payload hash or busybox bot tag.</p>']
    for i, c in enumerate(a["campaigns"], 1):
        stages = " → ".join(c["stages"])
        body.append(
            f'<details class=hn{" open" if i <= 2 else ""}><summary><b>{i}. {e(c["family"])}</b>'
            f'<span class="pill dim">{e(c["family_confidence"] or "unclassified")}</span>'
            f'<span class=mono>{e(", ".join(c["tags"]))}</span>'
            f'<span class=dim>{c["sessions"]:,} sessions · {len(c["source_ips"])} IPs · '
            f'{c["fingerprints"]} loader variant(s) · deepest: {e(c["stages"][-1])}</span></summary>'
            f'<table style="margin-top:10px">'
            f'<tr><th>Seen</th><td>{e(c["first_seen"][:16])} → {e(c["last_seen"][:16])} UTC</td></tr>'
            f'<tr><th>Kill chain</th><td>{e(stages)}</td></tr>'
            f'<tr><th>ATT&amp;CK</th><td class=mono>{e(", ".join(c["ttps"]))}</td></tr>'
            f'<tr><th>Creds accepted</th><td class=mono>{e(", ".join(f"{u}:{p} ×{n}" for (u, p), n in c["creds"]) or "—")}</td></tr>'
            f'<tr><th>Infrastructure</th><td class=mono>{e(", ".join(defang(h) for h in c["infra"]) or "—")}</td></tr>'
            f'<tr><th>Payload SHA-256</th><td class=mono style="word-break:break-all">{e(" ".join(c["hashes"]) or "—")}</td></tr>'
            f'<tr><th>Source IPs</th><td class=mono style="word-break:break-all">{e(", ".join(defang(x) for x in c["source_ips"][:40]))}'
            f'{" …" if len(c["source_ips"]) > 40 else ""}</td></tr></table>'
            f'<pre class=hn-cmd>{e(chr(10).join(defang_cmd(x) for x in c["sample"]))}</pre></details>')
    return "".join(body)


def defang_cmd(c: str) -> str:
    import re
    c = c.replace("http://", "hxxp://").replace("https://", "hxxps://")
    return re.sub(r"\b((?:\d{1,3}\.){3})(\d{1,3})\b", r"\1[.]\2", c.replace("\n", " ⏎ "))[:600]


def render_iocs(role: str = "") -> str:
    a, d = load()
    if not a:
        return empty()
    feed = a["ip_feed"]
    rows = [f for f in feed if not role or f["role"] == role]
    filt = '<div class=row style="margin-bottom:10px">' + "".join(
        f'<a class="btn sm {"" if role == r else "ghost"}" href="/honeynet/iocs{("?role=" + r) if r else ""}">'
        f'{e(r or "all")} ({(a["roles"].get(r, 0) if r else len(feed)):,})</a>'
        for r in [""] + [r for r in ROLE_TEXT if a["roles"].get(r)]) + "</div>"
    dl = '<div class=row style="margin-bottom:12px">' + "".join(
        f'<a class="btn ghost sm" href="/honeynet/file/{n}">{n}</a>'
        for n in ("iocs.csv", "blocklist.txt", "creds.csv", "report.json")) + "</div>"
    body = [tabs("/honeynet/iocs"), header(a, d), dl, filt,
            '<div class=card><table><tr><th>IP</th><th>Role</th><th>Deepest stage</th><th>Class</th>'
            '<th>Proto</th><th class=num>Sessions</th><th class=num>Attempts</th><th class=num>Accepted</th>'
            '<th>First → last</th></tr>']
    for f in rows[:500]:
        body.append(f'<tr><td class=mono>{e(defang(f["ip"]))}</td><td>{e(f["role"])}</td><td>{e(f["stage"])}</td>'
                    f'<td>{e(", ".join(f["families"]) or "—")}</td><td>{e(", ".join(f["protocols"]))}</td>'
                    f'<td class=num>{f["sessions"]:,}</td><td class=num>{f["login_attempts"]:,}</td>'
                    f'<td class=num>{f["logins_ok"]:,}</td>'
                    f'<td class=dim>{e(f["first_seen"][:10])} → {e(f["last_seen"][:10])}</td></tr>')
    more = f'<p class=dim>Showing 500 of {len(rows):,}; full set in iocs.csv.</p>' if len(rows) > 500 else ""
    body.append('</table>' + more + '</div>')
    infra = a["infra"]
    body.append('<div class=card><h3>Payload / C2 infrastructure</h3><table><tr><th>Host</th>'
                '<th class=num>Sessions</th><th class=num>Bots</th><th>Classes</th></tr>' + "".join(
                    f'<tr><td class=mono>{e(defang(x["host"]))}</td><td class=num>{x["sessions"]}</td>'
                    f'<td class=num>{x["bots"]}</td><td>{e(", ".join(x["families"]) or "—")}</td></tr>'
                    for x in infra) + '</table></div>')
    return "".join(body)


def render_credentials() -> str:
    a, d = load()
    if not a:
        return empty()
    ok, failed = a["creds_all"]["ok"], a["creds_all"]["failed"]

    def table(rows, n=40):
        return ('<table><tr><th>Username</th><th>Password</th><th class=num>Times</th></tr>' + "".join(
            f'<tr><td class=mono>{e(u or "(blank)")}</td><td class=mono>{e(p or "(blank)")}</td>'
            f'<td class=num>{c:,}</td></tr>' for u, p, c in rows[:n]) + '</table>')
    return "".join([tabs("/honeynet/credentials"), header(a, d),
                    '<div class=tiles>', tile(len(ok), "accepted pairs", "warn"),
                    tile(a["totals"]["unique_failed_pairs"], "failed pairs"),
                    tile(a["totals"]["login_attempts"], "SSH/Telnet attempts"), '</div>',
                    '<div class=grid2><div class=card><h3>Accepted by the sensor</h3>', table(ok),
                    '</div><div class=card><h3>Most-tried (failed)</h3>', table(failed),
                    '</div></div><p><a class="btn ghost sm" href="/honeynet/file/creds.csv">creds.csv</a></p>'])


def render_windows() -> str:
    a, d = load()
    if not a:
        return empty()
    w = a["windows"]
    body = [tabs("/honeynet/windows"), header(a, d), '<div class=tiles>',
            tile(w["failed_logons"], "failed logons"), tile(w["unique_ips"], "source IPs"),
            tile(len(w["remote_logons"]), "successful remote logons", "bad" if w["remote_logons"] else "good"),
            tile(len(w["account_changes"]), "account changes"), tile(len(w["log_cleared"]), "log clears"), '</div>']
    if w["by_host"]:
        body.append('<div class=card><h3>Failed logons by honeypot</h3>'
                    + hbars([(h, n, "") for h, n in w["by_host"].items()], w["failed_logons"]) + '</div>')
    body.append('<div class=grid2><div class=card><h3>Top source IPs</h3><table><tr><th>IP</th>'
                '<th class=num>Failed</th><th class=num>Usernames</th></tr>' + "".join(
                    f'<tr><td class=mono>{e(defang(r["ip"]))}</td><td class=num>{r["n"]:,}</td>'
                    f'<td class=num>{r["users"]}</td></tr>' for r in w["top_ips"]) + '</table></div>'
                '<div class=card><h3>Usernames sprayed</h3><table><tr><th>Username</th><th class=num>Attempts</th></tr>'
                + "".join(f'<tr><td class=mono>{e(r["username"])}</td><td class=num>{r["n"]:,}</td></tr>'
                          for r in w["top_users"]) + '</table></div></div>')
    if w["remote_logons"]:
        body.append('<div class=card><h3>Successful remote logons</h3><p class=dim>Review: may include operator testing.</p>'
                    '<table><tr><th>Time</th><th>Host</th><th>User</th><th>From</th><th>Type</th></tr>' + "".join(
                        f'<tr><td>{e(r["ts"][:16])}</td><td>{e(r["host"])}</td><td class=mono>{e(r["username"])}</td>'
                        f'<td class=mono>{e(defang(r["ip"]))}</td><td>{e(r["logon_type"])}</td></tr>'
                        for r in w["remote_logons"]) + '</table></div>')
    if a["alerts"]:
        body.append('<div class=card><h3>Sentinel / Defender alerts on honeypots</h3><table><tr><th>Alert</th>'
                    '<th>Severity</th><th>Provider</th><th class=num>Count</th></tr>' + "".join(
                        f'<tr><td>{e(r["name"])}</td><td>{e(r["severity"])}</td><td>{e(r["provider"])}</td>'
                        f'<td class=num>{r["n"]}</td></tr>' for r in a["alerts"]) + '</table></div>')
    return "".join(body)


def render_report() -> str:
    a, d = load()
    if not a:
        return empty()
    md = (d / "report.md").read_text() if (d / "report.md").exists() else ""
    social = (d / "social.txt").read_text() if (d / "social.txt").exists() else ""
    return "".join([tabs("/honeynet/report"), header(a, d),
                    '<div class=card><h3>Social draft</h3><p class=dim>Draft only — honeyintel never publishes. '
                    'Copy into Compose if you want it to go out through the normal review path.</p>'
                    f'<pre>{e(social)}</pre><a class="btn ghost sm" href="/compose">Open Compose</a></div>',
                    '<div class=card><h3>Subscriber report (markdown)</h3>'
                    '<a class="btn ghost sm" href="/honeynet/file/report.md">Download report.md</a>'
                    f'<pre style="margin-top:10px;max-height:70vh">{e(md)}</pre></div>'])
