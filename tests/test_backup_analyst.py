"""Backup-model guards: CVE grounding and the hold-for-approval gate."""
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


CANDS = {"candidates": [{"url": "https://x.test/a", "title": "Flaw CVE-2026-1111 exploited"}]}


def post(cves, text="Patch now."):
    return {"url": "https://x.test/a", "score": 60, "cve_ids": cves,
            "post_text": text, "comment": "Source: https://x.test/a"}


chk("claude is not a backup", V.is_backup_analyst({}), False)
chk("gemini is a backup", V.is_backup_analyst({"analyst": "antigravity-gemini"}), True)
chk("api provider is a backup", V.is_backup_analyst({"analyst": "api:openrouter/m"}), True)

for who, strict in (("antigravity-gemini", True), ("api:openrouter/m", True), (None, False)):
    d = {"decision": "post", "analyst": who, "posts": [post(["CVE-2026-9999"])]}
    _, dropped = V.sanitize_decision(d, CANDS)
    chk(f"invented CVE dropped for {who}", bool(dropped), strict)

d = {"decision": "post", "analyst": "api:x/m", "posts": [post(["CVE-2026-1111"])]}
chk("grounded CVE kept", V.sanitize_decision(d, CANDS)[1], [])
d = {"decision": "post", "analyst": "api:x/m", "posts": [post([], "See CVE-2026-2222 now.")]}
chk("CVE invented in body text is caught", bool(V.sanitize_decision(d, CANDS)[1]), True)

chk("summary-only codex is a backup", V.is_backup_analyst({"analyst": "codex-summary"}), True)
chk("web-verified codex is not", V.is_backup_analyst({"analyst": "codex"}), False)
_env = {"LINKEDIN_ACCESS_TOKEN": "AQX" + "z" * 40, "EMAIL_SMTP_PORT": "587", "EMPTY": ""}
chk("secret value in an alert is caught",
    V.leaked_secrets({"alerts": [{"body": "key AQX" + "z" * 40}]}, _env), ["LINKEDIN_ACCESS_TOKEN"])
chk("short values never match", V.leaked_secrets({"alerts": [{"body": "port 587"}]}, _env), [])
chk("public site URL is not a secret",
    V.leaked_secrets({"posts": [{"comment": "https://cti.securitycyber.uk"}]},
                     {"CTI_WORKER_URL": "https://cti.securitycyber.uk"}), [])

# ---- publisher holds backup-model posts in pending.json, posts nothing ----
tmp = Path(tempfile.mkdtemp())
dec = tmp / "decision.json"
dec.write_text(json.dumps({"decision": "post", "analyst": "api:x/m", "posts": [post(["CVE-2026-1111"])]}))
probe = f"""
import sys, json
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'bin')!r})
import publish_linkedin as P
P.STATE = Path({str(tmp)!r}); P.SOCIAL_QUEUE = P.STATE / "social_queue.json"
P.archive_draft = lambda *a, **k: None
P.write_last_run = lambda **k: None
P.load_settings = lambda: {{"publish": {{"social_delay_hours": 0}}, "analyst": {{}}}}
sys.argv = ["x", "--decision", {str(dec)!r}]
rc = P.main()
q = json.loads((P.STATE / "pending.json").read_text())["queue"]
print("RESULT", rc, len(q), q[0]["held_because"])
"""
out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
line = next((l for l in out.stdout.splitlines() if l.startswith("RESULT")), out.stderr[-300:])
chk("backup post held for approval, not published", line, "RESULT 0 1 backup model (api:x/m)")

# ---- engines on probation (analyst.hold_posts_from) are held too ----
tmp2 = Path(tempfile.mkdtemp())
dec2 = tmp2 / "decision.json"
dec2.write_text(json.dumps({"decision": "post", "analyst": "codex", "posts": [post(["CVE-2026-1111"])]}))
probe2 = (probe.replace(repr(str(tmp)), repr(str(tmp2))).replace(repr(str(dec)), repr(str(dec2)))
          .replace('"analyst": {}', '"analyst": {"hold_posts_from": ["codex"]}'))
out = subprocess.run([sys.executable, "-c", probe2], capture_output=True, text=True)
line = next((l for l in out.stdout.splitlines() if l.startswith("RESULT")), out.stderr[-300:])
chk("web-verified codex post held while on probation", line, "RESULT 0 1 backup model (codex)")

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
