#!/usr/bin/env python3
"""Phase-1 self-test: the fan-out engine, with channel sends mocked."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "bin"))

FAILED = []


def check(name, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILED.append(name)


def main() -> int:
    tmp = tempfile.mkdtemp()
    os.environ["SUBS_DB_PATH"] = str(Path(tmp) / "t.db")
    os.environ["SUBS_TOKEN_SECRET"] = "x" * 20
    os.environ.pop("TURSO_DATABASE_URL", None)
    os.environ.pop("CTI_DRY_RUN", None)

    import db as dbmod
    import subscribers as S
    import subsconfig
    import channels as CH
    import deliver

    dbmod.reset_db_singleton()
    S.ensure_schema()

    # force subscriptions.enabled true + known tiers for the test
    subsconfig.load = lambda: {
        "enabled": True, "live": False, "brand_name": "BT CTI Test",
        "from_email": "a@b.c", "reply_to": "",
        "tiers": {
            "individual": {"instant_min_score": 90, "daily_digest": True, "attach_json": False,
                           "channels": ["email", "ntfy", "telegram"], "max_channels": 2},
            "team": {"instant_min_score": 75, "daily_digest": True, "attach_json": True,
                     "channels": ["email", "slack", "webhook", "ntfy", "telegram"], "max_channels": 6},
        },
    }

    # capture sends instead of hitting the network
    sent = []
    def fake_send(kind, target, subject, body, **kw):
        sent.append({"kind": kind, "target": target, "subject": subject,
                     "json": kw.get("json_payload")})
        return {"kind": kind, "ok": True, "ref": "mock", "error": ""}
    CH.send = fake_send
    deliver.CH.send = fake_send

    # --- subscribers ---
    a = S.create_pending("indiv@example.com", "individual")
    S.activate(a); S.add_channel(a, "email", "indiv@example.com", verified=True)
    S.set_prefs(a, vendors=["Fortinet"])

    b = S.create_pending("team@example.com", "team")
    S.activate(b)
    S.add_channel(b, "email", "team@example.com", verified=True)
    S.add_channel(b, "slack", "https://hooks.slack.com/services/x", verified=True)
    S.add_channel(b, "telegram", "999", verified=False)      # unverified -> skipped

    c = S.create_pending("paused@example.com", "team")
    S.activate(c); S.set_status(c, "paused")
    S.add_channel(c, "email", "paused@example.com", verified=True)

    # --- a run's worth of posts ---
    decision = {"decision": "post", "posts": [
        {"title": "Fortinet FortiOS RCE exploited", "url": "https://ex.com/forti", "source": "X",
         "score": 95, "cve_ids": ["CVE-2026-1000"], "post_text": "Patch FortiOS now. https://ex.com/forti",
         "short_text": "Patch FortiOS now CVE-2026-1000 https://ex.com/forti"},
        {"title": "Cisco IOS XE minor bug", "url": "https://ex.com/cisco", "source": "X",
         "score": 80, "cve_ids": ["CVE-2026-2000"], "post_text": "Cisco advisory. https://ex.com/cisco",
         "short_text": "Cisco CVE-2026-2000 https://ex.com/cisco"},
        {"title": "Roundup piece", "url": "https://ex.com/round", "source": "X",
         "score": 60, "cve_ids": [], "post_text": "weekly roundup https://ex.com/round", "short_text": "x"},
    ]}
    dpath = Path(tmp) / "decision.json"
    dpath.write_text(json.dumps(decision))
    deliver.POSTS_LOG = Path(tmp) / "posts_log.jsonl"

    # --- instant ---
    deliver.run("instant", dpath, dry=False)
    kinds_a = [s for s in sent if s["target"] == "indiv@example.com"]
    check("individual got the 95 (Fortinet, >=90, vendor match)", len(kinds_a) == 1)
    check("individual did NOT get the 80 (below tier threshold)",
          all("cisco" not in (s["subject"] or "").lower() for s in kinds_a))

    team_sent = [s for s in sent if s["target"] in ("team@example.com", "https://hooks.slack.com/services/x")]
    check("team got 95 + 80 on 2 verified channels (4 sends)", len(team_sent) == 4)
    check("team's unverified telegram was skipped",
          not any(s["kind"] == "telegram" for s in sent))
    check("paused subscriber got nothing",
          not any(s["target"] == "paused@example.com" for s in sent))
    check("team email carries JSON attachment (attach_json)",
          any(s["kind"] == "email" and s["json"] for s in team_sent))

    # --- dedupe: run instant again, nothing new ---
    before = len(sent)
    deliver.run("instant", dpath, dry=False)
    check("re-running instant sends nothing (dedupe)", len(sent) == before)

    # --- digest ---
    sent.clear()
    deliver.run("digest", dpath, dry=False)
    dg = [s for s in sent if s["target"] == "team@example.com"]
    check("digest: one email to team", len(dg) == 1)
    check("digest subject mentions item count", "item" in dg[0]["subject"])
    check("digest to individual too (digest pref on)",
          any(s["target"] == "indiv@example.com" for s in sent))

    # --- dry run sends nothing ---
    sent.clear()
    S.create_pending("d@example.com", "team")
    deliver.run("digest", dpath, dry=True)
    check("dry=True: no real sends recorded", len(sent) == 0)

    # --- disabled -> no-op ---
    subsconfig.load = lambda: {"enabled": False, "tiers": {}}
    check("disabled subscriptions -> run() returns 0", deliver.run("instant", dpath, False) == 0)

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} failed: {', '.join(FAILED)}")
        return 1
    print("✅ Phase-1 self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
