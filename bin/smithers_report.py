#!/usr/bin/env python3
"""Smithers — read-only CTI pipeline health reporter for Paperclip (nightshift).

Runs hourly from cti-smithers.timer. Reads pipeline health on this box and
posts to Paperclip: one summary a day filed under the standing "Pipeline
health" task, a high-priority task when something breaks, and a note under
that alert when it recovers.

Only numbers, statuses and fixed labels leave this box. Feed and log text is
untrusted and Paperclip agents read these reports, so none of it is copied in.

Needs in config/env: PAPERCLIP_URL, SMITHERS_API_KEY, SMITHERS_COMPANY_ID,
SMITHERS_PROJECT_ID, SMITHERS_HEALTH_ISSUE_ID (plus the existing
CTI_WORKER_URL / CTI_ADMIN_TOKEN for subscriber counts).

  smithers_report.py            report (summary at most once a day after 08:00)
  smithers_report.py --dry-run  print the report and alert decision, post nothing
  smithers_report.py --summary  force today's summary even if already sent
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "smithers_state.json"
LOGS = ROOT / "logs"

UNITS = ("cti-harvester", "cti-emergency", "cti-digest", "cti-backup", "cti-marketing", "cti-retention")
SITES = {
    "main site": "https://securitycyber.uk/",
    "cti page": "https://securitycyber.uk/cti",
    "cti landing": "https://cti.securitycyber.uk/",
    "signup form": "https://cti.securitycyber.uk/subscribe?tier=free",
}
UA = "SecurityCyber-Smithers/1.0"
NO_HARVEST_HOURS = 6
DISK_ALERT_PCT = 90


def load_env() -> dict:
    env = {}
    p = ROOT / "config" / "env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def sh(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def unit_health() -> list[dict]:
    rows = []
    for u in UNITS:
        timer = sh("systemctl", "--user", "is-active", f"{u}.timer").strip() or "unknown"
        props = dict(l.split("=", 1) for l in sh(
            "systemctl", "--user", "show", f"{u}.service",
            "-p", "Result", "-p", "ExecMainStatus", "-p", "ExecMainExitTimestamp").splitlines() if "=" in l)
        last = props.get("ExecMainExitTimestamp", "")
        ok = props.get("Result") == "success" and props.get("ExecMainStatus") == "0"
        rows.append({"unit": u, "timer": timer, "ran": bool(last), "ok": ok, "last": last or "not yet run"})
    return rows


def run_health() -> dict:
    cutoff = time.time() - 86400
    logs = sorted((p for p in LOGS.glob("run-*.log") if p.stat().st_mtime >= cutoff), key=lambda p: p.stat().st_mtime)
    runs = fallback = stage_fail = posted = session_limit = 0
    epss_hit = epss_total = 0
    for p in logs:
        t = p.read_text(errors="replace")
        runs += 1
        fallback += "all analyst engines failed" in t
        stage_fail += "FAILED at" in t
        session_limit += "session limit" in t
        m = re.search(r"done — (\d+) posted", t)
        posted += int(m.group(1)) if m else 0
        m = re.search(r"EPSS: (\d+)/(\d+)", t)
        if m:
            epss_hit += int(m.group(1)); epss_total += int(m.group(2))
    newest = max((p.stat().st_mtime for p in LOGS.glob("run-*.log")), default=0)
    return {"runs": runs, "fallback": fallback, "stage_fail": stage_fail, "session_limit": session_limit,
            "posted": posted, "epss": f"{epss_hit}/{epss_total}",
            "hours_since_run": round((time.time() - newest) / 3600, 1) if newest else None}


def emergency_health() -> dict:
    j = sh("journalctl", "--user", "-u", "cti-emergency.service", "--since", "-24h", "--no-pager")
    return {"ok": j.count("Finished cti-emergency"), "failed": j.count("Failed to start")}


def site_health() -> dict:
    out = {}
    for label, url in SITES.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            out[label] = urllib.request.urlopen(req, timeout=15).status
        except urllib.error.HTTPError as e:
            out[label] = e.code
        except Exception:  # noqa: BLE001 — any failure is simply "down"
            out[label] = 0
    return out


def subscriber_health(env: dict) -> dict | None:
    url, tok = env.get("CTI_WORKER_URL"), env.get("CTI_ADMIN_TOKEN")
    if not url or not tok:
        return None
    try:
        # Cloudflare 403s urllib's default User-Agent, so always send one.
        req = urllib.request.Request(url.rstrip("/") + "/admin/stats",
                                     headers={"Authorization": f"Bearer {tok}", "User-Agent": UA})
        d = json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception:  # noqa: BLE001
        return None
    by_status = {str(k): int(v) for k, v in (d.get("by_status") or {}).items() if str(k).isalnum() or "_" in str(k)}
    return {"active": int(d.get("total_active") or 0), "mrr_gbp": round(int(d.get("mrr_pence") or 0) / 100, 2),
            "sent_24h": int(d.get("sent_24h") or 0), "by_status": by_status}


def problems_of(units, runs, emerg, sites, subs, disk_pct) -> list[str]:
    p = []
    for u in units:
        if u["timer"] != "active":
            p.append(f"timer not active: {u['unit']}")
        elif u["ran"] and not u["ok"]:
            p.append(f"last run failed: {u['unit']}")
    if runs["fallback"]:
        p.append("analyst fell back to unverified mode (last 24h)")
    if runs["stage_fail"]:
        p.append("a pipeline stage failed (last 24h)")
    if runs["hours_since_run"] is None or runs["hours_since_run"] > NO_HARVEST_HOURS:
        p.append(f"no harvest run for over {NO_HARVEST_HOURS}h")
    if emerg["failed"]:
        p.append("emergency sweep failures (last 24h)")
    for label, code in sites.items():
        if code != 200:
            p.append(f"site not OK: {label}")
    if subs is None:
        p.append("subscriber stats unreachable")
    if disk_pct >= DISK_ALERT_PCT:
        p.append(f"disk over {DISK_ALERT_PCT}%")
    return p


def report_md(units, runs, emerg, sites, subs, disk_pct, problems) -> str:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    L = [f"**Pipeline health — {now}**", "",
         f"**Status:** {'✅ all clear' if not problems else '⚠️ ' + str(len(problems)) + ' problem(s)'}", ""]
    if problems:
        L += [f"- {x}" for x in problems] + [""]
    L += ["| Scheduled job | Timer | Last run |", "|---|---|---|"]
    L += [f"| {u['unit']} | {u['timer']} | {'—' if not u['ran'] else ('✅' if u['ok'] else '❌')} {u['last']} |" for u in units]
    L += ["", "| Last 24h | Value |", "|---|---|",
          f"| Harvest runs | {runs['runs']} |", f"| Fallback (unverified) runs | {runs['fallback']} |",
          f"| Session-limit hits | {runs['session_limit']} |", f"| Stage failures | {runs['stage_fail']} |",
          f"| Posted to LinkedIn | {runs['posted']} |", f"| EPSS scored | {runs['epss']} |",
          f"| Hours since last run | {runs['hours_since_run']} |",
          f"| Emergency sweeps ok / failed | {emerg['ok']} / {emerg['failed']} |", ""]
    L += ["| Site | HTTP |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sites.items()]
    if subs:
        L += ["", f"**Subscribers:** {subs['active']} active · £{subs['mrr_gbp']:.2f} MRR · {subs['sent_24h']} alerts sent 24h · "
              + ", ".join(f"{k} {v}" for k, v in sorted(subs["by_status"].items()))]
    L += ["", f"Disk: {disk_pct}% used"]
    return "\n".join(L)


def api(env: dict, method: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(env["PAPERCLIP_URL"].rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {env['SMITHERS_API_KEY']}",
                                          "Content-Type": "application/json", "User-Agent": UA})
    return json.loads(urllib.request.urlopen(req, timeout=20).read() or b"{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    env = load_env()
    units, runs, emerg = unit_health(), run_health(), emergency_health()
    sites, subs = site_health(), subscriber_health(env)
    du = shutil.disk_usage("/")
    disk_pct = round(du.used / du.total * 100)
    problems = problems_of(units, runs, emerg, sites, subs, disk_pct)
    md = report_md(units, runs, emerg, sites, subs, disk_pct, problems)

    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    sig = "|".join(problems)
    today = datetime.now().strftime("%Y-%m-%d")
    want_summary = args.summary or (datetime.now().hour >= 8 and state.get("last_summary") != today)

    if args.dry_run:
        print(md)
        print(f"\n[dry-run] problems={len(problems)} alert_open={bool(state.get('alert_issue'))} "
              f"signature_changed={sig != state.get('alert_sig', '')} would_summarise={want_summary}")
        return 0

    missing = [k for k in ("PAPERCLIP_URL", "SMITHERS_API_KEY", "SMITHERS_COMPANY_ID",
                           "SMITHERS_PROJECT_ID", "SMITHERS_HEALTH_ISSUE_ID") if not env.get(k)]
    if missing:
        print(f"[smithers] missing config: {', '.join(missing)}", file=sys.stderr)
        return 1

    # Paperclip lets an agent key CREATE tasks without a heartbeat run, but
    # commenting on / editing another task needs one. So Smithers only ever
    # creates tasks: reports are child tasks filed already-done under their
    # parent. State is saved after every successful post so a later failure
    # can never cause a duplicate.
    def save() -> None:
        STATE.write_text(json.dumps(state, indent=2))

    def create(title: str, body: str, status: str, priority: str, parent: str | None = None) -> dict:
        payload = {"title": title, "description": body, "status": status, "priority": priority,
                   "projectId": env["SMITHERS_PROJECT_ID"]}
        if parent:
            payload["parentId"] = parent
        return api(env, "POST", f"/api/companies/{env['SMITHERS_COMPANY_ID']}/issues", payload)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if problems and sig != state.get("alert_sig", ""):
        if state.get("alert_issue"):
            create(f"Alert changed {stamp}: {len(problems)} problem(s)", md, "done", "high", state["alert_issue"])
        else:
            issue = create(f"Pipeline alert: {len(problems)} problem(s)", md, "todo", "high")
            state["alert_issue"] = issue.get("id")
        state["alert_sig"] = sig
        save()
    elif not problems and state.get("alert_issue"):
        create(f"Recovered {stamp} — all clear", md + "\n\nClose the parent alert when you've read this.",
               "done", "medium", state["alert_issue"])
        state.pop("alert_issue", None)
        state["alert_sig"] = ""
        save()

    if want_summary:
        status_word = "all clear" if not problems else f"{len(problems)} problem(s)"
        create(f"Pipeline health — {today} ({status_word})", md, "done", "low", env["SMITHERS_HEALTH_ISSUE_ID"])
        state["last_summary"] = today
        save()

    print(f"[smithers] problems={len(problems)} summary={'sent' if want_summary else 'skipped'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
