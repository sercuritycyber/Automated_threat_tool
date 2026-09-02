#!/usr/bin/env python3
"""Phase-2 self-test: Stripe webhook verification + event → store handling.
No network — synthetic events, temp DB."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "bin"))

FAILED = []


def check(name, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILED.append(name)


def sign(payload: bytes, secret: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def main() -> int:
    tmp = tempfile.mkdtemp()
    os.environ["SUBS_DB_PATH"] = str(Path(tmp) / "t.db")
    os.environ["SUBS_TOKEN_SECRET"] = "x" * 20
    os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_123"
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy"
    os.environ.pop("TURSO_DATABASE_URL", None)

    import db as dbmod
    import subscribers as S
    import subsconfig
    import billing

    dbmod.reset_db_singleton()
    S.ensure_schema()

    subsconfig.price_to_tier = lambda: {"price_indiv": ("individual", "monthly"),
                                        "price_team": ("team", "monthly")}
    subsconfig.load = lambda: {"tiers": {
        "individual": {"monthly_price_id": "price_indiv", "instant_min_score": 90},
        "team": {"monthly_price_id": "price_team", "instant_min_score": 75}}}
    subsconfig.price_id = lambda t, c: {"individual": "price_indiv", "team": "price_team"}.get(t, "")

    # --- signature verification ---
    body = json.dumps({"id": "evt_x", "type": "ping"}).encode()
    good = sign(body, "whsec_test_123")
    check("valid signature verifies", billing.verify_webhook(body, good)["id"] == "evt_x")
    try:
        billing.verify_webhook(body, sign(body, "wrong_secret"))
        check("bad secret rejected", False)
    except billing.WebhookError:
        check("bad secret rejected", True)
    try:
        billing.verify_webhook(body, sign(body, "whsec_test_123", ts=int(time.time()) - 9999))
        check("stale timestamp rejected", False)
    except billing.WebhookError:
        check("stale timestamp rejected", True)
    try:
        billing.verify_webhook(body, "garbage")
        check("malformed header rejected", False)
    except billing.WebhookError:
        check("malformed header rejected", True)

    # --- form flattening ---
    flat = dict(billing._flatten({"a": {"b": "c"}, "items": [{"price": "p", "quantity": 1}], "ok": True}))
    check("nested form encoding", flat.get("a[b]") == "c" and flat.get("items[0][price]") == "p"
          and flat.get("ok") == "true")

    # --- checkout.session.completed ---
    ev1 = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "customer": "cus_1", "subscription": "sub_1",
        "customer_details": {"email": "New@Example.com"}, "metadata": {"tier": "team"}}}}
    r = billing.handle_event(ev1)
    check("checkout → send_confirm action", r["action"] == "send_confirm")
    sub = S.get_by_email("new@example.com")
    check("subscriber created, unconfirmed", sub and sub["status"] == "unconfirmed")
    check("stripe ids stored", sub["stripe_customer_id"] == "cus_1" and sub["stripe_subscription_id"] == "sub_1")
    check("tier from metadata", sub["tier"] == "team")

    # --- idempotency: same event again ---
    check("duplicate event ignored", billing.handle_event(ev1)["action"] == "duplicate")

    # confirm the subscriber (simulate email opt-in)
    S.set_status(sub["id"], "active", "email confirmed")

    # --- payment failed ---
    ev2 = {"id": "evt_2", "type": "invoice.payment_failed",
           "data": {"object": {"customer": "cus_1"}}}
    r = billing.handle_event(ev2)
    check("payment_failed → past_due + notify_owner", r["action"] == "notify_owner"
          and S.get(sub["id"])["status"] == "past_due")

    # --- recovered ---
    ev3 = {"id": "evt_3", "type": "invoice.paid", "data": {"object": {"customer": "cus_1"}}}
    r = billing.handle_event(ev3)
    check("invoice.paid → recovered/active", S.get(sub["id"])["status"] == "active")

    # --- subscription updated: past_due status ---
    ev4 = {"id": "evt_4", "type": "customer.subscription.updated", "data": {"object": {
        "id": "sub_1", "customer": "cus_1", "status": "past_due", "current_period_end": 1900000000}}}
    billing.handle_event(ev4)
    check("subscription.updated past_due syncs status", S.get(sub["id"])["status"] == "past_due")
    check("period_end synced", S.get(sub["id"], hydrate=False)["current_period_end"] == 1900000000)

    # --- subscription deleted ---
    ev5 = {"id": "evt_5", "type": "customer.subscription.deleted", "data": {"object": {
        "id": "sub_1", "customer": "cus_1"}}}
    r = billing.handle_event(ev5)
    check("subscription.deleted → cancelled", r["action"] == "cancelled"
          and S.get(sub["id"])["status"] == "cancelled")

    # --- checkout with no tier metadata falls back ---
    ev6 = {"id": "evt_6", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_2", "customer": "cus_2", "subscription": "sub_2",
        "customer_details": {"email": "b@example.com"}, "metadata": {}}}}
    billing.get_subscription = lambda sid: {"items": {"data": [{"price": {"id": "price_indiv"}}]}}
    r = billing.handle_event(ev6)
    b = S.get_by_email("b@example.com")
    check("tier + cadence inferred from price when metadata missing",
          b["tier"] == "individual" and b["cadence"] == "monthly")

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} failed: {', '.join(FAILED)}")
        return 1
    print("✅ Phase-2 self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
