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
import re
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
# Three complete tags reach further than five on LinkedIn; more reads as
# tag-stuffing and gets down-ranked. Matches prompts/analyst.md.
HASHTAGS = "#CyberSecurity #ThreatIntel #BlueTeam"
SITE = "https://securitycyber.uk/cti"
# discord.gg/r5ckQe33B had expired by 2026-10-08. This one expires
# 2026-11-07 — swap in a never-expiring invite; empty = invite posts skip.
DISCORD_INVITE = "https://discord.gg/BP8Y2m9nH"
URL_RE = re.compile(r"https?://\S+")
# Local windows where a UK working audience is actually on the feed. A promo
# post at 21:00 spends a slot for almost no reach.
WINDOWS = ((7, 0, 9, 30), (11, 30, 13, 30), (16, 30, 18, 30))


def _in_window(now: datetime) -> bool:
    hm = (now.hour, now.minute)
    return any((a, b) <= hm <= (c, d) for a, b, c, d in WINDOWS)


def _delink(text: str) -> str:
    """De-hyperlink for LinkedIn: drop the scheme, keep the domain.

    A full `https://…` URL in the body gets the post's reach suppressed, but
    stripping the link outright leaves the post with no route to the site at all
    (our token cannot post the first comment — it 403s). `securitycyber.uk/cti`
    is not auto-linked, so the post keeps its distribution and the reader can
    still act on it.
    """
    out = [URL_RE.sub(lambda m: re.sub(r"^https?://(?:www\.)?", "", m.group(0)), ln)
           for ln in text.splitlines()]
    while out and not out[-1].strip():
        out.pop()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def intel_lure(limit: int = 3) -> tuple[str, str] | None:
    """A proof-of-work post built from the briefs we actually shipped today.

    The strongest lure we have is not a claim about the product, it is the
    product's output: real headlines, real scores, and the count of what the
    reader is not seeing. Titles and scores only — the analysis stays paid.
    """
    dec = _load(STATE / "decision.json", {})
    alerts = [a for a in (dec.get("alerts") or []) if a.get("title")]
    if len(alerts) < 6:
        return None                      # too thin to be impressive; use the bank
    top = sorted(alerts, key=lambda a: int(a.get("score") or 0), reverse=True)[:limit]
    lines = [f"\u2022 {str(a['title']).strip()[:105]} \u2014 {int(a.get('score') or 0)}/100"
             for a in top]
    rest = len(alerts) - len(top)
    return (
        "What went into the free CTI feed today, top three by impact:\n\n"
        + "\n".join(lines)
        + f"\n\nPlus {rest} more, each with the affected versions, how to check "
          "whether you are exposed, and the one action to take.\n\n"
          # Bare domain, no scheme: LinkedIn does not auto-link it, so the post
          # keeps its reach and the reader still has a route (our token cannot
          # post the first comment - it 403s).
          "Free, no sign-up: cti.securitycyber.uk",
        f"Read the free feed: {SITE}",
    )


_INVITE_TEXTS = [
    "I run a live channel where defenders talk "
    "through what's actually being exploited today — same feed, more "
    "back-and-forth on what's worth acting on. Open to anyone:\n\n"
    f"{DISCORD_INVITE}",

    "A look behind the daily posts: the Discord carries every alert as it "
    "lands, plus the reasoning behind why something scores the way it "
    "does. Come see it:\n\n"
    f"{DISCORD_INVITE}",

    "CVSS isn't the whole story on most of what we post — the Discord is "
    "where that gets discussed properly, with other people doing the same "
    "job you are. No pitch, just the conversation:\n\n"
    f"{DISCORD_INVITE}",
]


def community_invite() -> tuple[str, str]:
    """A soft, low-frequency nudge toward the Discord community — value and a
    link, no ask, no urgency. Never posted back into Discord itself."""
    return random.choice(_INVITE_TEXTS), f"Join the community: {DISCORD_INVITE}"


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

    # Outside the windows a promo post reaches almost nobody. Skip the slot
    # rather than spend a bank entry on it.
    if _truthy(mk.get("respect_windows", True)) and not _in_window(datetime.now()):
        print(f"[marketing] {datetime.now():%H:%M} local is outside the posting "
              f"windows \u2014 skipping this slot")
        return 0

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
    rot = _load(ROT, {})
    is_invite = False
    # Rarest slot: a soft nudge toward the Discord community. This is growth,
    # not product pitch, so it gets far less airtime than the lure — every 6th
    # slot (~4/day cadence -> roughly every day and a half), never back to back
    # with itself, and it never fires without a real invite link configured.
    since_invite = int(rot.get("since_invite", 0)) + 1
    if DISCORD_INVITE and since_invite >= 6:
        text, comment = community_invite()
        idx = -1
        since_invite = 0
        is_invite = True
        print("[marketing] slot -> community invite")
    else:
        # Every third remaining slot is a proof-of-work lure built from today's
        # real briefs — far more persuasive than another feature pitch, and it
        # costs nothing to produce. Falls back to the bank when the run was
        # too thin to show off.
        since_lure = int(rot.get("since_lure", 0)) + 1
        lure = intel_lure() if since_lure >= 3 else None
        if lure:
            text, comment = lure
            idx = -1
            since_lure = 0
            print("[marketing] slot -> intel lure (today's shipped briefs)")
        else:
            idx, text = pick_post(bank)
            if idx < 0:
                print("[marketing] content bank is empty", file=sys.stderr)
                return 1
            comment = f"Read the free feed: {bank.get('url', SITE)}"
        rot = _load(ROT, {})      # pick_post may have rewritten it; keep `used`
        rot["since_lure"] = since_lure
    rot["since_invite"] = since_invite
    ROT.write_text(json.dumps(rot))

    # LinkedIn gets no link in the body (it throttles reach) — the URL goes in
    # the first comment. Every other network keeps it inline.
    body_li = _delink(text).rstrip() + "\n\n" + HASHTAGS
    body = text.rstrip() + "\n\n" + HASHTAGS
    short = (text.split("\n", 1)[0].strip()[:230] + " " + bank.get("url", SITE)).strip()

    targets = publishers.resolve_targets(settings, creds)
    targets = [t for t in targets if t in publishers.configured(creds)]
    if is_invite:
        # Never invite people already in the room.
        targets = [t for t in targets if t != "discord"]
    print(f"[marketing] post #{idx} -> {', '.join(targets) or '(no targets)'}"
          + ("  [DRY-RUN]" if dry else ""))

    if dry or not targets:
        with LOG.open("a") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "idx": idx,
                                 "dry_run": True, "targets": targets, "text": text[:120]}) + "\n")
        print(body_li if "linkedin" in targets else body)
        print(f"[marketing] first comment -> {comment}")
        return 0

    li = [t for t in targets if t == "linkedin"]
    others = [t for t in targets if t != "linkedin"]
    results = []
    if li:
        results += publishers.post_all(body_li, creds, settings, short_text=short,
                                       comment=comment, only=li, log=print)
    if others:
        results += publishers.post_all(body, creds, settings, short_text=short,
                                       only=others, log=print)
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
