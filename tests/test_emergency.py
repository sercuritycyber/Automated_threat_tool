"""A verified emergency must be unstoppable by policy.

Public mitigation value on an actively exploited critical outranks feed
hygiene and subscriber exclusivity, so these lock in that nothing procedural
can hold one.
"""
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import publish_linkedin as P  # noqa: E402

P.SOCIAL_QUEUE = Path(tempfile.mkdtemp()) / "social_queue.json"
PUB = {"emergency_score": 90, "min_post_gap_minutes": 90,
       "social_delay_hours": 4, "social_delay_bypass_score": 85}
fails = []


def chk(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f" != {want!r}"))
    if not ok:
        fails.append(name)


# ---- the predicate ------------------------------------------------------
chk("breaking + high confidence",
    P.is_emergency({"lane": "breaking", "confidence": "high", "score": 85}, PUB), True)
chk("breaking + medium confidence",
    P.is_emergency({"lane": "breaking", "confidence": "medium", "score": 70}, PUB), True)
chk("breaking + LOW confidence is a rumour, not an emergency",
    P.is_emergency({"lane": "breaking", "confidence": "low", "score": 85}, PUB), False)
chk("intel at 95 counts (only exploitation reaches that)",
    P.is_emergency({"lane": "intel", "confidence": "high", "score": 95}, PUB), True)
chk("intel at 85 does not",
    P.is_emergency({"lane": "intel", "confidence": "high", "score": 85}, PUB), False)
chk("trend never does",
    P.is_emergency({"lane": "trend", "confidence": "high", "score": 40}, PUB), False)

# ---- lead time ---------------------------------------------------------
P.SOCIAL_QUEUE.unlink(missing_ok=True)
out = P.schedule_social(
    [{"title": "emg", "url": "u1", "lane": "breaking", "confidence": "high", "score": 70},
     {"title": "slow", "url": "u2", "lane": "intel", "confidence": "high", "score": 50}],
    {"alerts": []}, {"publish": PUB}, operator=False)
chk("emergency skips the 4h subscriber lead time",
    [o["title"] for o in out], ["emg"])
chk("the normal post still waits",
    [x["title"] for x in json.loads(P.SOCIAL_QUEUE.read_text())["queue"]], ["slow"])

P.SOCIAL_QUEUE.unlink(missing_ok=True)
out = P.schedule_social(
    [{"title": "rumour", "url": "u3", "lane": "breaking", "confidence": "low", "score": 70}],
    {"alerts": []}, {"publish": PUB}, operator=False)
chk("an unconfirmed 'breaking' takes the normal path", out, [])

# ---- posting windows ---------------------------------------------------
# A window that cannot contain "now", so the out-of-window branch is exercised.
SHUT = {"publish": dict(PUB, post_windows=["23:59-23:59"])}
P.SOCIAL_QUEUE.unlink(missing_ok=True)
kept = P.pace_posts(
    [{"title": "emg", "lane": "breaking", "confidence": "high", "score": 92},
     {"title": "normal", "lane": "intel", "confidence": "high", "score": 70}],
    SHUT, operator=False)
chk("emergency posts outside every window", [x["title"] for x in kept], ["emg"])
chk("the normal post is parked for the window",
    [x["title"] for x in json.loads(P.SOCIAL_QUEUE.read_text())["queue"]], ["normal"])

# ---- two emergencies both go, spacing notwithstanding ------------------
P.SOCIAL_QUEUE.unlink(missing_ok=True)
kept = P.pace_posts(
    [{"title": "e1", "lane": "breaking", "confidence": "high", "score": 95},
     {"title": "e2", "lane": "breaking", "confidence": "medium", "score": 88}],
    SHUT, operator=False)
chk("multiple emergencies all publish", sorted(x["title"] for x in kept), ["e1", "e2"])

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILURES: {', '.join(fails)}")
sys.exit(1 if fails else 0)
