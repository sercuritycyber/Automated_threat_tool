#!/usr/bin/env python3
"""Phase-0 self-test: db backend, schema, tokens, config, subscriber CRUD.

Runs against a throwaway SQLite file (or Turso, if creds are set AND you pass
--turso — careful, it writes to the real DB). Safe by default.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

FAILED = []


def check(name, cond, detail=""):
    mark = "ok  " if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def main() -> int:
    use_turso = "--turso" in sys.argv
    if not use_turso:
        tmp = tempfile.mkdtemp()
        os.environ["SUBS_DB_PATH"] = str(Path(tmp) / "t.db")
        os.environ.pop("TURSO_DATABASE_URL", None)
        os.environ.pop("TURSO_AUTH_TOKEN", None)
    os.environ.setdefault("SUBS_TOKEN_SECRET", "test-secret-0123456789")

    import db as dbmod
    import tokens
    import subsconfig
    import subscribers as S

    dbmod.reset_db_singleton()
    d = dbmod.get_db()
    print(f"\ndb backend: {d.backend}")

    # --- migrations ---
    n = S.ensure_schema()
    check("schema applied", d.schema_version() == 2, f"version={d.schema_version()}")
    check("migrate is idempotent", S.ensure_schema() == 0)
    tabs = {r["name"] for r in d.query("SELECT name FROM sqlite_master WHERE type='table'")} \
        if d.backend == "sqlite" else set()
    if d.backend == "sqlite":
        check("core tables exist",
              {"subscribers", "channels", "prefs", "consents", "deliveries", "events",
               "signup_intents"} <= tabs)
        cols = {r["name"] for r in d.query("PRAGMA table_info(subscribers)")}
        check("cadence column added", "cadence" in cols)

    # --- tokens ---
    t = tokens.sign({"sub": 7, "act": "manage"}, ttl=60)
    got = tokens.verify(t, "manage")
    check("token round-trips", got["sub"] == 7)
    try:
        tokens.verify(t, "unsubscribe")
        check("token action is enforced", False)
    except tokens.BadToken:
        check("token action is enforced", True)
    try:
        tokens.verify(t[:-2] + "xx", "manage")
        check("tamper is rejected", False)
    except tokens.BadToken:
        check("tamper is rejected", True)
    try:
        tokens.verify(tokens.sign({"a": 1}, ttl=-10))
        check("expiry is enforced", False)
    except tokens.BadToken:
        check("expiry is enforced", True)

    # --- config ---
    c = subsconfig.load()
    check("config loads with tiers", set(c["tiers"]) == {"individual", "team"})
    check("config defaults present", c["retention_days_unconfirmed"] == 7)
    check("problems() lists blockers", "subscriptions.enabled is false" in subsconfig.problems())

    # --- CRUD ---
    sid = S.create_pending("Alice@Example.com ", "team", source="stripe",
                           stripe_customer_id="cus_1", cadence="weekly")
    sub = S.get(sid)
    check("create_pending lowercases email", sub["email"] == "alice@example.com")
    check("cadence stored", S.get(sid, hydrate=False)["cadence"] == "weekly")

    itok = tokens.sign({"x": 1}, ttl=1).split(".")[0][:12] + "T"
    S.create_intent(itok, "z@ex.com", "individual", "monthly", "discord",
                    "https://discord.com/api/webhooks/1/x", ip="203.0.113.1")
    check("signup intent round-trips", S.get_intent(itok)["channel_kind"] == "discord")
    S.consume_intent(itok)
    check("consumed intent is gone", S.get_intent(itok) is None)
    check("email channel auto-created", any(ch["kind"] == "email" for ch in sub["channels"]))
    check("prefs row auto-created", sub["prefs"]["digest"] is True)

    S.set_stripe_ids(sid, subscription_id="sub_1", period_end=1900000000)
    S.activate(sid, "sub_1", 1900000000)
    check("activate sets status", S.get(sid)["status"] == "active")
    check("confirmed_at stamped", S.get(sid, hydrate=False)["confirmed_at"] is not None)

    S.log_consent(sid, "signup", True, ip="203.0.113.9", user_agent="pytest")
    check("consent logged", len(S.consents_for(sid)) == 1)

    S.set_prefs(sid, vendors=["Fortinet", "Cisco"], min_score_override=80)
    p = S.get_prefs(sid)
    check("prefs vendors saved", p["vendors"] == ["Fortinet", "Cisco"] and p["min_score_override"] == 80)

    S.add_channel(sid, "telegram", "123456", verified=True, label="phone")
    check("channel added", len(S.channels_for(sid)) == 2)

    check("dedupe: not delivered yet", not S.already_delivered(sid, "https://x/1"))
    S.record_delivery(sid, "https://x/1", "email", "instant", ok=True)
    check("dedupe: now delivered", S.already_delivered(sid, "https://x/1"))

    check("stripe event idempotency", not S.seen_stripe_event("evt_1"))
    S.mark_stripe_event("evt_1")
    check("stripe event marked", S.seen_stripe_event("evt_1"))

    S.create_pending("bob@example.com", "individual")
    check("list filters by status", len(S.list_subscribers(status="pending_payment")) == 1)
    check("active_for_delivery returns hydrated", S.active_for_delivery()[0]["prefs"]["vendors"] == ["Fortinet", "Cisco"])

    ex = S.export_subscriber(sid)
    check("DSAR export has all sections",
          {"subscriber", "channels", "prefs", "consents", "deliveries", "events"} <= set(ex))
    S.erase_subscriber(sid)
    check("DSAR erase removes the row", S.get(sid) is None)
    check("erase leaves anon audit event",
          any(e["kind"] == "erased" for e in d.query("SELECT kind FROM events")))

    st = S.stats()
    check("stats() runs", "by_status" in st)

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} failed: {', '.join(FAILED)}")
        return 1
    print("✅ Phase-0 self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
