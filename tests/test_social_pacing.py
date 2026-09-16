"""Exercise the new scheduling/pacing logic without posting anything."""
import json, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import publish_linkedin as P

tmp = Path(tempfile.mkdtemp())
P.SOCIAL_QUEUE = tmp / "social_queue.json"

S = {"publish": {"social_delay_hours": 0.5, "social_delay_bypass_score": 85,
                 "min_post_gap_minutes": 90,
                 "post_windows": ["07:00-09:30", "11:30-13:30", "16:30-18:30"],
                 "lane_min_score": {"breaking": 60, "intel": 45, "trend": 32}}}
DEC = {"alerts": [{"cve_ids": ["CVE-2026-1"]}, {"cve_ids": ["CVE-2026-2"]},
                  {"cve_ids": ["CVE-2026-2"]}, {}, {}], "reason": "x"}
fails = []


def chk(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f" != {want!r}"))
    if not ok:
        fails.append(name)


# ---- token substitution -------------------------------------------------
chk("alerts_today (5 alerts)",
    P.substitute_tokens("We wrote {alerts_today} briefs today.", DEC),
    "We wrote 5 briefs today.")
chk("cves_today dedups", P.substitute_tokens("{cves_today} CVEs", DEC), "2 CVEs")
chk("thin run reads correctly",
    P.substitute_tokens("We wrote {alerts_today} briefs today.", {"alerts": [{}]}),
    "We wrote a full set of briefs today.")
chk("no token untouched", P.substitute_tokens("plain text", DEC), "plain text")

# ---- lane_of ------------------------------------------------------------
chk("lane default", P.lane_of({}), "intel")
chk("lane garbage -> intel", P.lane_of({"lane": "VIRAL"}), "intel")
chk("lane breaking", P.lane_of({"lane": " Breaking "}), "breaking")

# ---- schedule_social: emergency bypasses the lead time -----------------
P.SOCIAL_QUEUE.unlink(missing_ok=True)
out = P.schedule_social(
    [{"title": "brk", "url": "u1", "lane": "breaking", "confidence": "high", "score": 70},
     {"title": "hi", "url": "u2", "lane": "intel", "score": 90},
     {"title": "norm", "url": "u3", "lane": "intel", "score": 50}],
    DEC, S, operator=False)
chk("breaking + score>=85 go now", sorted(o["title"] for o in out), ["brk", "hi"])
q = json.loads(P.SOCIAL_QUEUE.read_text())["queue"]
chk("normal intel parked for lead time", [x["title"] for x in q], ["norm"])

# ---- queue drains even with delay 0 (the old bug) ----------------------
past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
P.SOCIAL_QUEUE.write_text(json.dumps({"queue": [
    {"title": "ripe", "url": "u9", "lane": "intel", "score": 50, "post_after": past}]}))
S0 = {"publish": dict(S["publish"], social_delay_hours=0)}
out = P.schedule_social([], DEC, S0, operator=False)
chk("due item released when delay=0", [o["title"] for o in out], ["ripe"])
chk("released item marked from_queue", out[0].get("from_queue"), True)
chk("queue emptied after release", json.loads(P.SOCIAL_QUEUE.read_text())["queue"], [])

# ---- no duplicate queueing --------------------------------------------
future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
P.SOCIAL_QUEUE.write_text(json.dumps({"queue": [
    {"title": "dup", "url": "u5", "lane": "intel", "score": 50, "post_after": future}]}))
P.schedule_social([{"title": "dup", "url": "u5", "lane": "intel", "score": 50}],
                  DEC, S, operator=False)
chk("already-queued item not re-queued",
    len(json.loads(P.SOCIAL_QUEUE.read_text())["queue"]), 1)

# ---- operator bypasses everything -------------------------------------
P.SOCIAL_QUEUE.unlink(missing_ok=True)
out = P.schedule_social([{"title": "op", "url": "u7", "lane": "intel", "score": 1}],
                        DEC, S, operator=True)
chk("operator one-off posts now", [o["title"] for o in out], ["op"])

# ---- window helpers ---------------------------------------------------
wins = P._windows(S)
chk("windows parsed", wins, [((7, 0), (9, 30)), ((11, 30), (13, 30)), ((16, 30), (18, 30))])
d = lambda h, m: datetime(2026, 9, 10, h, m, tzinfo=P.LONDON)
chk("08:00 in window", P._in_window(d(8, 0), wins), True)
chk("03:00 out of window", P._in_window(d(3, 0), wins), False)
chk("10:00 out of window", P._in_window(d(10, 0), wins), False)
chk("03:00 -> next 07:00", P._next_window(d(3, 0), wins).strftime("%d %H:%M"), "10 07:00")
chk("10:00 -> next 11:30", P._next_window(d(10, 0), wins).strftime("%d %H:%M"), "10 11:30")
chk("20:00 -> tomorrow 07:00", P._next_window(d(20, 0), wins).strftime("%d %H:%M"), "11 07:00")
chk("malformed window ignored",
    P._windows({"publish": {"post_windows": ["07:00-09:30", "nonsense"]}}),
    [((7, 0), (9, 30))])

# ---- pace_posts -------------------------------------------------------
# pace_posts branches on whether *now* is inside a posting window, so these
# assertions must not depend on the clock. ALWAYS_OPEN keeps the in-window
# branch under test; the out-of-window branch is covered in test_emergency.py.
ALWAYS_OPEN = {"publish": dict(S["publish"], post_windows=["00:00-23:59"])}
P.SOCIAL_QUEUE.unlink(missing_ok=True)
three = [{"title": "a", "lane": "intel", "score": 70},
         {"title": "b", "lane": "intel", "score": 60},
         {"title": "c", "lane": "trend", "score": 40}]
kept = P.pace_posts([dict(x) for x in three], ALWAYS_OPEN, operator=False)
parked = json.loads(P.SOCIAL_QUEUE.read_text())["queue"] if P.SOCIAL_QUEUE.exists() else []
chk("only one post goes out per run", len(kept), 1)
chk("the other two are parked", sorted(x["title"] for x in parked), ["b", "c"])
chk("parked items carry post_after", all(x.get("post_after") for x in parked), True)

P.SOCIAL_QUEUE.unlink(missing_ok=True)
kept = P.pace_posts([{"title": "brk", "lane": "breaking", "confidence": "high", "score": 95},
                     {"title": "a", "lane": "intel", "score": 70}],
                    ALWAYS_OPEN, operator=False)
chk("breaking always goes, intel parked alongside it",
    sorted(x["title"] for x in kept), ["brk"])

P.SOCIAL_QUEUE.unlink(missing_ok=True)
kept = P.pace_posts([{"title": "op1", "lane": "intel", "score": 70},
                     {"title": "op2", "lane": "intel", "score": 60}],
                    ALWAYS_OPEN, operator=True)
chk("operator not paced", len(kept), 2)

P.SOCIAL_QUEUE.unlink(missing_ok=True)
kept = P.pace_posts([{"title": "q1", "lane": "intel", "score": 70, "from_queue": True},
                     {"title": "q2", "lane": "intel", "score": 60, "from_queue": True}],
                    ALWAYS_OPEN, operator=False)
chk("one non-breaking post per run, even from the queue", len(kept), 1)
chk("the queue-released item is preferred", kept[0]["title"], "q1")
reparked = json.loads(P.SOCIAL_QUEUE.read_text())["queue"]
chk("the surplus is re-parked, not dropped", [x["title"] for x in reparked], ["q2"])

# a run carrying a breaking post posts nothing else alongside it
P.SOCIAL_QUEUE.unlink(missing_ok=True)
kept = P.pace_posts([{"title": "brk", "lane": "breaking", "confidence": "high", "score": 95},
                     {"title": "i1", "lane": "intel", "score": 70, "from_queue": True}],
                    ALWAYS_OPEN, operator=False)
chk("breaking post stands alone", [x["title"] for x in kept], ["brk"])
chk("its companion is parked",
    [x["title"] for x in json.loads(P.SOCIAL_QUEUE.read_text())["queue"]], ["i1"])

# ---- CVE repost gate (2026-09-16 incident: CVE-2026-76461 posted 4x in <24h
# because a backlog flush via --approve never re-checked pending.json items
# against what had already shipped in the meantime) ----------------------
P.STATE = tmp
hist = tmp / "history.jsonl"
now = datetime.now(timezone.utc)
hist.write_text(
    json.dumps({"posted_at": (now - timedelta(hours=1)).isoformat(),
                "cve_ids": ["CVE-2026-76461"], "title": "already posted"}) + "\n"
    + json.dumps({"posted_at": (now - timedelta(hours=100)).isoformat(),
                  "cve_ids": ["CVE-2026-99999"], "title": "too old to count"}) + "\n"
)
recent = P._recently_posted_cves()
chk("a CVE posted 1h ago is in the recent set", "CVE-2026-76461" in recent, True)
chk("a CVE posted 100h ago has aged out (72h window)", "CVE-2026-99999" in recent, False)
chk("case-insensitive lookup", "cve-2026-76461".upper() in recent, True)
hist.unlink()

# Same incident, second half: CVE-2026-90553 posted 4x 11-15s apart in one
# batch. A pre-loop history.jsonl snapshot can't see this — items accepted
# earlier in the SAME run must also block later duplicates.
dup = P._find_dup_cve(["CVE-2026-90553"], {}, {"CVE-2026-90553": "first phrasing"})
chk("a CVE already accepted this run is caught", dup is not None, True)
chk("...flagged as 'this run', not '72h'", dup[1] if dup else None, "this run")
none = P._find_dup_cve(["CVE-2026-1"], {"CVE-2026-2": "x"}, {"CVE-2026-3": "y"})
chk("an unrelated CVE is never flagged", none, None)
recent_hit = P._find_dup_cve(["CVE-2026-76461"], {"CVE-2026-76461": "recent title"}, {})
chk("a 72h-recent CVE takes priority over run_cves and is labelled '72h'",
    recent_hit[1] if recent_hit else None, "72h")

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
