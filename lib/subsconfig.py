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
    "brief": {
        "price_id": "",
        "name": "SecurityCyber — on-demand threat brief",
        "currency": "gbp",
        "unit_amount": 4900,
        "display": "£49",
        "blurb": "A one-off analyst-written deep dive on a vulnerability or incident of your "
                 "choosing — impact, exposure, detection and response — by email within two "
                 "working days.",
    },
    "tiers": {
        "individual": {
            "monthly_price_id": "",
            "weekly_price_id": "",
            "monthly_display": "£8 / month",
            "weekly_display": "£2 / week",
            "monthly_pence": 800,
            "weekly_pence": 200,
            "blurb": "Instant alerts for anything scoring 90+, plus a daily digest.",
            "instant_min_score": 90,
            "daily_digest": True,
            "attach_json": False,
            "channels": ["email", "discord", "telegram", "ntfy"],
            "max_channels": 2,
        },
        "team": {
            "monthly_price_id": "",
            "weekly_price_id": "",
            "monthly_display": "£18 / month",
            "weekly_display": "£5 / week",
            "monthly_pence": 1800,
            "weekly_pence": 500,
            "blurb": "Everything scoring 75+ instantly, a daily digest, machine-readable JSON, Slack and Discord.",
            "instant_min_score": 75,
            "daily_digest": True,
            "attach_json": True,
            "channels": ["email", "discord", "slack", "telegram", "webhook", "ntfy"],
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


def price_to_tier() -> dict[str, tuple[str, str]]:
    """{stripe_price_id: (tier_name, cadence)} for every configured price."""
    out: dict[str, tuple[str, str]] = {}
    for name, t in load()["tiers"].items():
        for cad in ("monthly", "weekly"):
            pid = t.get(f"{cad}_price_id")
            if pid:
                out[pid] = (name, cad)
    return out


def price_id(tier_name: str, cadence: str) -> str:
    return load()["tiers"].get(tier_name, {}).get(f"{cadence}_price_id", "")


def tier_pence(tier_name: str, cadence: str) -> int:
    return int(load()["tiers"].get(tier_name, {}).get(f"{cadence}_pence", 0) or 0)


def monthly_pence(tier_name: str, cadence: str) -> int:
    """A tier's revenue normalised to per-month, for MRR estimates."""
    p = tier_pence(tier_name, cadence)
    return round(p * 52 / 12) if cadence == "weekly" else p


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
        if not (t.get("monthly_price_id") or t.get("weekly_price_id")):
            out.append(f"tier '{name}' has no Stripe price id (monthly or weekly)")
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
