"""Honeynet add-on: the weekly brief meets the house standard and goes through
the same pipeline rules as CTI posts, without an approval step."""
import json, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import decision_validation as V
import honeynet_bridge as H
from lint_post import lint

fails = []


def chk(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f" != {want!r}"))
    if not ok:
        fails.append(name)


NOW = datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc)
REPORT = {
    "window": {"start": "2026-09-19T00:00:00Z", "end": "2026-09-27T00:00:00Z", "days": 8},
    "last_sensor_event": "2026-09-26T12:13:55Z",
    "totals": {"sessions": 22884, "source_ips": 1467, "login_attempts": 22024, "logins_ok": 777,
               "sessions_with_commands": 626, "download_attempts": 306,
               "unique_payload_hashes": 42, "campaigns": 46},
    "daily_sessions": {"2026-09-19": 0, "2026-09-20": 10, "2026-09-26": 4731},
    "funnel": {"Reconnaissance": 22884, "Installation": 77},
    "creds": [[["root", "root"], 238], [["admin", "admin"], 62]],
    "campaigns": [{"family": "Mirai-lineage", "family_confidence": "medium", "tags": ["HISILICON"],
                   "sessions": 223, "source_ips": ["198.51.100.%d" % i for i in range(54)],
                   "first_seen": "2026-09-23T20:53:50Z", "last_seen": "2026-09-26T11:45:37Z",
                   "stages": ["Reconnaissance", "Delivery"], "infra": ["192.0.2.7"]}],
    "infra": [{"host": "192.0.2.7", "sessions": 19}],
    "windows": {"failed_logons": 175959, "unique_ips": 145,
                "top_ips": [{"ip": "203.0.113.9", "n": 115080, "users": 5022}]},
}


def report_dir(date="2026-09-29", report=REPORT):
    d = Path(tempfile.mkdtemp())
    (d / date).mkdir()
    (d / date / "report.json").write_text(json.dumps(report))
    return d


# ---- the post meets the house standard ----
post, alert = H.build({**REPORT, "_report_date": "2026-09-29"}, H.DEFAULTS)
ql = lint(post["post_text"], [], "trend")
chk("post lints clean (no errors)", ql["errors"], [])
chk("post has no lint warnings", ql["warnings"], [])
chk("no brackets (LinkedIn truncates at '(')", "(" in post["post_text"], False)
chk("figures copied from the report", all(s in post["post_text"] for s in
    ("22,884", "1,467", "777", "root/root", "115,080 of 175,959", "65%")), True)
chk("data span, not the padded window", "20 to 26 September" in post["post_text"], True)
chk("coverage gap disclosed", "stopped reporting at 13:13 UK time on 26 September" in post["post_text"], True)
chk("subscriber alert defangs IPs", ("192.0.2[.]7" in alert["subscriber_text"],
                                      "192.0.2.7" in alert["subscriber_text"]), (True, False))
chk("alert scored above every tier floor and the digest bar", alert["score"] >= 30, True)
chk("Bluesky teaser fits", len(post["short_text"]) <= 300, True)

# ---- cadence ----
cfg = {**H.DEFAULTS}
chk("fresh report with no history is due", H.due(cfg, "2026-09-29", REPORT, {}, NOW), None)
chk("same report is not sent twice",
    H.due(cfg, "2026-09-29", REPORT, {"last_report_date": "2026-09-29"}, NOW), "report 2026-09-29 already sent")
chk("weekly cadence holds a newer report",
    bool(H.due(cfg, "2026-09-29", REPORT, {"last_report_date": "2026-09-28",
                                           "last_sent_at": (NOW - timedelta(days=2)).isoformat()}, NOW)), True)
chk("stale report is skipped", H.due(cfg, "2026-09-20", REPORT, {}, NOW), "newest report 2026-09-20 is stale")
chk("thin week is skipped",
    H.due(cfg, "2026-09-29", {**REPORT, "totals": {**REPORT["totals"], "sessions": 10}}, {}, NOW),
    "too few sessions for a representative week")

# ---- merge: spoof stripped, brief added, decision promoted, state recorded ----
tmp = Path(tempfile.mkdtemp())
H.BRIDGE_STATE = tmp / "honeynet_bridge.json"
dec = tmp / "decision.json"
dec.write_text(json.dumps({"decision": "skip", "reason": "quiet", "analyst": "claude",
    "posts": [{"title": "spoof", "source": "honeynet", "post_text": "x"}],
    "alerts": [{"title": "real alert", "source": "CISA"}, {"title": "spoof", "source": "honeynet"}]}))
H.merge(dec, {**cfg, "report_dir": str(report_dir())}, NOW)
d = json.loads(dec.read_text())
chk("analyst items claiming honeynet are stripped",
    [p["title"] for p in d["posts"] if p["title"] == "spoof"] + [a["title"] for a in d["alerts"] if a["title"] == "spoof"], [])
chk("brief added as one post + one alert",
    (len(d["posts"]), [a["title"] for a in d["alerts"]][0], len(d["alerts"])), (1, "real alert", 2))
chk("skip run promoted so the post can queue", d["decision"], "post")
chk("state records the report", json.loads(H.BRIDGE_STATE.read_text())["last_report_date"], "2026-09-29")
H.merge(dec, {**cfg, "report_dir": str(report_dir())}, NOW)
chk("a repeat merge never duplicates the brief",
    sum(p.get("source") == "honeynet" for p in json.loads(dec.read_text())["posts"]) <= 1, True)

# ---- validation: honeynet post survives, news post without a source doesn't ----
news = {"title": "n", "url": "", "score": 60, "post_text": "x", "comment": ""}
clean, dropped = V.sanitize_decision({"decision": "post", "analyst": "api:x/m", "posts": [post, news]},
                                     {"candidates": []})
chk("honeynet post passes provenance, sourceless news post dropped",
    ([p["title"] for p in clean["posts"]], len(dropped)), ([post["title"]], 1))
bad = {**post, "post_text": post["post_text"] + " https://evil.test/x"}
chk("honeynet post with a body URL is still dropped",
    len(V.sanitize_decision({"decision": "post", "posts": [bad]}, {"candidates": []})[1]), 1)

# ---- publisher: backup-model hold skips the honeynet brief, not the news post ----
tmp2 = Path(tempfile.mkdtemp())
dec2 = tmp2 / "decision.json"
news_post = {"title": "news", "url": "https://x.test/a", "score": 60, "lane": "intel",
             "post_text": "y" * 400, "comment": "https://x.test/a"}
dec2.write_text(json.dumps({"decision": "post", "analyst": "api:x/m", "posts": [post, news_post]}))
probe = f"""
import sys, json
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'bin')!r})
import publish_linkedin as P
P.STATE = Path({str(tmp2)!r}); P.SOCIAL_QUEUE = P.STATE / "social_queue.json"
P.archive_draft = lambda *a, **k: None
P.write_last_run = lambda **k: None
# dry_run + a one-minute window: nothing can reach a network from this test.
P.load_settings = lambda: {{"publish": {{"social_delay_hours": 0, "dry_run": True,
    "post_windows": ["03:00-03:01"]}}, "analyst": {{}}}}
sys.argv = ["x", "--decision", {str(dec2)!r}]
P.main()
q = json.loads((P.STATE / "pending.json").read_text())["queue"]
print("RESULT", [p["title"] for p in q])
"""
out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
line = next((l for l in out.stdout.splitlines() if l.startswith("RESULT")), out.stderr[-300:])
chk("backup run: news post held, honeynet brief not held", line, "RESULT ['news']")

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
