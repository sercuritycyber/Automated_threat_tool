"""Per-source grounding: a post's facts must come from ITS OWN source story,
not from anywhere in the batch (the "Chinese whispers" blending failure)."""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import decision_validation as V

fails = []


def chk(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f" != {want!r}"))
    if not ok:
        fails.append(name)


CANDS = {"candidates": [
    {"url": "https://x.test/citrix", "title": "Citrix zero-days CVE-2026-88771 exploited",
     "summary": "Fixed in 14.1-73.37. Storm-2570 linked. 1,700 appliances exposed."},
    {"url": "https://x.test/crypto", "title": "$387M crypto heist",
     "summary": "Attackers drained $387M after CVE-2026-50000 in a bridge contract."},
]}


def post(url, text, cves=()):
    return {"url": url, "score": 70, "cve_ids": list(cves), "post_text": text,
            "comment": f"Source: {url}"}


# ---- the blending case: story B's CVE in story A's post ----
blended = post("https://x.test/citrix", "Patch now: CVE-2026-50000 is exploited.", ["CVE-2026-50000"])
chk("batch-wide text contains the CVE (old check would pass)", "CVE-2026-50000" in json.dumps(CANDS), True)
chk("per-source check catches the blended CVE",
    V.ungrounded_facts(blended, CANDS), ["cve CVE-2026-50000"])

grounded = post("https://x.test/citrix",
                "CVE-2026-88771 fixed in 14.1-73.37; Storm-2570 linked; 1,700 exposed.", ["CVE-2026-88771"])
chk("facts from its own source pass", V.ungrounded_facts(grounded, CANDS), [])

chk("blended money figure caught",
    V.ungrounded_facts(post("https://x.test/citrix", "A $387M loss."), CANDS), ["money $387M"])
chk("invented actor ID caught",
    V.ungrounded_facts(post("https://x.test/citrix", "Linked to UNC5221."), CANDS), ["actor UNC5221"])
chk("invented build caught",
    V.ungrounded_facts(post("https://x.test/citrix", "Upgrade to 14.1-80.12."), CANDS), ["version 14.1-80.12"])
chk("plain numbers and dates are not facts to check",
    V.ungrounded_facts(post("https://x.test/citrix", "Do these 3 checks within 24 hours of 28 Sep."), CANDS), [])

# ---- sanitize: backup drops, Claude marks for review ----
d = {"decision": "post", "analyst": "codex-summary", "posts": [dict(blended)]}
clean, dropped = V.sanitize_decision(d, CANDS)
chk("backup engine: blended post dropped", (len(clean["posts"]), bool(dropped)), (0, True))

d = {"decision": "post", "posts": [dict(blended), dict(grounded)]}
clean, dropped = V.sanitize_decision(d, CANDS)
chk("claude: nothing dropped", (len(clean["posts"]), dropped), (2, []))
chk("claude: blended post marked for review", clean["posts"][0].get("ungrounded"), ["cve CVE-2026-50000"])
chk("claude: grounded post unmarked", clean["posts"][1].get("ungrounded"), None)

d = {"decision": "post", "posts": [post("https://x.test/citrix", "About 90% of appliances are unpatched.")]}
clean, _ = V.sanitize_decision(d, CANDS)
chk("claude: weak figure only logged, not held",
    (clean["posts"][0].get("ungrounded"), clean["posts"][0].get("unverified_figures")), (None, ["count 90%"]))

# ---- publisher holds the marked post, lets the grounded one through ----
tmp = Path(tempfile.mkdtemp())
dec = tmp / "decision.json"
marked = {**blended, "ungrounded": ["cve CVE-2026-50000"], "title": "blended"}
dec.write_text(json.dumps({"decision": "post", "posts": [marked, {**grounded, "title": "grounded"}]}))
probe = f"""
import sys, json
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'bin')!r})
import publish_linkedin as P
P.STATE = Path({str(tmp)!r}); P.SOCIAL_QUEUE = P.STATE / "social_queue.json"
P.archive_draft = lambda *a, **k: None
P.write_last_run = lambda **k: None
P.load_settings = lambda: {{"publish": {{"social_delay_hours": 5}}, "analyst": {{}}}}
sys.argv = ["x", "--decision", {str(dec)!r}]
P.main()
held = [q["title"] for q in json.loads((P.STATE / "pending.json").read_text())["queue"]]
queued = [q["title"] for q in json.loads(P.SOCIAL_QUEUE.read_text())["queue"]]
print("RESULT", held, queued)
"""
out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
line = next((l for l in out.stdout.splitlines() if l.startswith("RESULT")), out.stderr[-300:])
chk("blended post held, grounded post continues", line, "RESULT ['blended'] ['grounded']")

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
