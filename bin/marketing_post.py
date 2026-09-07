#!/usr/bin/env python3
"""SecurityCyber marketing autoposter.

Posts ONE promotional post per invocation to the configured social targets
(LinkedIn, Bluesky, Telegram — whatever `publish.targets` has), rotating through
bin/marketing_content.json so nothing repeats until the bank is exhausted.
Every post carries https://securitycyber.uk/cti so the link-preview card (our
OG image) renders — we can't attach media through the APIs.

These are PROMO posts, not CTI alerts: they do NOT fan out to subscribers and
do NOT touch history.jsonl / the analyst pipeline. Runs from its own timer,
4x/day.

Guardrails:
  * marketing.enabled in settings.yaml must be true (default false)
  * a marketing post never goes out within `min_gap_minutes` of a CTI post,
    so the feed doesn't get two SecurityCyber posts back to back
  * dry-run honoured (settings.publish.dry_run / CTI_DRY_RUN)
"""
from __future__ import annotations

import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import yaml  # noqa: E402
import publishers  # noqa: E402

STATE = ROOT / "state"
BANK = ROOT / "bin" / "marketing_content.json"
ROT = STATE / "marketing_rotation.json"
LOG = STATE / "marketing_posts.jsonl"
HASHTAGS = "#CyberSecurity #ThreatIntelligence #BlueTeam #SECURITYCYBERUK"


def _load(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _creds() -> dict:
    out = {}
    envf = ROOT / "config" / "env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    out.update({k: os.environ[k] for k in os.environ if k.startswith(("LINKEDIN_", "BLUESKY_", "TELEGRAM_", "MASTODON_", "X_"))})
    return out


def pick_post(bank: dict) -> tuple[int, str]:
    posts = bank.get("posts", [])
    if not posts:
        return -1, ""
    rot = _load(ROT, {})
    used = set(rot.get("used", []))
    if len(used) >= len(posts):
        used = set()
    pool = [i for i in range(len(posts)) if i not in used]
    idx = random.choice(pool)
    used.add(idx)
    ROT.write_text(json.dumps({"used": sorted(used), "updated": datetime.now(timezone.utc).isoformat()}))
    return idx, posts[idx]


def main() -> int:
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}
    mk = settings.get("marketing", {}) if isinstance(settings.get("marketing"), dict) else {}
    if not _truthy(mk.get("enabled", False)):
        print("[marketing] disabled (settings.marketing.enabled != true) — nothing sent")
        return 0

    creds = _creds()
    dry = _truthy(os.environ.get("CTI_DRY_RUN", settings.get("publish", {}).get("dry_run", True)))

    # don't stack on top of a CTI post
    gap = int(mk.get("min_gap_minutes", 45))
    hist = STATE / "history.jsonl"
    if hist.exists() and gap > 0:
        try:
            rows = [json.loads(x) for x in hist.read_text().splitlines() if x.strip()]
            if rows:
                last = datetime.fromisoformat(rows[-1].get("posted_at", "").replace("Z", "+00:00"))
                mins = (datetime.now(timezone.utc) - last).total_seconds() / 60
                if 0 <= mins < gap:
                    print(f"[marketing] a CTI post went out {int(mins)}m ago (< {gap}m) — skipping this slot")
                    return 0
        except Exception:
            pass

    bank = _load(BANK, {})
    idx, text = pick_post(bank)
    if idx < 0:
        print("[marketing] content bank is empty", file=sys.stderr)
        return 1
    body = text.rstrip() + "\n\n" + HASHTAGS
    short = (text.split("\n", 1)[0].strip()[:230] + " " + bank.get("url", "")).strip()

    targets = publishers.resolve_targets(settings, creds)
    targets = [t for t in targets if t in publishers.configured(creds)]
    print(f"[marketing] post #{idx} -> {', '.join(targets) or '(no targets)'}"
          + ("  [DRY-RUN]" if dry else ""))

    if dry or not targets:
        with LOG.open("a") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "idx": idx,
                                 "dry_run": True, "targets": targets, "text": text[:120]}) + "\n")
        print(text)
        return 0

    results = publishers.post_all(body, creds, settings, short_text=short, only=targets, log=print)
    ok = [r["provider"] for r in results if r.get("ok")]
    fail = [f"{r['provider']}: {r['error']}" for r in results if not r.get("ok")]
    with LOG.open("a") as fh:
        fh.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(), "idx": idx,
            "posted": ok, "failed": fail,
            "urls": [r.get("url") for r in results if r.get("ok") and r.get("url")],
            "text": text[:200],
        }) + "\n")
    for r in results:
        print(f"  {r['provider']}: {'ok ' + (r.get('url') or '') if r.get('ok') else 'FAIL ' + r['error']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
