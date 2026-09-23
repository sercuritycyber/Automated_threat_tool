#!/usr/bin/env python3
"""Post-run audit — prove the social-funnel gaps are still closed.

Runs after each in-window harvest. Every check corresponds to a fault that was
found and fixed on 2026-09-10, so a regression is caught by the check that
would have caught it the first time. Writes a dated report to reports/ and
exits non-zero if anything FAILED, so systemd surfaces it.

  python3 bin/post_run_audit.py            # audit + write the report
  python3 bin/post_run_audit.py --quiet    # only print the summary line
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
STATE = ROOT / "state"
LONDON = ZoneInfo("Europe/London")
REPORTS = ROOT / "reports"

# Posts published before the funnel rules existed cannot be judged by them, or
# every audit FAILs forever on history. This is when the link-route rule landed
# (commit 42bb657, 2026-09-10). Posts older than this are simply not checked.
POLICY_EPOCH = datetime(2026, 9, 10, 10, 0, tzinfo=LONDON)

results: list[tuple[str, str, str]] = []       # (status, name, detail)


def _posted_at(r: dict):
    try:
        return datetime.fromisoformat(str(r.get("posted_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return None


def _in_policy(r: dict) -> bool:
    t = _posted_at(r)
    return t is not None and t >= POLICY_EPOCH


def rec(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))


def env() -> dict:
    out = {}
    p = ROOT / "config" / "env"
    if p.exists():
        for ln in p.read_text().splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def rows(name: str, hours: float | None = None) -> list[dict]:
    p = STATE / name
    if not p.exists():
        return []
    out = []
    cut = (datetime.now(timezone.utc) - timedelta(hours=hours)) if hours else None
    for ln in p.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if r.get("dry_run"):
            continue
        if cut:
            try:
                t = datetime.fromisoformat(str(r.get("posted_at") or r.get("at") or "")
                                           .replace("Z", "+00:00"))
            except ValueError:
                continue
            if t < cut:
                continue
        out.append(r)
    return out


# --------------------------------------------------------------- 1. config
def check_settings() -> None:
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}
    pub = cfg.get("publish", {})
    want = {
        "social_delay_hours": lambda v: 0 < float(v) <= 1,
        "social_delay_bypass_score": lambda v: int(v) > 0,
        "min_post_gap_minutes": lambda v: int(v) >= 30,
        "emergency_score": lambda v: int(v) >= 80,
    }
    bad = [k for k, ok in want.items()
           if k not in pub or not ok(pub[k])]
    wins = pub.get("post_windows") or []
    if len(wins) < 1:
        bad.append("post_windows")
    lanes = pub.get("lane_min_score") or {}
    if not all(l in lanes for l in ("breaking", "intel", "trend")):
        bad.append("lane_min_score")
    if cfg.get("brand", {}).get("name") != "SecurityCyber":
        bad.append("brand.name")
    if bad:
        rec("FAIL", "settings keys", f"missing/invalid: {', '.join(bad)}")
    else:
        rec("PASS", "settings keys",
            f"delay {pub['social_delay_hours']}h, {len(wins)} window(s), "
            f"gap {pub['min_post_gap_minutes']}m, emergency {pub['emergency_score']}")


# ------------------------------------- 2. the lint bug that held good posts
REFERENCE = """If you run Fortinet appliances, assume attackers are scanning for you now.

A pre-auth RCE is being exploited in the wild, and it is on CISA KEV.

The quick breakdown:

• What: CVSS 10.0 unauthenticated static code injection in the management API
• Impact: full remote code execution on the management server as root
• Affected: all versions before 2026.3 Hotfix 4, including the hosted tier
• Do this: apply Hotfix 4 now, or restrict management access to trusted IPs

What to look for: unexpected admin accounts, new scheduled tasks, or odd child
processes spawned by the service.

Subscribers had this at 08:12 with the affected build list and a detection rule.

We wrote 20 briefs like this for defenders today: cti.securitycyber.uk

#CyberSecurity #ThreatIntel #VulnerabilityManagement"""


def check_lint() -> None:
    try:
        from lint_post import lint, split_sentences
    except ImportError as e:
        rec("FAIL", "lint importable", str(e))
        return
    longest = max((len(s.split()) for s in split_sentences(REFERENCE)), default=0)
    if longest > 40:
        rec("FAIL", "sentence splitter", f"longest 'sentence' is {longest} words — "
            "the bullet block is being flattened again, which HOLDS good posts")
    else:
        rec("PASS", "sentence splitter", f"longest prose sentence {longest} words")
    r = lint(REFERENCE, ["CVE-2026-86218"], "breaking")
    if r["errors"]:
        rec("FAIL", "reference post lints clean", "; ".join(r["errors"][:3]))
    else:
        rec("PASS", "reference post lints clean",
            f"{len(r['warnings'])} warning(s), conversion line present: "
            f"{r['stats'].get('conversion_line')}")


# --------------------------------- 3. every post must carry a route + 3 tags
def check_shaper() -> None:
    try:
        from publishers import _li_shape
    except ImportError as e:
        rec("FAIL", "shaper importable", str(e))
        return
    out = _li_shape("Hook.\n\nBody.\n\n#A #B #C #D #E")
    problems = []
    if out.count("#") != 3:
        problems.append(f"{out.count('#')} tags, expected 3")
    if "cti.securitycyber.uk" not in out:
        problems.append("no route domain injected")
    if "in the comments" in _li_shape("Hi.\n\n(Link in the comments.)\n\n#A #B #C").lower():
        problems.append("dead comment promise not stripped")
    paren_out = _li_shape("Hook (CVSS 9.8) here.\n\n#A #B #C")
    if "(" in paren_out or ")" in paren_out:
        problems.append("literal paren reached the body — LinkedIn truncates on this")
    rec("FAIL" if problems else "PASS", "linkedin shaper",
        "; ".join(problems) or "3 tags + route guaranteed, dead promises stripped")


# ------------------------------------------- 4. what actually went on the page
def check_live_posts(limit: int = 3) -> None:
    import requests
    import publishers
    e = env()
    tok = e.get("LINKEDIN_ACCESS_TOKEN")
    if not tok:
        rec("WARN", "live post bodies", "no LINKEDIN_ACCESS_TOKEN — skipped")
        return
    recent = [r for r in rows("history.jsonl", hours=72)
              if r.get("post_id") and _in_policy(r)][-limit:]
    if not recent:
        rec("PASS", "live post bodies", "no in-policy posts yet to inspect")
        return
    import yaml
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}
    pinned = str(settings.get("publish", {}).get("linkedin_api_version", publishers._LI_DEFAULT_VERSION))
    bad: list[str] = []
    manual: list[str] = []
    for r in recent:
        urn = r["post_id"]
        resp = None
        try:
            # LinkedIn rotates LinkedIn-Version monthly and 426s the stale one —
            # same fallback list post_linkedin() uses, so this check doesn't
            # mistake "our own read-back is stale" for "the post has no route".
            for version in publishers._li_versions(pinned):
                resp = requests.get(
                    f"https://api.linkedin.com/rest/posts/{requests.utils.quote(urn, safe='')}",
                    headers={"Authorization": f"Bearer {tok}",
                             "X-Restli-Protocol-Version": "2.0.0",
                             "LinkedIn-Version": version}, timeout=20)
                if resp.status_code != 426:
                    break
        except requests.RequestException as exc:
            rec("WARN", "live post bodies", f"API unreachable ({exc})")
            return
        if resp is None or not resp.ok:
            code = resp.status_code if resp is not None else "?"
            bad.append(f"{urn[-8:]}: HTTP {code}")
            continue
        c = resp.json().get("commentary", "") or ""
        tags = len(re.findall(r"\{hashtag\|", c)) or c.count("#")
        issues = []
        if "https://" in c:
            issues.append("https:// in body (throttles reach)")
        if "securitycyber.uk" not in c:
            issues.append("NO route to the site")
        if tags > 3:
            issues.append(f"{tags} hashtags")
        if "in the comments" in c.lower():
            issues.append("promises a comment we cannot post")
        if issues:
            # A hand-posted item is the operator's own editorial call, so it is
            # reported but never counted as a system failure.
            tag = "manual" if r.get("source_path") == "manual-operator" else "auto"
            (manual if tag == "manual" else bad).append(
                f"{str(r.get('title',''))[:34]}: " + ", ".join(issues))
    if bad:
        rec("FAIL", f"live post bodies (last {len(recent)})", " | ".join(bad))
    elif manual:
        rec("WARN", f"live post bodies (last {len(recent)})",
            "operator-posted: " + " | ".join(manual))
    else:
        rec("PASS", f"live post bodies (last {len(recent)})",
            "all carry a route, no body links, <=3 tags")


# ------------------------------------------ 5. posting windows were respected
def check_windows() -> None:
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}
    wins = []
    for w in cfg.get("publish", {}).get("post_windows") or []:
        try:
            a, b = str(w).split("-", 1)
            wins.append((tuple(int(x) for x in a.split(":")),
                         tuple(int(x) for x in b.split(":"))))
        except ValueError:
            pass
    if not wins:
        rec("WARN", "posting windows", "none configured")
        return
    off = []
    checked = 0
    for r in rows("history.jsonl", hours=48):
        if str(r.get("lane", "")).lower() == "breaking":
            continue                      # emergencies are meant to ignore windows
        if r.get("source_path") == "manual-operator":
            continue                      # the operator posts when they like
        if not _in_policy(r):
            continue                      # predates the rule
        t = _posted_at(r)
        if t is None:
            continue
        checked += 1
        loc = t.astimezone(LONDON)
        if not any(a <= (loc.hour, loc.minute) <= b for a, b in wins):
            off.append(f"{loc:%a %H:%M} {str(r.get('title',''))[:30]}")
    rec("FAIL" if off else "PASS", "posts landed in a window (48h)",
        " | ".join(off[:4]) or f"{checked} in-policy post(s) checked, all in-window")


# ------------------------------------------- 6. the queue is not stranding work
def check_queue() -> None:
    p = STATE / "social_queue.json"
    if not p.exists():
        rec("PASS", "social queue", "empty")
        return
    try:
        q = json.loads(p.read_text()).get("queue", [])
    except (json.JSONDecodeError, OSError) as e:
        rec("FAIL", "social queue", f"unreadable ({e})")
        return
    now = datetime.now(timezone.utc)
    stale = []
    for x in q:
        try:
            after = datetime.fromisoformat(str(x.get("post_after", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if (now - after) > timedelta(hours=26):
            stale.append(f"{str(x.get('title',''))[:34]} due {after:%d %H:%M}")
    rec("FAIL" if stale else "PASS", "social queue drains",
        " | ".join(stale[:3]) or f"{len(q)} parked, none overdue >26h")


# ------------------------------ 7. nothing held by the old quality-gate bug
def check_no_quality_holds() -> None:
    p = STATE / "pending.json"
    if not p.exists():
        rec("PASS", "review queue", "nothing held")
        return
    try:
        q = json.loads(p.read_text()).get("queue", [])
    except (json.JSONDecodeError, OSError):
        rec("WARN", "review queue", "pending.json unreadable")
        return
    qual = [x for x in q if x.get("held_because") == "quality"]
    if qual:
        rec("WARN", "review queue", f"{len(qual)} post(s) held on quality — check the "
            f"lint errors are real: {str(qual[0].get('title',''))[:40]}")
    else:
        rec("PASS", "review queue", f"{len(q)} held, none on quality")


# --------------------------------------------------- 8. the emergency path
def check_emergency() -> None:
    try:
        from publish_linkedin import is_emergency
    except ImportError as e:
        rec("FAIL", "emergency path", str(e))
        return
    pub = {"emergency_score": 90}
    cases = [
        ({"lane": "breaking", "confidence": "high", "score": 85}, True),
        ({"lane": "breaking", "confidence": "low", "score": 85}, False),
        ({"lane": "intel", "confidence": "high", "score": 95}, True),
        ({"lane": "intel", "confidence": "high", "score": 60}, False),
    ]
    bad = [f"{c}" for c, want in cases if is_emergency(c, pub) is not want]
    rec("FAIL" if bad else "PASS", "emergency predicate",
        " | ".join(bad) or "breaking+confirmed bypasses policy, rumours do not")


# --------------------------------------------------------- 9. the test suites
def check_tests() -> None:
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    if not tests:
        rec("WARN", "test suites", "none found")
        return
    py = ROOT / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else "python3"
    bad = []
    for t in tests:
        r = subprocess.run([py, str(t)], cwd=str(ROOT), capture_output=True,
                           text=True, timeout=180)
        if r.returncode != 0:
            tail = (r.stdout or r.stderr).strip().splitlines()[-1:] or ["?"]
            bad.append(f"{t.name}: {tail[0][:70]}")
    rec("FAIL" if bad else "PASS", f"test suites ({len(tests)})",
        " | ".join(bad) or "all green")


# ------------------------------------------- 10. every scheduled job is armed AND working
# An active timer only proves the job is scheduled. cti-emergency sat "active"
# through 338 consecutive exec failures, so the last run's result is what counts.
# cti-audit is excluded: it exits non-zero by design whenever it finds a FAIL.
SCHEDULED_UNITS = ("cti-harvester", "cti-emergency", "cti-digest", "cti-backup",
                   "cti-marketing", "cti-retention")


def check_units() -> None:
    for unit in SCHEDULED_UNITS:
        timer = subprocess.run(["systemctl", "--user", "is-active", f"{unit}.timer"],
                               capture_output=True, text=True).stdout.strip()
        if timer != "active":
            rec("FAIL", f"{unit} timer", timer or "not installed")
            continue
        props = dict(
            line.split("=", 1) for line in subprocess.run(
                ["systemctl", "--user", "show", f"{unit}.service",
                 "-p", "Result", "-p", "ExecMainStatus", "-p", "ExecMainExitTimestamp"],
                capture_output=True, text=True).stdout.splitlines() if "=" in line)
        result, status = props.get("Result", "?"), props.get("ExecMainStatus", "?")
        last = props.get("ExecMainExitTimestamp", "")
        if not last:
            rec("WARN", f"{unit}", "timer armed, no run since boot yet")
        elif result == "success" and status == "0":
            rec("PASS", f"{unit}", f"last run ok ({last})")
        else:
            rec("FAIL", f"{unit}", f"last run {result} exit {status} ({last}) — "
                f"journalctl --user -u {unit}.service")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    for fn in (check_settings, check_lint, check_shaper, check_live_posts,
               check_windows, check_queue, check_no_quality_holds,
               check_emergency, check_tests, check_units):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — one broken check must not hide the rest
            rec("FAIL", fn.__name__, f"{type(e).__name__}: {e}")

    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    now = datetime.now(LONDON)
    head = (f"SecurityCyber post-run audit — {now:%Y-%m-%d %H:%M %Z}\n"
            f"{len(results) - len(fails) - len(warns)} passed, "
            f"{len(warns)} warning(s), {len(fails)} FAILED")
    body = "\n".join(f"  [{s:4}] {n}" + (f" — {d}" if d else "") for s, n, d in results)

    if not args.quiet:
        print(head)
        print(body)

    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"post-run-audit-{now:%Y-%m-%d}.md"
    with out.open("a") as fh:
        fh.write(f"\n## {now:%H:%M %Z}\n\n{head}\n\n```\n{body}\n```\n")
    if args.quiet:
        print(head.replace("\n", " | "))

    if fails:
        try:
            subprocess.run(["python3", str(ROOT / "bin" / "notify.py"),
                            f"CTI audit FAILED {len(fails)} check(s): "
                            f"{fails[0][1]} — {fails[0][2][:110]}", "high"],
                           timeout=20, check=False)
        except Exception:  # noqa: BLE001
            pass
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
