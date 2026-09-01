#!/usr/bin/env python3
"""CTI ingest stage — deterministic, no LLM.

  1. pull every feed in config/feeds.yaml (concurrently)
  2. keep items published within settings.ingest.lookback_hours
  3. normalise URLs, extract CVE IDs
  4. drop anything already in state/seen.jsonl (URL previously posted)
  5. cross-reference the CISA KEV catalog
  6. cluster near-duplicate stories, keep the strongest source per cluster
  7. attach a transparent heuristic pre-score (a HINT for the analyst)
  8. write state/candidates.json

Exit codes: 0 always (an empty candidate set is a valid outcome).
"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import feedparse  # noqa: E402

STATE = ROOT / "state"
STATE.mkdir(exist_ok=True)
# A real browser UA — several security sites (Talos, SecurityWeek, MS) 403 bots.
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-GB,en;q=0.9",
}
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "mkt_tok", "fbclid", "gclid", "ref", "ref_src", "source",
    "amp", "__twitter_impression", "guccounter",
}

# keyword -> (points, why)  used only for the pre-score hint
SIGNALS = [
    (re.compile(r"\bzero[- ]day\b|\b0day\b", re.I), 30, "zero-day"),
    (re.compile(r"actively exploited|exploited in the wild|under active attack|in-the-wild", re.I), 30, "active exploitation"),
    (re.compile(r"\bunauthenticated\b|pre-?auth|without authentication", re.I), 12, "unauthenticated"),
    (re.compile(r"\bRCE\b|remote code execution|arbitrary code", re.I), 14, "RCE"),
    (re.compile(r"\bwormable\b|self-propagat", re.I), 12, "wormable"),
    (re.compile(r"supply[- ]chain|compromised package|malicious (?:npm|pypi|package)", re.I), 16, "supply chain"),
    (re.compile(r"ransomware|extortion group|data-leak site", re.I), 8, "ransomware"),
    (re.compile(r"\bPoC\b|proof[- ]of[- ]concept|exploit code (?:released|public)", re.I), 10, "public PoC"),
    (re.compile(r"emergency (?:patch|update|directive)|out-of-band", re.I), 12, "emergency patch"),
    (re.compile(r"critical infrastructure|water utility|power grid|healthcare|hospital", re.I), 10, "critical infrastructure"),
    (re.compile(r"CVSS[^0-9]{0,6}(?:9\.\d|10(?:\.0)?)", re.I), 14, "CVSS 9.0+"),
    (re.compile(r"\b(Fortinet|FortiOS|Cisco|Ivanti|Citrix|Palo Alto|VMware|Exchange|SharePoint|Outlook|Chrome|Windows|Active Directory|Okta|SolarWinds|MOVEit|Confluence|Jira|GitLab|Kubernetes)\b", re.I), 8, "widely deployed vendor"),
]
NEGATIVE = [
    (re.compile(r"\bwebinar\b|\bsponsored\b|\bwhitepaper\b|register now|\be-?book\b", re.I), -40, "marketing"),
    (re.compile(r"weekly (?:recap|roundup)|news roundup|in case you missed", re.I), -25, "roundup"),
    (re.compile(r"\bopinion\b|\bpredictions\b|\bthought leadership\b", re.I), -20, "opinion"),
]

STOP = set("the a an and or of to in for on with is are be by from at as into via new update patch flaw bug vulnerability vulnerabilities attack attacks hackers threat security cyber".split())


def load_yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text()) or {}


def normalise_url(url: str) -> str:
    try:
        s = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (s.hostname or "").lower().removeprefix("www.")
    q = "&".join(
        kv for kv in s.query.split("&")
        if kv and kv.split("=", 1)[0].lower() not in TRACKING_PARAMS
    )
    path = s.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, q, ""))


def title_fp(title: str) -> frozenset:
    toks = re.findall(r"[a-z0-9]+", title.lower())
    return frozenset(t for t in toks if len(t) > 2 and t not in STOP)


def fetch_feed(name: str, url: str, weight: float, timeout: int) -> list[dict]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! {name}: {e}", file=sys.stderr)
        return []
    out = []
    for e in feedparse.parse(r.content, source=name):
        e["source_weight"] = weight
        out.append(e)
    print(f"  - {name}: {len(out)} items")
    return out


def fetch_kev(timeout: int) -> dict:
    try:
        r = requests.get(KEV_URL, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  ! KEV: {e}", file=sys.stderr)
        return {"all": set(), "recent": set(), "meta": {}}
    all_cves, recent, meta = set(), set(), {}
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=KEV_RECENT_DAYS)
    for v in data.get("vulnerabilities", []):
        cid = v.get("cveID", "").upper()
        if not cid:
            continue
        all_cves.add(cid)
        meta[cid] = {
            "name": v.get("vulnerabilityName", ""),
            "dateAdded": v.get("dateAdded", ""),
            "ransomware": v.get("knownRansomwareCampaignUse", "") == "Known",
            "dueDate": v.get("dueDate", ""),
        }
        try:
            if datetime.strptime(v["dateAdded"], "%Y-%m-%d").date() >= cutoff:
                recent.add(cid)
        except (KeyError, ValueError):
            pass
    print(f"  - CISA KEV: {len(all_cves)} total, {len(recent)} added <= {KEV_RECENT_DAYS}d")
    return {"all": all_cves, "recent": recent, "meta": meta}


def prescore(item: dict, kev: dict) -> dict:
    text = f"{item['title']} \n {item['summary']}"
    score = 8.0 * item["source_weight"]
    reasons = [f"source weight {item['source_weight']:.2f}"]
    for rx, pts, why in SIGNALS:
        if rx.search(text):
            score += pts
            reasons.append(f"+{pts} {why}")
    for rx, pts, why in NEGATIVE:
        if rx.search(text):
            score += pts
            reasons.append(f"{pts} {why}")
    cves = sorted({c.upper() for c in CVE_RE.findall(text)})
    kev_hits = [c for c in cves if c in kev["all"]]
    kev_recent = [c for c in cves if c in kev["recent"]]
    if kev_recent:
        score += 25
        reasons.append(f"+25 on CISA KEV (recent): {', '.join(kev_recent)}")
    elif kev_hits:
        score += 12
        reasons.append(f"+12 on CISA KEV: {', '.join(kev_hits)}")
    if any(kev["meta"].get(c, {}).get("ransomware") for c in kev_hits):
        score += 8
        reasons.append("+8 KEV flagged for ransomware use")
    score = max(0.0, min(100.0, score))
    return {
        "cve_ids": cves,
        "kev_cves": kev_hits,
        "kev_recent_cves": kev_recent,
        "pre_score": round(score, 1),
        "pre_score_reasons": reasons,
    }


def main() -> int:
    global KEV_RECENT_DAYS
    settings = load_yaml(ROOT / "config" / "settings.yaml")
    ing = settings.get("ingest", {})
    KEV_RECENT_DAYS = int(ing.get("kev_recent_days", 21))
    lookback = int(ing.get("lookback_hours", 12))
    timeout = int(ing.get("feed_timeout_seconds", 20))
    max_candidates = int(ing.get("max_candidates", 40))
    feeds = load_yaml(ROOT / "config" / "feeds.yaml").get("feeds", [])

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)
    print(f"[ingest] {len(feeds)} feeds, lookback {lookback}h (since {cutoff:%Y-%m-%d %H:%M UTC})")

    seen_urls = set()
    seen_path = STATE / "seen.jsonl"
    if seen_path.exists():
        for line in seen_path.read_text().splitlines():
            try:
                seen_urls.add(json.loads(line)["url"])
            except (json.JSONDecodeError, KeyError):
                pass

    recently_posted = []
    hist_path = STATE / "history.jsonl"
    if hist_path.exists():
        rows = [json.loads(x) for x in hist_path.read_text().splitlines() if x.strip()]
        recently_posted = [
            {"title": r.get("title", ""), "url": r.get("url", ""), "posted_at": r.get("posted_at", "")}
            for r in rows[-25:]
        ]

    raw: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [
            ex.submit(fetch_feed, f["name"], f["url"], float(f.get("weight", 0.6)), timeout)
            for f in feeds
        ]
        for fut in as_completed(futs):
            raw.extend(fut.result())
    kev = fetch_kev(timeout)

    # window + URL dedup
    fresh: list[dict] = []
    for it in raw:
        pub = it["published"]
        if pub is None or pub < cutoff:
            continue
        it["url"] = normalise_url(it["link"])
        if it["url"] in seen_urls:
            continue
        it["published_iso"] = pub.astimezone(timezone.utc).isoformat()
        fresh.append(it)

    # cluster near-duplicates
    clusters: list[dict] = []
    for it in sorted(fresh, key=lambda x: x["published_iso"], reverse=True):
        sc = prescore(it, kev)
        it.update(sc)
        fp = title_fp(it["title"])
        cve_set = set(it["cve_ids"])
        placed = False
        for cl in clusters:
            shared_cve = bool(cve_set & cl["cves"]) and cve_set
            overlap = len(fp & cl["fp"]) / max(1, min(len(fp), len(cl["fp"])))
            if shared_cve or overlap >= 0.6:
                cl["members"].append(it)
                cl["fp"] |= fp
                cl["cves"] |= cve_set
                placed = True
                break
        if not placed:
            clusters.append({"members": [it], "fp": set(fp), "cves": set(cve_set)})

    candidates = []
    for cl in clusters:
        members = sorted(
            cl["members"],
            key=lambda m: (m["pre_score"], m["source_weight"], m["published_iso"]),
            reverse=True,
        )
        best = members[0]
        candidates.append({
            "title": best["title"],
            "url": best["url"],
            "source": best["source"],
            "published": best["published_iso"],
            "summary": best["summary"],
            "cve_ids": best["cve_ids"],
            "kev_cves": best["kev_cves"],
            "kev_recent_cves": best["kev_recent_cves"],
            "kev_meta": {c: kev["meta"][c] for c in best["kev_cves"] if c in kev["meta"]},
            "pre_score": best["pre_score"],
            "pre_score_reasons": best["pre_score_reasons"],
            "also_reported_by": [
                {"source": m["source"], "url": m["url"]} for m in members[1:]
            ],
        })

    candidates.sort(key=lambda c: c["pre_score"], reverse=True)
    candidates = candidates[:max_candidates]

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": lookback,
        "min_score": int(settings.get("scoring", {}).get("min_score", 80)),
        "brand": settings.get("brand", {}),
        "kev_recent_days": KEV_RECENT_DAYS,
        "counts": {
            "raw_items": len(raw),
            "in_window_new": len(fresh),
            "clusters": len(clusters),
            "candidates": len(candidates),
        },
        "recently_posted": recently_posted,
        "candidates": candidates,
    }
    (STATE / "candidates.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"[ingest] wrote {len(candidates)} candidates -> state/candidates.json")
    if candidates:
        top = candidates[0]
        print(f"[ingest] top: {top['pre_score']:.0f}  {top['title'][:90]}")
    return 0


if __name__ == "__main__":
    t0 = time.time()
    rc = main()
    print(f"[ingest] done in {time.time() - t0:.1f}s")
    sys.exit(rc)
