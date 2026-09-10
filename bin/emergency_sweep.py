#!/usr/bin/env python3
"""Emergency sweep — catch an actively exploited critical between harvests.

The harvester runs 7x/day, so a KEV-listed RCE that breaks at 09:30 would sit
undetected until the 12:00 run. Getting a warning about something being
exploited right now in front of defenders is worth more than tidy scheduling,
so this runs every 20 minutes and wakes the full pipeline the moment a primary
source says something is on fire.

Cheap by design: it costs zero analyst tokens when nothing is burning. It only
fetches the CISA KEV catalogue and the PRIMARY feeds (vendor PSIRTs, CERTs,
research labs — not the news wires that rehash them hours later).

It writes ONLY state/emergency_seen.json, its own ledger, so it can never race
a harvest that is midway through rewriting state/candidates.json.

Exit codes: 0 nothing on fire (or a run was triggered), 1 on a hard failure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT))

import ingest  # noqa: E402  — reuse its fetchers and scoring, nothing else

STATE = ROOT / "state"
LEDGER = STATE / "emergency_seen.json"

# A story must be this fresh at its source to count as an emergency. Older than
# this and the normal harvest will pick it up soon enough.
FRESH_HOURS = 6
# A KEV entry added within this many days is "new".
KEV_FRESH_DAYS = 2
# Heuristic pre-score floor. Deliberately below the analyst's publish floor:
# this only decides whether to WAKE the analyst, which then judges properly.
MIN_PRESCORE = 55
# Don't trigger again within this many minutes of the last pipeline run.
COOLDOWN_MIN = 25

EXPLOITED = re.compile(
    r"exploited in the wild|activ(?:e|ely) exploit|under active attack"
    r"|in[- ]the[- ]wild|mass (?:scanning|exploitation)|exploitation observed"
    r"|zero[- ]day|emergency (?:patch|directive|advisory)"
    r"|added to (?:the )?(?:CISA )?KEV", re.I)


def log(m: str) -> None:
    print(f"[emergency] {m}")


def load_ledger() -> dict:
    try:
        d = json.loads(LEDGER.read_text())
        return {"cves": set(d.get("cves", [])), "urls": set(d.get("urls", [])),
                "triggers": d.get("triggers", [])}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"cves": set(), "urls": set(), "triggers": []}


def save_ledger(led: dict) -> None:
    try:
        LEDGER.write_text(json.dumps({
            "updated": datetime.now(timezone.utc).isoformat(),
            # keep the ledger bounded — old entries can't re-trigger anyway
            "cves": sorted(led["cves"])[-800:],
            "urls": sorted(led["urls"])[-800:],
            "triggers": led["triggers"][-40:],
        }, indent=2))
    except OSError as e:
        log(f"could not write the ledger ({e}) — a repeat trigger is possible")


def seen_urls() -> set[str]:
    """URLs the pipeline has already posted about."""
    out: set[str] = set()
    p = STATE / "seen.jsonl"
    if p.exists():
        for ln in p.read_text().splitlines():
            if ln.strip():
                try:
                    u = json.loads(ln).get("url")
                except json.JSONDecodeError:
                    continue
                if u:
                    out.add(ingest.normalise_url(u))
    return out


def minutes_since_last_run() -> float:
    """How long since the pipeline last finished, by run-log mtime."""
    logs = sorted((ROOT / "logs").glob("run-*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return 1e9
    age = datetime.now(timezone.utc).timestamp() - logs[0].stat().st_mtime
    return age / 60.0


def find_emergencies() -> list[dict]:
    settings = ingest.load_yaml(ROOT / "config" / "settings.yaml")
    ing = settings.get("ingest", {})
    timeout = int(ing.get("feed_timeout_seconds", 20))
    ingest.KEV_RECENT_DAYS = int(ing.get("kev_recent_days", 21))

    kev = ingest.fetch_kev(timeout)
    today = datetime.now(timezone.utc).date()
    fresh_kev: dict[str, dict] = {}
    for cid, meta in kev["meta"].items():
        try:
            added = datetime.strptime(meta.get("dateAdded", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - added).days <= KEV_FRESH_DAYS:
            fresh_kev[cid] = meta
    log(f"{len(fresh_kev)} KEV entr(ies) added in the last {KEV_FRESH_DAYS}d")

    feeds = [f for f in ingest.load_yaml(ROOT / "config" / "feeds.yaml").get("feeds", [])
             if f.get("enabled", True)
             and f.get("type", "rss") == "rss"
             and float(f.get("weight", 0)) >= ingest.PRIMARY_WEIGHT]
    log(f"polling {len(feeds)} primary source(s)")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=FRESH_HOURS)
    items = []
    for f in feeds:
        for it in ingest.fetch_feed(f["name"], f["url"], float(f.get("weight", 1)), timeout):
            pub = it.get("published")
            if pub is None or pub < cutoff:
                continue
            it["url"] = ingest.normalise_url(it.get("link", ""))
            items.append(it)

    already = seen_urls()
    led = load_ledger()
    hits = []
    for it in items:
        if not it["url"] or it["url"] in already or it["url"] in led["urls"]:
            continue
        sc = ingest.prescore(it, kev)
        text = f"{it.get('title','')} {it.get('summary','')}"
        kev_hit = [c for c in sc["cve_ids"] if c in fresh_kev]
        ransom = any(fresh_kev.get(c, {}).get("ransomware") for c in kev_hit)
        exploited = bool(EXPLOITED.search(text))
        if sc["pre_score"] < MIN_PRESCORE:
            continue
        if not (kev_hit or exploited):
            continue
        hits.append({
            "title": it.get("title", "")[:140], "url": it["url"],
            "source": it.get("source", ""), "pre_score": sc["pre_score"],
            "cves": sc["cve_ids"][:6], "fresh_kev": kev_hit,
            "ransomware": ransom, "exploited_language": exploited,
        })

    # A brand-new KEV entry flagged for ransomware use is an emergency on CISA's
    # authority alone, even if no feed has written it up yet.
    for cid, meta in fresh_kev.items():
        if cid in led["cves"] or not meta.get("ransomware"):
            continue
        if any(cid in h["cves"] for h in hits):
            continue
        hits.append({"title": f"CISA KEV: {meta.get('name', cid)}"[:140],
                     "url": "", "source": "CISA KEV", "pre_score": 100.0,
                     "cves": [cid], "fresh_kev": [cid], "ransomware": True,
                     "exploited_language": True})

    hits.sort(key=lambda h: h["pre_score"], reverse=True)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report what it found and exit without triggering a run")
    args = ap.parse_args()

    try:
        hits = find_emergencies()
    except Exception as e:  # noqa: BLE001 — a sweep failure must never be fatal
        log(f"sweep failed ({type(e).__name__}: {e}) — leaving the schedule alone")
        return 0

    if not hits:
        log("nothing on fire")
        return 0

    for h in hits[:5]:
        tags = ",".join(t for t, on in (("KEV", h["fresh_kev"]),
                                        ("ransomware", h["ransomware"]),
                                        ("exploited", h["exploited_language"])) if on)
        log(f"  [{h['pre_score']:.0f}] {h['title'][:80]}  ({tags})")

    if args.check:
        log(f"--check: {len(hits)} emergency signal(s), not triggering")
        return 0

    mins = minutes_since_last_run()
    if mins < COOLDOWN_MIN:
        log(f"a run finished {mins:.0f}m ago (< {COOLDOWN_MIN}m) — it already covers this")
        return 0

    led = load_ledger()
    for h in hits:
        if h["url"]:
            led["urls"].add(h["url"])
        led["cves"].update(h["fresh_kev"])
    led["triggers"].append({"at": datetime.now(timezone.utc).isoformat(),
                            "count": len(hits), "top": hits[0]["title"]})
    save_ledger(led)

    log(f"{len(hits)} emergency signal(s) -> waking the full pipeline")
    env = dict(os.environ, CTI_TRIGGER="emergency")
    r = subprocess.run(["bash", str(ROOT / "bin" / "run.sh")], env=env, cwd=str(ROOT))
    log(f"pipeline exited {r.returncode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
