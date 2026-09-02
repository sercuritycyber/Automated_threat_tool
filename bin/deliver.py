#!/usr/bin/env python3
"""Fan-out engine — pushes analyst posts to paying subscribers.

    bin/deliver.py --instant [--from state/decision.json]   # after each harvest
    bin/deliver.py --digest                                 # daily timer
    bin/deliver.py --test <subscriber_id>                   # send a test alert
    bin/deliver.py --list                                   # who's active

Gating:
  * subscriptions.enabled false            -> no-op
  * CTI_DRY_RUN truthy / --dry             -> log intended sends, send nothing
  * per subscriber: status active, channel verified, tier threshold, vendor
    filter, and not already delivered (deliveries table dedupes instant→digest)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "bin"))

import channels as CH          # noqa: E402
import subscribers as S        # noqa: E402
import subsconfig              # noqa: E402

STATE = ROOT / "state"
POSTS_LOG = STATE / "posts_log.jsonl"


def log(m: str) -> None:
    print(f"[deliver] {m}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _creds() -> dict:
    d = dict(os.environ)
    envf = ROOT / "config" / "env"
    if envf.exists():
        for ln in envf.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d.setdefault(k.strip(), v.strip())
    cfg = subsconfig.load()
    d["SUBS_FROM_EMAIL"] = cfg.get("from_email") or d.get("EMAIL_FROM", "")
    d["SUBS_REPLY_TO"] = cfg.get("reply_to") or cfg.get("support_email") or ""
    return d


# --------------------------------------------------------------------------- post source
def _norm_url(u: str) -> str:
    return (u or "").split("?")[0].rstrip("/").lower()


def post_key(p: dict) -> str:
    u = _norm_url(p.get("url", ""))
    if u:
        return u
    import hashlib
    return "t:" + hashlib.sha1((p.get("title", "") or p.get("post_text", "")[:80]).encode()).hexdigest()[:16]


def load_run_posts(path: Path) -> list[dict]:
    try:
        d = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if d.get("decision") != "post":
        return []
    posts = d.get("posts") or ([d["selected"]] if isinstance(d.get("selected"), dict) else [])
    out = []
    for p in posts:
        if not (p.get("post_text") or "").strip():
            continue
        out.append({"title": p.get("title", ""), "url": p.get("url", ""),
                    "source": p.get("source", ""), "score": int(p.get("score", 0) or 0),
                    "cve_ids": p.get("cve_ids", []) or [],
                    "post_text": p.get("post_text", ""), "short_text": p.get("short_text", ""),
                    "seen_at": now_iso()})
    return out


def append_posts_log(posts: list[dict]) -> None:
    have = set()
    if POSTS_LOG.exists():
        for ln in POSTS_LOG.read_text().splitlines():
            try:
                have.add(post_key(json.loads(ln)))
            except json.JSONDecodeError:
                pass
    with POSTS_LOG.open("a") as f:
        for p in posts:
            if post_key(p) not in have:
                f.write(json.dumps(p) + "\n")
    # trim to last ~500
    lines = POSTS_LOG.read_text().splitlines()
    if len(lines) > 500:
        POSTS_LOG.write_text("\n".join(lines[-500:]) + "\n")


def posts_last_hours(hours: float) -> list[dict]:
    if not POSTS_LOG.exists():
        return []
    cut = time.time() - hours * 3600
    out, seen = [], set()
    for ln in POSTS_LOG.read_text().splitlines():
        try:
            p = json.loads(ln)
        except json.JSONDecodeError:
            continue
        try:
            t = datetime.fromisoformat(p.get("seen_at", "").replace("Z", "+00:00")).timestamp()
        except ValueError:
            t = cut + 1
        k = post_key(p)
        if t >= cut and k not in seen:
            seen.add(k)
            out.append(p)
    return out


# --------------------------------------------------------------------------- matching
def vendor_match(post: dict, vendors: list[str]) -> bool:
    if not vendors:
        return True
    hay = (post.get("title", "") + " " + post.get("post_text", "") + " "
           + " ".join(post.get("cve_ids", []))).lower()
    return any(v.lower() in hay for v in vendors)


def wants(post: dict, sub: dict, tier_cfg: dict, mode: str) -> bool:
    prefs = sub.get("prefs", {})
    if mode == "instant" and not prefs.get("instant", True):
        return False
    if mode == "digest" and not prefs.get("digest", True):
        return False
    threshold = prefs.get("min_score_override")
    if threshold is None:
        threshold = tier_cfg.get("instant_min_score", 90) if mode == "instant" else 0
    if int(post.get("score", 0)) < int(threshold):
        return False
    return vendor_match(post, prefs.get("vendors", []))


# --------------------------------------------------------------------------- formatting
def fmt_instant(post: dict) -> tuple[str, str]:
    subject = (post.get("title") or post.get("post_text", "").splitlines()[0])[:180]
    return subject, post.get("post_text", "")


def fmt_digest(posts: list[dict], brand: str) -> tuple[str, str]:
    subject = f"{brand} — daily threat digest · {len(posts)} item(s)"
    lines = [f"{len(posts)} item(s) from the last 24 hours, highest impact first.", ""]
    for p in sorted(posts, key=lambda x: -int(x.get("score", 0))):
        hook = p.get("short_text") or (p.get("post_text", "").splitlines()[0])
        cves = ", ".join(p.get("cve_ids", []))
        lines.append(f"• [{p.get('score')}] {hook}")
        if cves:
            lines.append(f"  {cves}")
        if p.get("url"):
            lines.append(f"  {p['url']}")
        lines.append("")
    lines.append("— You can change vendors, channels or unsubscribe from your preference link.")
    return subject, "\n".join(lines)


# --------------------------------------------------------------------------- send
def deliver_to_sub(sub: dict, posts: list[dict], mode: str, cfg: dict, creds: dict,
                   dry: bool) -> dict:
    tier_cfg = cfg["tiers"].get(sub["tier"], cfg["tiers"]["individual"])
    verified = [c for c in sub.get("channels", []) if c.get("verified")]
    allowed_kinds = set(tier_cfg.get("channels", []))
    verified = [c for c in verified if c["kind"] in allowed_kinds]
    if not verified:
        return {"sub": sub["id"], "sent": 0, "skipped": "no verified channel"}

    matched = [p for p in posts if wants(p, sub, tier_cfg, mode)]
    if mode == "instant":
        matched = [p for p in matched if not S.already_delivered(sub["id"], post_key(p))]
    if not matched:
        return {"sub": sub["id"], "sent": 0}

    sent = 0
    for p in matched if mode == "instant" else [None]:
        if mode == "instant":
            subject, body = fmt_instant(p)
            keys = [post_key(p)]
            jsonp = {"posts": [p]} if tier_cfg.get("attach_json") else None
        else:
            subject, body = fmt_digest(matched, cfg["brand_name"])
            keys = [f"digest:{datetime.now(timezone.utc):%Y-%m-%d}"]
            jsonp = {"posts": matched} if tier_cfg.get("attach_json") else None

        for ch in verified:
            if dry:
                log(f"  DRY sub#{sub['id']} {ch['kind']} <- {subject[:60]}")
                sent += 1
                continue
            r = CH.send(ch["kind"], ch["target"], subject, body,
                        json_payload=jsonp if ch["kind"] in ("email", "webhook") else None,
                        creds=creds, priority=mode)
            S.record_delivery(sub["id"], keys[0], ch["kind"], mode, ok=r["ok"],
                              error=None if r["ok"] else r["error"])
            if r["ok"]:
                sent += 1
            else:
                log(f"  FAIL sub#{sub['id']} {ch['kind']}: {r['error']}")
        if mode == "digest":
            break
    return {"sub": sub["id"], "sent": sent, "matched": len(matched)}


# --------------------------------------------------------------------------- modes
def run(mode: str, source: Path, dry: bool) -> int:
    cfg = subsconfig.load()
    if not cfg.get("enabled"):
        log("subscriptions.enabled is false — nothing to do")
        return 0
    S.ensure_schema()
    creds = _creds()

    if mode == "instant":
        fresh = load_run_posts(source)
        if fresh:
            append_posts_log(fresh)
        posts = fresh
    else:
        posts = posts_last_hours(24)

    if not posts:
        log(f"{mode}: no posts to consider")
        return 0

    subs = S.active_for_delivery()
    subs = [s for s in subs if s["status"] == "active"]
    log(f"{mode}: {len(posts)} post(s) · {len(subs)} active subscriber(s)"
        + (" · DRY" if dry else ""))
    total = 0
    for s in subs:
        res = deliver_to_sub(s, posts, mode, cfg, creds, dry)
        total += res.get("sent", 0)
        if res.get("sent") or res.get("matched"):
            log(f"  sub#{s['id']} ({s['tier']}): sent {res.get('sent', 0)}"
                + (f", {res['skipped']}" if res.get("skipped") else ""))
    S.kv_set(f"last_{mode}_run", now_iso())
    log(f"{mode}: {total} message(s) sent")
    return 0


def send_test(subscriber_id: int, dry: bool) -> int:
    S.ensure_schema()
    sub = S.get(subscriber_id)
    if not sub:
        log(f"no subscriber #{subscriber_id}")
        return 1
    creds = _creds()
    demo = {"title": "Test alert — Blue Team CTI",
            "post_text": "This is a test alert.\n\nIf you're reading this, your channel is wired up "
                         "correctly and you'll get real alerts here.\n\n"
                         "Manage your preferences or unsubscribe from your account link.",
            "url": "", "cve_ids": [], "score": 100}
    subject, body = fmt_instant(demo)
    for ch in sub.get("channels", []):
        if dry:
            log(f"DRY -> {ch['kind']} {ch['target'][:40]}")
            continue
        r = CH.send(ch["kind"], ch["target"], subject, body, creds=creds, priority="instant")
        log(f"{ch['kind']}: {'ok' if r['ok'] else 'FAIL ' + r['error']}")
        S.record_delivery(subscriber_id, "test:" + now_iso(), ch["kind"], "test",
                          ok=r["ok"], error=None if r["ok"] else r["error"])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--instant", action="store_true")
    g.add_argument("--digest", action="store_true")
    g.add_argument("--test", type=int, metavar="SUB_ID")
    g.add_argument("--list", action="store_true")
    ap.add_argument("--from", dest="src", default=str(STATE / "decision.json"))
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    dry = a.dry or truthy(os.getenv("CTI_DRY_RUN"))

    if a.list:
        S.ensure_schema()
        for s in S.active_for_delivery():
            chs = ",".join(f"{c['kind']}{'✓' if c['verified'] else '?'}" for c in s["channels"])
            print(f"#{s['id']:>4}  {s['status']:<9} {s['tier']:<10} {s['email']:<32} [{chs}]")
        return 0
    if a.test is not None:
        return send_test(a.test, dry)
    return run("instant" if a.instant else "digest", Path(a.src), dry)


if __name__ == "__main__":
    sys.exit(main())
