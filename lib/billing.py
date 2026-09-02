#!/usr/bin/env python3
"""Stripe billing — REST via `requests`, no `stripe` SDK.

Checkout (hosted) + Customer Portal (hosted) so no card data ever touches us.
`handle_event()` turns a verified webhook event into subscriber-store changes
and returns an action hint for the caller (send confirm email / notify owner).

Test mode: use sk_test_ keys with subscriptions.live=false. Nothing here charges
anyone — Stripe does, and only for real prices you configure.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

import requests

import subsconfig  # type: ignore

API = "https://api.stripe.com/v1"
SIG_TOLERANCE = 300  # seconds


class StripeError(RuntimeError):
    pass


class WebhookError(RuntimeError):
    pass


def _key() -> str:
    k = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not k:
        raise StripeError("STRIPE_SECRET_KEY not set")
    return k


def _req(method: str, path: str, data: dict | None = None) -> dict:
    try:
        r = requests.request(method, f"{API}{path}",
                             auth=(_key(), ""), data=_flatten(data or {}), timeout=25)
    except requests.RequestException as e:
        raise StripeError(f"network: {e}") from e
    if r.status_code >= 400:
        msg = ""
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        raise StripeError(f"HTTP {r.status_code}: {msg}")
    return r.json()


def _flatten(d: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Stripe wants bracketed form encoding: a[b][c]=v ."""
    out: list[tuple[str, str]] = []
    for k, v in d.items():
        key = f"{prefix}[{k}]" if prefix else k
        if isinstance(v, dict):
            out += _flatten(v, key)
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out += _flatten(item, f"{key}[{i}]")
                else:
                    out.append((f"{key}[{i}]", str(item)))
        elif isinstance(v, bool):
            out.append((key, "true" if v else "false"))
        elif v is not None:
            out.append((key, str(v)))
    return out


# --------------------------------------------------------------------------- checkout / portal
def create_checkout_session(tier: str, cadence: str, email: str | None, success_url: str,
                            cancel_url: str, client_reference: str | None = None) -> dict:
    cadence = cadence if cadence in ("monthly", "weekly") else "monthly"
    pid = subsconfig.price_id(tier, cadence)
    if not pid:
        raise StripeError(f"tier '{tier}' has no {cadence} Stripe price configured")
    payload = {
        "mode": "subscription",
        "line_items": [{"price": pid, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": True,
        "billing_address_collection": "auto",
        "subscription_data": {"metadata": {"tier": tier, "cadence": cadence}},
        "metadata": {"tier": tier, "cadence": cadence},
        "consent_collection": {"terms_of_service": "required"},
    }
    if email:
        payload["customer_email"] = email
    if client_reference:
        payload["client_reference_id"] = client_reference
    s = _req("POST", "/checkout/sessions", payload)
    return {"id": s["id"], "url": s["url"]}


def portal_session(customer_id: str, return_url: str) -> dict:
    p = _req("POST", "/billing_portal/sessions",
             {"customer": customer_id, "return_url": return_url})
    return {"url": p["url"]}


def get_subscription(sub_id: str) -> dict:
    return _req("GET", f"/subscriptions/{sub_id}")


def cancel_subscription(sub_id: str, at_period_end: bool = False) -> dict:
    if at_period_end:
        return _req("POST", f"/subscriptions/{sub_id}", {"cancel_at_period_end": True})
    return _req("DELETE", f"/subscriptions/{sub_id}")


# --------------------------------------------------------------------------- webhook verify
def verify_webhook(payload: bytes, sig_header: str, secret: str | None = None) -> dict:
    secret = secret or os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise WebhookError("STRIPE_WEBHOOK_SECRET not set")
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    ts = parts.get("t")
    v1 = parts.get("v1")
    if not (ts and v1):
        raise WebhookError("malformed Stripe-Signature header")
    if abs(time.time() - int(ts)) > SIG_TOLERANCE:
        raise WebhookError("timestamp outside tolerance")
    signed = f"{ts}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, v1):
        raise WebhookError("signature mismatch")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise WebhookError(f"bad JSON: {e}") from e


# --------------------------------------------------------------------------- event -> store
def _price_id_from_subscription_obj(obj: dict) -> str | None:
    try:
        return obj["items"]["data"][0]["price"]["id"]
    except (KeyError, IndexError, TypeError):
        return None


def handle_event(event: dict) -> dict:
    """Apply a verified Stripe event to the subscriber store.
    Returns {"action": <hint>, ...} for the caller to act on.
    Import subscribers lazily so lib import order stays flexible."""
    import subscribers as S

    eid = event.get("id", "")
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    p2t = subsconfig.price_to_tier()

    if eid and S.seen_stripe_event(eid):
        return {"action": "duplicate", "type": etype}

    result: dict = {"action": "ignored", "type": etype}

    if etype == "checkout.session.completed":
        email = (obj.get("customer_details") or {}).get("email") or obj.get("customer_email")
        customer = obj.get("customer")
        subscription = obj.get("subscription")
        meta = obj.get("metadata") or {}
        tier, cadence = meta.get("tier"), meta.get("cadence")
        client_ref = obj.get("client_reference_id")
        intent = S.get_intent(client_ref) if client_ref else None
        if intent:
            tier = tier or intent["tier"]
            cadence = cadence or intent["cadence"]
            email = email or intent["email"]
        if (not tier or not cadence) and subscription:
            try:
                sub_obj = get_subscription(subscription)
                t = p2t.get(_price_id_from_subscription_obj(sub_obj))
                if t:
                    tier, cadence = tier or t[0], cadence or t[1]
            except StripeError:
                pass
        tier = tier or "individual"
        cadence = cadence if cadence in ("monthly", "weekly") else "monthly"
        if email:
            sid = S.create_pending(email, tier, source="stripe", stripe_customer_id=customer,
                                   cadence=cadence)
            S.set_stripe_ids(sid, customer_id=customer, subscription_id=subscription)
            S.set_status(sid, "unconfirmed", "checkout complete")
            S.log_event("stripe:checkout_completed", sid, {"session": obj.get("id"), "cadence": cadence})
            ch = None
            if intent and intent.get("channel_kind"):
                ch = {"kind": intent["channel_kind"], "target": intent.get("channel_target") or ""}
                if ch["target"]:
                    S.add_channel(sid, ch["kind"], ch["target"], verified=False, label="signup")
                S.consume_intent(client_ref)
            result = {"action": "send_confirm", "subscriber_id": sid, "email": email,
                      "tier": tier, "cadence": cadence, "channel": ch}
        else:
            result = {"action": "error", "detail": "no email on checkout session"}

    elif etype == "customer.subscription.updated":
        sub = S.get_by_stripe_subscription(obj.get("id", "")) \
            or S.get_by_stripe_customer(obj.get("customer", ""))
        if sub:
            period_end = obj.get("current_period_end")
            S.set_stripe_ids(sub["id"], subscription_id=obj.get("id"), period_end=period_end)
            status = obj.get("status")
            if status == "past_due":
                S.set_status(sub["id"], "past_due", "stripe: past_due")
                result = {"action": "notify_owner", "detail": f"{sub['email']} past_due"}
            elif status in ("canceled", "unpaid", "incomplete_expired"):
                S.set_status(sub["id"], "cancelled", f"stripe: {status}")
                result = {"action": "cancelled", "subscriber_id": sub["id"]}
            elif status == "active" and sub["status"] in ("past_due",):
                S.set_status(sub["id"], "active", "stripe: recovered")
                result = {"action": "recovered", "subscriber_id": sub["id"]}
            else:
                result = {"action": "synced", "subscriber_id": sub["id"]}

    elif etype == "customer.subscription.deleted":
        sub = S.get_by_stripe_subscription(obj.get("id", "")) \
            or S.get_by_stripe_customer(obj.get("customer", ""))
        if sub:
            S.set_status(sub["id"], "cancelled", "stripe: subscription deleted")
            result = {"action": "cancelled", "subscriber_id": sub["id"]}

    elif etype == "invoice.payment_failed":
        sub = S.get_by_stripe_customer(obj.get("customer", ""))
        if sub:
            S.set_status(sub["id"], "past_due", "stripe: payment failed")
            result = {"action": "notify_owner", "detail": f"{sub['email']} payment failed"}

    elif etype in ("invoice.paid", "invoice.payment_succeeded"):
        sub = S.get_by_stripe_customer(obj.get("customer", ""))
        if sub and sub["status"] == "past_due":
            S.set_status(sub["id"], "active", "stripe: payment recovered")
            result = {"action": "recovered", "subscriber_id": sub["id"]}

    if eid:
        S.mark_stripe_event(eid)
    return result


if __name__ == "__main__":
    import sys
    print(json.dumps(subsconfig.price_to_tier(), indent=2))
    if "--check" in sys.argv:
        try:
            _req("GET", "/balance")
            print("Stripe key OK")
        except StripeError as e:
            print(f"Stripe key check failed: {e}")
