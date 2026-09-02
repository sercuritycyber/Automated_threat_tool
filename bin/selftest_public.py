#!/usr/bin/env python3
"""Phase-3 self-test: the public subscriber app, full lifecycle, mocked externals."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "bin"))

FAILED = []
EMAILS = []


def check(name, cond):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILED.append(name)


def main() -> int:
    tmp = tempfile.mkdtemp()
    os.environ.update(SUBS_DB_PATH=str(Path(tmp) / "t.db"), SUBS_TOKEN_SECRET="k" * 16,
                      STRIPE_WEBHOOK_SECRET="whsec_x", STRIPE_SECRET_KEY="sk_test_x",
                      TELEGRAM_BOT_USERNAME="cti_test_bot", TELEGRAM_BOT_TOKEN="123:abc")
    os.environ.pop("TURSO_DATABASE_URL", None)
    os.environ.pop("CTI_DRY_RUN", None)

    import db as dbmod
    import subscribers as S
    import subsconfig
    import billing
    import channels as CH
    import tokens
    from starlette.testclient import TestClient

    dbmod.reset_db_singleton()
    S.ensure_schema()

    subsconfig.load = lambda: {
        "enabled": True, "live": False, "brand_name": "BT CTI",
        "site_url": "https://cti.example", "rate_limit_per_min": 500,
        "company_legal_name": "Test Co", "ico_registration": "ZA000000",
        "contact_email": "privacy@example.com", "from_email": "alerts@example.com",
        "retention_days_cancelled": 180, "retention_days_unconfirmed": 7,
        "tiers": {
            "individual": {"monthly_price_id": "price_i", "weekly_price_id": "price_iw",
                           "monthly_display": "£8/mo", "weekly_display": "£2/wk",
                           "monthly_pence": 800, "weekly_pence": 200, "blurb": "x",
                           "instant_min_score": 90, "daily_digest": True, "attach_json": False,
                           "channels": ["email", "discord", "telegram", "ntfy"], "max_channels": 2},
            "team": {"monthly_price_id": "price_t", "weekly_price_id": "price_tw",
                     "monthly_display": "£18/mo", "weekly_display": "£5/wk",
                     "monthly_pence": 1800, "weekly_pence": 500, "blurb": "y",
                     "instant_min_score": 75, "daily_digest": True, "attach_json": True,
                     "channels": ["email", "discord", "slack", "telegram", "webhook", "ntfy"], "max_channels": 6},
        }}
    subsconfig.price_to_tier = lambda: {"price_i": ("individual", "monthly"), "price_iw": ("individual", "weekly"),
                                        "price_t": ("team", "monthly"), "price_tw": ("team", "weekly")}
    subsconfig.price_id = lambda t, cad: {("individual", "monthly"): "price_i", ("individual", "weekly"): "price_iw",
                                          ("team", "monthly"): "price_t", ("team", "weekly"): "price_tw"}.get((t, cad), "")
    subsconfig.problems = lambda: []

    import web.public_app as P
    P.subsconfig.load = subsconfig.load
    P.subsconfig.problems = subsconfig.problems
    P.subsconfig.price_id = subsconfig.price_id
    billing.subsconfig.price_to_tier = subsconfig.price_to_tier
    billing.subsconfig.load = subsconfig.load
    billing.subsconfig.price_id = subsconfig.price_id

    # mock stripe checkout + portal
    billing.create_checkout_session = lambda tier, cadence, email, success_url, cancel_url, client_reference=None: \
        {"id": "cs_1", "url": f"https://checkout.stripe.com/c/cs_1?tier={tier}&cad={cadence}&ref={client_reference}"}
    billing.portal_session = lambda cid, return_url: {"url": "https://billing.stripe.com/p/1"}
    billing.cancel_subscription = lambda sid, at_period_end=False: {"status": "canceled"}
    billing.get_subscription = lambda sid: {"items": {"data": [{"price": {"id": "price_t"}}]}}
    # mock stripe webhook signature — accept anything, return the parsed body
    billing.verify_webhook = lambda payload, sig, secret=None: json.loads(payload)

    # capture emails + channel sends
    def fake_send(kind, target, subject, body, **kw):
        if kind == "email":
            EMAILS.append({"to": target, "subject": subject, "body": body})
        return {"kind": kind, "ok": True, "ref": "mock", "error": ""}
    CH.send = fake_send
    P.CH.send = fake_send
    P.notify_owner = lambda m: None

    c = TestClient(P.app, follow_redirects=False)

    # --- landing shows tiers ---
    r = c.get("/")
    check("landing lists both tiers", "Individual" in r.text and "Team" in r.text)

    # --- subscribe form renders + POST redirects to Stripe with an intent ref ---
    r = c.get("/subscribe?tier=team")
    check("subscribe form renders with cadence + platform", r.status_code == 200
          and "name=cadence" in r.text and "Discord" in r.text and 'value="weekly"' in r.text)
    r = c.post("/subscribe", data={"tier": "team", "cadence": "weekly", "email": "amy@example.com",
                                   "platform": "discord",
                                   "target": "https://discord.com/api/webhooks/123/abc", "agree": "on"})
    check("subscribe POST -> Stripe with intent ref", r.status_code == 303
          and "checkout.stripe.com" in r.headers["location"] and "cad=weekly" in r.headers["location"])
    intent_ref = r.headers["location"].split("ref=")[1]
    check("signup intent stored", S.get_intent(intent_ref) is not None
          and S.get_intent(intent_ref)["channel_kind"] == "discord")

    # --- stripe webhook: checkout completed (carries the intent ref) ---
    ev = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "customer": "cus_1", "subscription": "sub_1", "client_reference_id": intent_ref,
        "customer_details": {"email": "amy@example.com"}, "metadata": {"tier": "team", "cadence": "weekly"}}}}
    r = c.post("/webhooks/stripe", content=json.dumps(ev), headers={"stripe-signature": "x"})
    check("webhook accepted", r.status_code == 200 and r.json()["action"] == "send_confirm")
    check("confirm email sent", any(m["to"] == "amy@example.com" and "Confirm" in m["subject"] for m in EMAILS))
    sub = S.get_by_email("amy@example.com")
    check("subscriber unconfirmed after checkout", sub["status"] == "unconfirmed")
    check("cadence from checkout = weekly", sub["cadence"] == "weekly")
    check("discord channel created from signup intent (unverified)",
          any(ch["kind"] == "discord" and not ch["verified"] for ch in S.channels_for(sub["id"])))
    check("intent consumed", S.get_intent(intent_ref) is None)

    # --- bad webhook signature ---
    billing.verify_webhook = lambda *a, **k: (_ for _ in ()).throw(billing.WebhookError("bad sig"))
    r = c.post("/webhooks/stripe", content="{}", headers={"stripe-signature": "x"})
    check("bad signature -> 400", r.status_code == 400)
    billing.verify_webhook = lambda payload, sig, secret=None: json.loads(payload)

    # --- confirm link ---
    ctok = tokens.sign({"sub": sub["id"], "act": "confirm"}, ttl=3600)
    r = c.get(f"/confirm?t={ctok}")
    check("confirm redirects to /manage", r.status_code == 303 and "/manage?t=" in r.headers["location"])
    sub = S.get(sub["id"])
    check("confirmed -> active", sub["status"] == "active")
    check("consent logged with ip + terms version",
          any(x["kind"] == "signup" and x["granted"] and x["terms_version"] for x in S.consents_for(sub["id"])))
    check("signup discord channel verified by test-ping on confirm",
          any(ch["kind"] == "discord" and ch["verified"] for ch in S.channels_for(sub["id"])))

    mtok = r.headers["location"].split("t=")[1].split("&")[0]

    # --- manage: set prefs ---
    r = c.get(f"/manage?t={mtok}")
    check("manage page renders", r.status_code == 200 and "Your alert preferences" in r.text)
    r = c.post("/manage/prefs", data={"t": mtok, "vendors": "Fortinet, Cisco", "min_score": "85",
                                      "instant": "on", "digest": "on"})
    check("prefs saved", r.status_code == 303)
    p = S.get_prefs(sub["id"])
    check("vendors + threshold stored", p["vendors"] == ["Fortinet", "Cisco"] and p["min_score_override"] == 85)

    # --- add a channel (ntfy) — test ping mocked ok ---
    r = c.post("/manage/channel/add", data={"t": mtok, "kind": "ntfy", "target": "https://ntfy.sh/mytopic"})
    check("ntfy channel added + verified", any(ch["kind"] == "ntfy" and ch["verified"]
                                               for ch in S.channels_for(sub["id"])))
    # channel not on plan rejected
    r = c.post("/manage/channel/add", data={"t": mtok, "kind": "telegram", "target": "123"})
    check("telegram not addable via form (needs bot link)",
          not any(ch["kind"] == "telegram" for ch in S.channels_for(sub["id"])))

    # --- telegram deep-link + webhook ---
    r = c.get(f"/channel/telegram?t={mtok}")
    check("telegram link page", "t.me/cti_test_bot?start=" in r.text)
    import re as _re
    start_payload = _re.search(r"start=([A-Za-z0-9_.\-]+)", r.text).group(1)
    r = c.post("/webhooks/telegram", content=json.dumps({"message": {
        "text": f"/start {start_payload}", "chat": {"id": 55501}}}),
        headers={"content-type": "application/json"})
    check("telegram /start links the chat",
          any(ch["kind"] == "telegram" and ch["target"] == "55501" and ch["verified"]
              for ch in S.channels_for(sub["id"])))

    # --- DSAR export ---
    r = c.get(f"/manage/export?t={mtok}")
    check("export returns JSON attachment", r.status_code == 200
          and "attachment" in r.headers.get("content-disposition", "")
          and json.loads(r.text)["subscriber"]["email"] == "amy@example.com")

    # --- unsubscribe (pause) ---
    utok = tokens.sign({"sub": sub["id"], "act": "unsubscribe"}, ttl=3600)
    r = c.get(f"/unsubscribe?t={utok}")
    check("unsubscribe pauses", S.get(sub["id"])["status"] == "paused" and r.status_code == 200)

    # --- billing portal ---
    r = c.post("/manage/portal", data={"t": mtok})
    check("portal redirects to Stripe", r.status_code == 303 and "billing.stripe.com" in r.headers["location"])

    # --- delete ---
    r = c.post("/manage/delete", data={"t": mtok})
    check("delete erases + confirms", r.status_code == 200 and S.get(sub["id"]) is None)

    # --- expired manage token ---
    bad = tokens.sign({"sub": 1, "act": "manage"}, ttl=-5)
    r = c.get(f"/manage?t={bad}")
    check("expired token -> friendly error", r.status_code == 200 and "expired" in r.text.lower())

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} failed: {', '.join(FAILED)}")
        return 1
    print("✅ Phase-3 self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
