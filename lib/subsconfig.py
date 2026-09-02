#!/usr/bin/env python3
"""Load + validate the `subscriptions:` block of config/settings.yaml.

Everything has a safe default so a half-filled config never crashes a route.
`enabled` gates the whole feature; `live` gates real Stripe charges.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "config" / "settings.yaml"

DEFAULTS: dict = {
    "enabled": False,
    "live": False,
    "brand_name": "Blue Team Podcast",
    "site_url": "http://localhost:8080",
    "public_bind": "127.0.0.1:8080",
    "from_email": "",
    "reply_to": "",
    "ico_registration": "",
    "company_legal_name": "",
    "contact_email": "",
    "support_email": "",
    "retention_days_cancelled": 180,
    "retention_days_unconfirmed": 7,
    "digest_hour_utc": 8,
    "rate_limit_per_min": 20,
    "tiers": {
        "individual": {
            "price_id": "",
            "display_price": "£8 / month",
            "blurb": "Instant alerts for anything scoring 90+, plus a daily digest.",
            "instant_min_score": 90,
            "daily_digest": True,
            "attach_json": False,
            "channels": ["email", "telegram", "ntfy"],
            "max_channels": 2,
        },
        "team": {
            "price_id": "",
            "display_price": "£18 / month",
            "blurb": "Everything scoring 75+ instantly, a daily digest, machine-readable JSON, and Slack.",
            "instant_min_score": 75,
            "daily_digest": True,
            "attach_json": True,
            "channels": ["email", "slack", "telegram", "webhook", "ntfy"],
            "max_channels": 6,
        },
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    try:
        raw = yaml.safe_load(SETTINGS.read_text()) or {}
    except OSError:
        raw = {}
    cfg = _merge(DEFAULTS, raw.get("subscriptions", {}))
    # normalise tiers: keep only known tier keys, merge each onto its default
    tiers = {}
    for name, tdefault in DEFAULTS["tiers"].items():
        tiers[name] = _merge(tdefault, (raw.get("subscriptions", {}).get("tiers", {}) or {}).get(name, {}))
    cfg["tiers"] = tiers
    return cfg


def tier(name: str) -> dict:
    return load()["tiers"].get(name, load()["tiers"]["individual"])


def price_to_tier() -> dict[str, str]:
    return {t["price_id"]: name for name, t in load()["tiers"].items() if t.get("price_id")}


def problems() -> list[str]:
    """Human-readable list of what's stopping this from going live. Empty = ready."""
    import os
    c = load()
    out = []
    if not c["enabled"]:
        out.append("subscriptions.enabled is false")
    if not c["from_email"]:
        out.append("subscriptions.from_email is not set")
    if not c["contact_email"]:
        out.append("subscriptions.contact_email is not set (required in the privacy policy)")
    if not c["company_legal_name"]:
        out.append("subscriptions.company_legal_name is not set (required in the terms)")
    if not c["ico_registration"]:
        out.append("subscriptions.ico_registration is not set — register with the ICO first")
    for name, t in c["tiers"].items():
        if not t.get("price_id"):
            out.append(f"tier '{name}' has no Stripe price_id")
    if c["live"]:
        if not os.getenv("STRIPE_SECRET_KEY", "").startswith("sk_live_"):
            out.append("subscriptions.live is true but STRIPE_SECRET_KEY is not an sk_live_ key")
        if not os.getenv("STRIPE_WEBHOOK_SECRET"):
            out.append("STRIPE_WEBHOOK_SECRET is not set")
    else:
        if not os.getenv("STRIPE_SECRET_KEY"):
            out.append("STRIPE_SECRET_KEY is not set (use an sk_test_ key while live=false)")
    return out


if __name__ == "__main__":
    import json
    c = load()
    print(json.dumps(c, indent=2))
    print("\nblockers:")
    for p in problems():
        print("  -", p)
