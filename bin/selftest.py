#!/usr/bin/env python3
"""Pre-flight for the whole stack — run before going live.

Read-only. Tells you what's wired, what's missing, and whether you can take a
subscriber. Exit 0 if the paid service is ready (or intentionally disabled),
1 if `subscriptions.enabled` is true but something's incomplete.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "bin"))

G, R, Y, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def ok(m): print(f"  {G}✓{X} {m}")
def warn(m): print(f"  {Y}!{X} {m}")
def bad(m): print(f"  {R}✗{X} {m}")
def hd(m): print(f"\n{m}")


def _env() -> dict:
    d = dict(os.environ)
    f = ROOT / "config" / "env"
    if f.exists():
        for ln in f.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d.setdefault(k.strip(), v.strip())
    return d


def main() -> int:
    import subsconfig
    import subscribers as S
    import db as dbmod
    import publishers

    env = _env()
    cfg = subsconfig.load()
    hard_fail = False

    hd("Core pipeline")
    ok(f"claude CLI: {shutil.which('claude') or R + 'NOT FOUND' + X}")
    for mod in ("fastapi", "uvicorn", "requests", "yaml"):
        try:
            __import__(mod)
        except ImportError:
            bad(f"python module '{mod}' missing")
            hard_fail = True
    ok("python deps present") if not hard_fail else None
    conf = publishers.configured(env)
    (ok if conf else warn)(f"social targets configured: {', '.join(conf) or 'none'}")

    hd("Subscription database")
    try:
        d = dbmod.get_db()
        S.ensure_schema()
        st = S.stats()
        ok(f"backend = {d.backend}, schema v{d.schema_version()}, "
           f"{st['total_active']} active subscriber(s)")
        if d.backend == "sqlite" and (cfg.get("enabled")):
            warn("using LOCAL sqlite for PII while enabled — set TURSO_DATABASE_URL + "
                 "TURSO_AUTH_TOKEN for the production (EU) store")
    except Exception as ex:  # noqa
        bad(f"database not reachable: {ex}")
        hard_fail = True

    hd("Subscription config")
    if not cfg.get("enabled"):
        warn("subscriptions.enabled = false — public site & delivery are OFF (that's fine if intended)")
    probs = subsconfig.problems()
    if probs:
        for p in probs:
            bad(p)
    else:
        ok("config is complete")

    hd("Billing (Stripe)")
    sk = env.get("STRIPE_SECRET_KEY", "")
    if sk.startswith("sk_live_"):
        ok("live secret key set")
    elif sk.startswith("sk_test_"):
        warn("TEST secret key (fine until you go live)")
    else:
        (bad if cfg.get("enabled") else warn)("STRIPE_SECRET_KEY not set")
    (ok if env.get("STRIPE_WEBHOOK_SECRET") else (bad if cfg.get("live") else warn))(
        "STRIPE_WEBHOOK_SECRET " + ("set" if env.get("STRIPE_WEBHOOK_SECRET") else "not set"))
    for name, t in cfg["tiers"].items():
        got = [c for c in ("monthly", "weekly") if t.get(f"{c}_price_id")]
        (ok if got else bad)(f"tier '{name}' Stripe prices: {', '.join(got) or 'NONE'}")

    hd("Email delivery")
    if env.get("EMAIL_SMTP_HOST"):
        ok(f"SMTP host {env['EMAIL_SMTP_HOST']}:{env.get('EMAIL_SMTP_PORT', '587')}")
        (ok if (cfg.get("from_email") or env.get("EMAIL_FROM")) else bad)("from address set")
    else:
        (bad if cfg.get("enabled") else warn)("EMAIL_SMTP_HOST not set — no email can be sent")

    hd("Channels")
    ok("Telegram bot configured") if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_BOT_USERNAME") \
        else warn("Telegram not configured (optional)")

    hd("Legal / ICO")
    (ok if cfg.get("ico_registration") else bad)("ICO registration number: "
                                                 + (cfg.get("ico_registration") or "NOT SET — register at ico.org.uk"))
    (ok if cfg.get("company_legal_name") else bad)("legal name set")
    (ok if cfg.get("contact_email") else bad)("privacy contact email set")
    for doc in ("privacy-policy", "terms", "cookies", "compliance-runbook"):
        (ok if (ROOT / "docs" / "legal" / f"{doc}.md").exists() else bad)(f"docs/legal/{doc}.md")

    hd("Deployment")
    ok(f"caddy: {shutil.which('caddy')}") if shutil.which("caddy") else warn("caddy not installed (needed for public TLS)")
    unit_dir = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd" / "user"
    for u in ("cti-public.service", "cti-digest.timer", "cti-retention.timer"):
        (ok if (unit_dir / u).exists() else warn)(f"{u} " + ("installed" if (unit_dir / u).exists() else "not installed — run systemd/install-public.sh"))

    hd("Verdict")
    blocking = bool(probs) or hard_fail
    if not cfg.get("enabled"):
        print("  subscriptions are disabled — nothing to be ready for. Enable in /subscriptions when set up.")
        return 0
    if blocking:
        print(f"  {R}NOT ready{X} — resolve the ✗ items above.")
        return 1
    print(f"  {G}Ready to take subscribers.{X}"
          + ("  (still in Stripe TEST mode — run a full test checkout, then set live)"
             if not cfg.get("live") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
