#!/usr/bin/env python3
"""Honeynet add-on: merge the weekly first-party honeynet brief into this run.

honeyintel (/mnt/data/honeyintel) is a separate service that turns our own
honeypot telemetry into reports and never publishes. This bridge is the only
way its intel reaches people, and it does so *inside* a normal CTI run: it adds
one public post and one subscriber alert to state/decision.json after the
analyst, so the leak guard, validation, posting windows, post gap, daily cap and
social delay all apply exactly as they do to CTI posts. No approval step: the
post is built from the report by code, so its standard is enforced here and by
lint_post in the publisher (a post failing lint is held like any other).

    honeynet_bridge.py --merge state/decision.json   # called from bin/run.sh
    honeynet_bridge.py --preview [--report DIR]      # print the post + alert, change nothing

Cadence: one brief per `honeynet.every_days` (default 7), from the newest report
that is fresh and has enough sensor coverage. Every figure is copied from
report.json; nothing is estimated or model-written.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
BRIDGE_STATE = STATE / "honeynet_bridge.json"
SOURCE = "honeynet"
LONDON = ZoneInfo("Europe/London")
DEFAULTS = {
    "enabled": True,
    "report_dir": "/mnt/data/honeyintel/reports",
    "every_days": 7,
    "max_report_age_days": 3,
    "min_sessions": 1000,
    "score": 50,
}


def settings() -> dict:
    try:
        import yaml
        s = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}
    except Exception:  # noqa: BLE001
        s = {}
    return {**DEFAULTS, **(s.get("honeynet") or {})}


def defang(ip: str) -> str:
    head, _, tail = ip.rpartition(".")
    return f"{head}[.]{tail}" if head else ip


def _day(d: str) -> str:
    return datetime.strptime(d[:10], "%Y-%m-%d").strftime("%-d %B")


def span_text(first: str, last: str) -> str:
    a, b = datetime.strptime(first, "%Y-%m-%d"), datetime.strptime(last, "%Y-%m-%d")
    if a.month == b.month:
        return f"{a:%-d} to {b:%-d %B}"
    return f"{a:%-d %B} to {b:%-d %B}"


def newest_report(report_dir: Path) -> tuple[str, dict] | None:
    for d in sorted((p for p in report_dir.glob("20??-??-??") if p.is_dir()), reverse=True):
        f = d / "report.json"
        if f.exists():
            try:
                return d.name, json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
    return None


def data_span(r: dict) -> tuple[str, str]:
    """First and last day that actually has sessions (the window can overhang)."""
    days = sorted(d for d, n in (r.get("daily_sessions") or {}).items() if n)
    if days:
        return days[0], days[-1]
    w = r.get("window") or {}
    return w.get("start", "")[:10], w.get("end", "")[:10]


def coverage_gap(r: dict) -> str | None:
    """A sentence when the sensors went quiet well before the window closed."""
    last, end = r.get("last_sensor_event"), (r.get("window") or {}).get("end")
    if not last or not end:
        return None
    t_last = datetime.fromisoformat(last.replace("Z", "+00:00"))
    t_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if t_end - t_last < timedelta(hours=6):
        return None
    uk = t_last.astimezone(LONDON)
    return (f"Coverage note: our sensors stopped reporting at {uk:%H:%M} UK time on "
            f"{uk:%-d %B}, so these counts cover part of the week.")


def build(r: dict, cfg: dict) -> tuple[dict, dict]:
    t = r["totals"]
    first, last = data_span(r)
    span = span_text(first, last)
    funnel = r.get("funnel") or {}
    win = r.get("windows") or {}
    creds = r.get("creds") or []
    top_cred = creds[0] if creds else None
    camps = r.get("campaigns") or []
    top = camps[0] if camps else None
    gap = coverage_gap(r)

    # ---- public post: trend-lane shape, no brackets, no URLs, 3 hashtags ----
    hook = f"Our honeypots logged {t['sessions']:,} attack sessions from {t['source_ips']:,} IPs in one week."
    lines = [hook, "",
             f"This is first-party data from our own sensors, {span}, not a vendor feed.", ""]
    lines.append(f"Our sensors accepted {t['logins_ok']:,} logins. {funnel.get('Installation', 0):,} sessions "
                 f"reached the install stage, and we captured {t['unique_payload_hashes']:,} unique payloads.")
    if top_cred:
        (u, p), n = top_cred
        lines.append(f"The most-accepted login was {u}/{p or 'blank'}, used {n:,} times.")
    if top:
        lines.append(f"The biggest campaign was {top['family']}: {top['sessions']:,} sessions from "
                     f"{len(top.get('source_ips') or []):,} IPs.")
    tops = win.get("top_ips") or []
    if win.get("failed_logons") and tops:
        share = round(100 * tops[0]["n"] / win["failed_logons"])
        lines.append(f"On Windows, one IP made {tops[0]['n']:,} of {win['failed_logons']:,} failed "
                     f"RDP and SMB logons, {share}% of the total.")
    if gap:
        lines += ["", gap]
    lines += ["",
              "Why it matters: every login our sensors accepted came from a password guess, "
              "not an exploit. The top logins that worked were factory defaults.",
              "",
              "What to actually do:",
              "• Hunt for default logins like root/root and admin/admin on edge devices, then change them.",
              "• Take Telnet off the internet, and put SSH and RDP behind a VPN or an allow-list.",
              "• Alert on one IP failing logons across many usernames. That is a spray, not a user.",
              "",
              "First-party data from my own sensors. More free threat intel, no sign-up: "
              "cti.securitycyber.uk",
              "",
              "#CyberSecurity #ThreatIntel #Honeypot"]
    post_text = "\n".join(lines)
    short = (f"Our honeypots logged {t['sessions']:,} attack sessions, {span}. "
             f"The logins that worked were defaults like root/root. "
             f"First-party data. cti.securitycyber.uk")

    title = f"Honeynet weekly: {t['sessions']:,} attack sessions, {span}"
    post = {
        "title": title, "url": "", "source": SOURCE, "lane": "trend",
        "score": int(cfg["score"]), "confidence": "high", "cve_ids": [],
        "post_text": post_text, "short_text": short,
        "comment": "Weekly honeynet intel from my own sensors, free: "
                   "https://cti.securitycyber.uk",
    }

    # ---- subscriber alert: the depth the post leaves out ----
    sub = [f"SecurityCyber honeynet brief, {span}. First-party telemetry from our own Cowrie "
           "SSH/Telnet and Windows RDP/SMB sensors, collected in Microsoft Sentinel.", ""]
    if gap:
        sub += [gap, ""]
    sub += [f"Totals: {t['sessions']:,} sessions from {t['source_ips']:,} IPs; "
            f"{t['login_attempts']:,} login attempts, {t['logins_ok']:,} accepted; "
            f"{t['sessions_with_commands']:,} sessions ran commands in {t['campaigns']:,} campaigns; "
            f"{t['download_attempts']:,} download attempts, {t['unique_payload_hashes']:,} unique payload hashes.",
            "",
            "Kill chain, sessions reaching each stage: "
            + "; ".join(f"{k} {v:,}" for k, v in funnel.items()), ""]
    if creds:
        sub.append("Credentials that worked: " + ", ".join(
            f"{u}/{p or 'blank'} x{n}" for (u, p), n in creds[:10]))
        sub.append("")
    for i, c in enumerate(camps[:5], 1):
        tags = ", ".join(c.get("tags") or [])
        infra = ", ".join(defang(h) for h in (c.get("infra") or [])[:5])
        sub.append(f"Campaign {i}: {c['family']}, {c.get('family_confidence', '?')} confidence"
                   f"{f', bot tag {tags} (bot-chosen, indicative)' if tags else ''}. "
                   f"{c['sessions']:,} sessions from {len(c.get('source_ips') or [])} IPs, "
                   f"{c['first_seen'][:16]}Z to {c['last_seen'][:16]}Z. "
                   f"Kill chain reached: {' > '.join(c.get('stages') or [])}."
                   f"{f' Payload/C2: {infra}.' if infra else ''}")
    infra_all = r.get("infra") or []
    if infra_all:
        sub += ["", f"Payload / C2 hosts seen ({len(infra_all)}), top 15 by sessions: "
                + ", ".join(defang(h["host"]) for h in infra_all[:15])]
    if tops:
        sub += ["", f"Windows: {win.get('failed_logons', 0):,} failed logons from "
                f"{win.get('unique_ips', 0):,} IPs. Top sources: "
                + ", ".join(f"{defang(x['ip'])} {x['n']:,}" for x in tops[:10])]
    sub += ["", "Hunt: failed logons from one source across many usernames (Windows 4625, "
            "Linux auth logs); busybox echo tags or chmod 777 on /var/tmp/.f style dropper paths; "
            "outbound connections to the payload/C2 hosts above.",
            "IPs are defanged. Source: SecurityCyber honeynet, report dated "
            f"{r.get('_report_date', '')}."]
    alert = {
        "title": title, "url": "", "source": "SecurityCyber honeynet", "score": int(cfg["score"]),
        "confidence": "high", "category": "campaign", "cve_ids": [],
        "exposure": "Internet-facing SSH, Telnet, RDP and SMB accepting weak or default credentials.",
        "action": "Remove default logins, take Telnet off the internet, put SSH/RDP behind a VPN or "
                  "allow-list, and block or monitor the payload/C2 hosts listed.",
        "subscriber_text": "\n".join(sub), "post_text": post_text, "short_text": short,
    }
    return post, alert


def due(cfg: dict, report_date: str, r: dict, state: dict, now: datetime) -> str | None:
    """None if due, else the reason it isn't."""
    if not cfg.get("enabled"):
        return "disabled"
    if state.get("last_report_date") == report_date:
        return f"report {report_date} already sent"
    last = state.get("last_sent_at")
    if last:
        age = now - datetime.fromisoformat(last)
        if age < timedelta(days=float(cfg["every_days"])) - timedelta(hours=12):
            return f"last brief {age.days}d ago (< {cfg['every_days']}d)"
    rd = datetime.strptime(report_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if now - rd > timedelta(days=float(cfg["max_report_age_days"])):
        return f"newest report {report_date} is stale"
    if int((r.get("totals") or {}).get("sessions", 0)) < int(cfg["min_sessions"]):
        return "too few sessions for a representative week"
    return None


def merge(decision_path: Path, cfg: dict, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    decision = json.loads(decision_path.read_text())
    if not isinstance(decision, dict):
        return 0
    # Only this bridge may emit honeynet items. Anything the analyst labelled
    # honeynet is a spoof (prompt injection could try it to dodge the source
    # checks that honeynet posts are exempt from), so drop it. run.sh calls
    # this once per run on the analyst's fresh decision.json.
    for field in ("posts", "alerts"):
        items = decision.get(field)
        if isinstance(items, list):
            keep = [i for i in items if not (isinstance(i, dict) and i.get("source") in (SOURCE, "SecurityCyber honeynet"))]
            if len(keep) != len(items):
                print(f"[honeynet] dropped {len(items) - len(keep)} analyst {field} item(s) claiming honeynet source")
            decision[field] = keep

    found = newest_report(Path(cfg["report_dir"]))
    state = json.loads(BRIDGE_STATE.read_text()) if BRIDGE_STATE.exists() else {}
    why = "no honeyintel report found" if not found else due(cfg, found[0], found[1], state, now)
    if why:
        print(f"[honeynet] no brief this run: {why}")
        decision_path.write_text(json.dumps(decision, indent=2))
        return 0
    report_date, r = found
    r["_report_date"] = report_date
    post, alert = build(r, cfg)
    decision.setdefault("posts", []).append(post)
    decision.setdefault("alerts", []).append(alert)
    if decision.get("decision") != "post":
        decision["decision"] = "post"
        decision["reason"] = (str(decision.get("reason") or "") + " [honeynet weekly brief added]").strip()
    decision_path.write_text(json.dumps(decision, indent=2))
    BRIDGE_STATE.write_text(json.dumps({"last_report_date": report_date,
                                        "last_sent_at": now.isoformat(), "title": post["title"]}, indent=2))
    print(f"[honeynet] added weekly brief from report {report_date}: “{post['title']}”")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", type=Path)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--report", type=Path, help="report dir for --preview")
    a = ap.parse_args()
    cfg = settings()
    if a.preview:
        found = newest_report(a.report or Path(cfg["report_dir"]))
        if not found:
            print("no report"); return 1
        found[1]["_report_date"] = found[0]
        post, alert = build(found[1], cfg)
        print(post["post_text"], "\n\n---- short ----\n", post["short_text"],
              "\n\n---- subscriber ----\n", alert["subscriber_text"])
        return 0
    if a.merge:
        return merge(a.merge, cfg)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
