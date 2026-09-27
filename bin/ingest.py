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
import os
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
EPSS_URL = "https://api.first.org/data/v1/epss"
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "mkt_tok", "fbclid", "gclid", "ref", "ref_src", "source",
    "amp", "__twitter_impression", "guccounter",
}

# keyword -> (points, why)  used only for the pre-score hint.
#
# Tuned for the PAID feed, not just the social post: a subscriber wants the
# breach, the new malware family, the published exploit and the LE takedown as
# well as the CVE-of-the-day. CVE/KEV items still score highest, but a
# substantive story with no CVE number should still clear the subscriber floor.
SIGNALS = [
    (re.compile(r"\bzero[- ]day\b|\b0day\b", re.I), 30, "zero-day"),
    (re.compile(r"actively exploited|exploited in the wild|under active attack|in-the-wild|mass exploitation|being exploited", re.I), 30, "active exploitation"),
    (re.compile(r"\bunauthenticated\b|pre-?auth|without authentication|auth(?:entication)? bypass", re.I), 12, "unauthenticated"),
    (re.compile(r"\bRCE\b|remote code execution|arbitrary code|command injection", re.I), 14, "RCE"),
    (re.compile(r"\bwormable\b|self-propagat", re.I), 12, "wormable"),
    (re.compile(r"supply[- ]chain|compromised package|malicious (?:npm|pypi|package)|typosquat|dependency confusion", re.I), 16, "supply chain"),
    (re.compile(r"\b(?:npm|PyPI|RubyGems|NuGet|crates\.io|Packagist|Go module|VS ?Code extension|browser extension|GitHub Action)\b", re.I), 10, "package ecosystem"),
    (re.compile(r"ransomware|extortion group|data-leak site|double extortion", re.I), 8, "ransomware"),
    (re.compile(r"\bPoC\b|proof[- ]of[- ]concept|exploit (?:code |script )?(?:released|public|published|available|now available|drops?)|working exploit|Metasploit module|Nuclei template", re.I), 12, "public exploit / PoC"),
    (re.compile(r"\b(?:data )?breach(?:ed|es)?\b|\bhacked\b|\bcompromise[ds]?\b|data (?:theft|leak(?:ed)?|exposed|stolen)|\bexfiltrat|unauthorized access|intrusion", re.I), 9, "breach / intrusion"),
    (re.compile(r"\b(?:botnet|trojan|infostealer|info-stealer|stealer|backdoor|loader|dropper|wiper|rootkit|keylogger|\bRAT\b|webshell|implant|spyware|cryptominer)\b", re.I), 8, "malware family"),
    (re.compile(r"\bcampaign\b|threat actor|\bAPT[- ]?\d+\b|\bUNC\d+\b|nation[- ]state|state[- ]sponsored|espionage|initial access broker|phishing kit|watering hole", re.I), 8, "named activity / campaign"),
    (re.compile(r"takedown|dismantl|seiz(?:ed|ure)|disrupt(?:ed|ion)|law enforcement|indict|charged with|arrest(?:ed)?|sentenced|sanction", re.I), 5, "law-enforcement action"),
    (re.compile(r"emergency (?:patch|update|directive)|out-of-band|patch now|patch (?:today|immediately)", re.I), 12, "emergency patch"),
    (re.compile(r"critical infrastructure|water utility|power grid|\bICS\b|\bOT\b|SCADA|healthcare|hospital|\bPLC\b|building automation", re.I), 10, "critical infrastructure / OT"),
    (re.compile(r"CVSS[^0-9]{0,6}(?:9\.\d|10(?:\.0)?)|\bcritical(?:ity)?\b.{0,12}(?:flaw|vuln|bug|rating)|rated critical", re.I), 14, "critical severity"),
    (re.compile(r"\b(Fortinet|FortiOS|FortiGate|Cisco|Ivanti|Citrix|NetScaler|Palo Alto|PAN-OS|GlobalProtect|VMware|ESXi|vCenter|Exchange|SharePoint|Outlook|Chrome|Firefox|Windows|Active Directory|Entra|Okta|SolarWinds|MOVEit|Confluence|Jira|Bitbucket|GitLab|GitHub|Kubernetes|SonicWall|WatchGuard|Zyxel|QNAP|Synology|Jenkins|Apache|Tomcat|nginx|OpenSSL|OpenSSH|PostgreSQL|MongoDB|Elastic|Docker|Zoom|Atlassian|Veeam|Zimbra|Roundcube|F5|BIG-IP|pfSense|OPNsense|OpenVPN|WireGuard|1Password|LastPass|Bitwarden|JetBrains|TeamCity|PaperCut|ScreenConnect|ConnectWise|AnyDesk|Cleo|CrushFTP|GoAnywhere|Rockwell|Siemens|Schneider Electric|Moxa|Advantech|Mitsubishi Electric|Honeywell)\b", re.I), 8, "widely deployed / high-value vendor"),
]
# Ransomware.live's RSS <category> is an ISO-3166 country code for the
# victim. Charlie's editorial call: US/UK/EU victims are the priority — that
# is the audience most subscribers can act on (report to their own board,
# check a shared supplier, brief a client), where a victim in an unrelated
# region rarely is.
PRIORITY_COUNTRIES = {
    "US", "GB", "UK",
    "DE", "FR", "IT", "ES", "NL", "BE", "SE", "NO", "DK", "FI", "IE", "PT",
    "AT", "CH", "PL", "CZ", "GR", "HU", "RO", "BG", "HR", "SK", "SI", "LT",
    "LV", "EE", "LU", "MT", "CY", "IS",
}

NEGATIVE = [
    (re.compile(r"\bwebinar\b|\bsponsored\b|\bwhitepaper\b|register (?:now|today|here)|save your seat|\be-?book\b|\[?virtual event\]?|\[?webcast\]?|on-demand (?:webinar|session)|join us (?:for|on)", re.I), -40, "marketing / event"),
    (re.compile(r"weekly (?:recap|roundup|review)|news roundup|in case you missed|week in (?:review|security)|this week in|monthly (?:recap|roundup)|newsletter", re.I), -25, "roundup / newsletter"),
    (re.compile(r"stormcast|\bpodcast\b|\bepisode\b\s*#?\d|daily briefing|morning (?:brief|coffee)", re.I), -25, "podcast / audio"),
    (re.compile(r"\bopinion\b|\bpredictions\b|thought leadership|\bhot take\b|\bmusings\b|lessons learned from|the future of|why .{0,40}\bmatters\b", re.I), -20, "opinion / thought-piece"),
    (re.compile(r"how to (?:secure|protect|build|choose|approach)|\bbeginners? guide\b|\bultimate guide\b|\btips (?:for|to)\b|explained:|\bwhat is\b.{0,40}\?|for consumers|protect yourself|your (?:family|kids|children)", re.I), -14, "explainer / consumer"),
    (re.compile(r"\bhiring\b|\bwe'?re hiring\b|join our team|award|recognized (?:as|in)|named a leader|magic quadrant|forrester wave|raises \$|series [A-E] funding|acquires|acquisition of", re.I), -30, "corporate / PR"),
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



CVEFEED_API = "https://cvefeed.io/api/vulnerability/"


def fetch_cvefeed(name: str, weight: float, min_cvss: float, timeout: int, api_key: str) -> list[dict]:
    """CVEFeed.io — authoritative CVE stream (NVD + CISA KEV + vendor advisories).
    Keeps items scoring >= min_cvss OR on CISA KEV; the lookback window is applied
    downstream like every other source."""
    if not api_key:
        print(f"  ! {name}: CVEFEED_API_KEY not set — skipping", file=sys.stderr)
        return []
    try:
        r = requests.get(CVEFEED_API, params={"ordering": "-published", "page_size": 200},
                         headers={**HEADERS, "Authorization": f"Bearer {api_key}",
                                  "Accept": "application/json"}, timeout=timeout)
        r.raise_for_status()
        results = r.json().get("results", [])
    except (requests.RequestException, ValueError) as e:
        print(f"  ! {name}: {e}", file=sys.stderr)
        return []
    out = []
    for v in results:
        if v.get("is_rejected"):
            continue
        cvss = v.get("cvss_score")
        kev = bool(v.get("cisa_kev_detail"))
        try:
            score_ok = kev or (cvss is not None and float(cvss) >= min_cvss)
        except (TypeError, ValueError):
            score_ok = kev
        if not score_ok:
            continue
        try:
            pub = datetime.fromisoformat(str(v["published"]).replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            continue
        sev = v.get("severity") or ""
        desc = (v.get("description") or "").strip()
        tag = f"[{sev}" + (f" · CVSS {cvss}" if cvss is not None else "") + (" · CISA KEV" if kev else "") + "] "
        out.append({
            "title": v.get("title") or v.get("id") or "CVE advisory",
            "link": v.get("url") or f"https://cvefeed.io/vuln/detail/{v.get('id', '')}",
            "summary": (tag + desc)[:1200],
            "published": pub,
            "source": name,
            "source_weight": weight,
        })
    print(f"  - {name}: {len(out)} items (CVSS >= {min_cvss} or KEV)")
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
            "vendor": v.get("vendorProject", ""),
            "product": v.get("product", ""),
            "desc": v.get("shortDescription", ""),
            "action": v.get("requiredAction", ""),
        }
        try:
            if datetime.strptime(v["dateAdded"], "%Y-%m-%d").date() >= cutoff:
                recent.add(cid)
        except (KeyError, ValueError):
            pass
    print(f"  - CISA KEV: {len(all_cves)} total, {len(recent)} added <= {KEV_RECENT_DAYS}d")
    return {"all": all_cves, "recent": recent, "meta": meta}


def kev_items(kev: dict, days: int) -> list[dict]:
    """New KEV listings as stories in their own right.

    A CVE CISA adds to KEV with no write-up in our feeds used to be invisible:
    KEV only boosted articles that already named it. That is how the Pixel
    modem zero-day (CVE-2026-58704, KEV 2026-09-16) never reached subscribers.
    When an article on the same CVE exists, clustering merges the two.
    """
    if days <= 0:
        return []
    today = datetime.now(timezone.utc).date()
    out = []
    for cid, m in kev["meta"].items():
        try:
            added = datetime.strptime(m.get("dateAdded", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - added).days > days:
            continue
        what = " ".join(x for x in (m.get("vendor"), m.get("product")) if x)
        tag = (f"[CISA KEV · added {m['dateAdded']} · federal patch deadline {m.get('dueDate') or 'n/a'}"
               + (" · known ransomware use" if m.get("ransomware") else "") + "] ")
        out.append({
            "title": f"CISA adds {cid} ({what}) to Known Exploited Vulnerabilities",
            "link": f"https://nvd.nist.gov/vuln/detail/{cid}",
            "summary": (tag + f"{m.get('name', '')}. {m.get('desc', '')} Required action: {m.get('action', '')}")[:1200],
            # KEV gives a date only; CISA usually publishes US daytime.
            "published": min(datetime.now(timezone.utc),
                             datetime.combine(added, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=18)),
            "source": "CISA KEV",
            "source_weight": 1.0,
        })
    print(f"  - CISA KEV as stories: {len(out)} added in the last {days}d")
    return out


# A feed at or above this weight is a PRIMARY source — a vendor PSIRT, a CERT,
# a research lab publishing its own finding (CISA, watchTowr, Huntress, NCSC,
# ZDI, Talos, Project Zero, DFIR Report). Below it are the wires that write up
# what those sources published. Catching a primary source before the wire picks
# it up is the whole first-mover edge: subscribers get lead time, and the public
# post lands while the story is still fresh instead of hours behind everyone.
PRIMARY_WEIGHT = 0.85


def fetch_epss(cve_ids: list[str], timeout: int) -> dict:
    """FIRST.org EPSS scores (probability of exploitation in the next 30 days).
    Free, no API key. Batched at 90 CVEs/request per their documented limits.

    Deliberately does NOT send the shared browser-spoofing HEADERS (UA + Accept +
    Accept-Language together) used for RSS feeds: that exact combination reliably
    triggers a 400 "errorNotSupported" from FIRST.org, while no headers, or any
    one of those three alone, works every time — looks like a bot-heuristic on
    their side flagging the full browser-header signature from a non-browser TLS
    stack. A bare, honest UA avoids it."""
    out: dict[str, float] = {}
    ids = sorted(set(cve_ids))
    for i in range(0, len(ids), 90):
        chunk = ids[i:i + 90]
        try:
            r = requests.get(
                EPSS_URL, params={"cve": ",".join(chunk)},
                headers={"User-Agent": "SecurityCyber-CTI/1.0"}, timeout=timeout
            )
            r.raise_for_status()
            for row in r.json().get("data", []):
                cid = row.get("cve", "").upper()
                try:
                    out[cid] = float(row["epss"])
                except (KeyError, ValueError):
                    pass
        except (requests.RequestException, ValueError) as e:
            print(f"  ! EPSS: {e}", file=sys.stderr)
    if ids:
        print(f"  - EPSS: {len(out)}/{len(ids)} CVEs scored")
    return out


def prescore(item: dict, kev: dict) -> dict:
    text = f"{item['title']} \n {item['summary']}"
    # Base lifted from 8 -> 11: a substantive story from a trusted feed with no
    # CVE and no keyword hit used to land ~5-7, below the subscriber floor, so
    # breaches / campaigns / exploits-published silently never reached the paid
    # feed. The NEGATIVE rules pull genuine noise back down.
    score = 11.0 * item["source_weight"]
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

    # First-mover bonus. Age is measured against the source's own publish time,
    # so this rewards reaching a story early rather than reaching it at all.
    age_h = None
    primary = float(item.get("source_weight", 0)) >= PRIMARY_WEIGHT
    pub = item.get("published")
    if pub is not None:
        age_h = max(0.0, (datetime.now(timezone.utc) - pub).total_seconds() / 3600.0)
        if primary and age_h <= 2:
            score += 10
            reasons.append("+10 primary source, broke <2h ago")
        elif primary and age_h <= 6:
            score += 5
            reasons.append("+5 primary source, broke <6h ago")
        elif age_h <= 3:
            score += 3
            reasons.append("+3 published <3h ago")

    category = str(item.get("category", "")).strip().upper()
    if category in PRIORITY_COUNTRIES:
        score += 12
        reasons.append(f"+12 priority region victim ({category})")

    score = max(0.0, min(100.0, score))
    return {
        "cve_ids": cves,
        "kev_cves": kev_hits,
        "kev_recent_cves": kev_recent,
        "pre_score": round(score, 1),
        "pre_score_reasons": reasons,
        "age_hours": None if age_h is None else round(age_h, 1),
        "primary_source": primary,
    }


def main() -> int:
    global KEV_RECENT_DAYS
    settings = load_yaml(ROOT / "config" / "settings.yaml")
    ing = settings.get("ingest", {})
    KEV_RECENT_DAYS = int(ing.get("kev_recent_days", 21))
    lookback = int(ing.get("lookback_hours", 12))
    timeout = int(ing.get("feed_timeout_seconds", 20))
    max_candidates = int(ing.get("max_candidates", 40))
    all_feeds = load_yaml(ROOT / "config" / "feeds.yaml").get("feeds", [])
    feeds = [f for f in all_feeds if f.get("enabled", True)]
    rss_feeds = [f for f in feeds if f.get("type", "rss") == "rss"]
    api_feeds = [f for f in feeds if f.get("type") == "api"]

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)
    print(f"[ingest] {len(feeds)}/{len(all_feeds)} feeds enabled, lookback {lookback}h "
          f"(since {cutoff:%Y-%m-%d %H:%M UTC})")

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
        # only *real* posts count as "already covered" — dry-run sims must not
        # make the analyst think a story was published
        rows = [r for r in rows if not r.get("dry_run")]
        recently_posted = [
            {"title": r.get("title", ""), "url": r.get("url", ""), "posted_at": r.get("posted_at", "")}
            for r in rows[-25:]
        ]

    raw: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [
            ex.submit(fetch_feed, f["name"], f["url"], float(f.get("weight", 0.6)), timeout)
            for f in rss_feeds
        ]
        for f in api_feeds:
            if f.get("provider") == "cvefeed":
                futs.append(ex.submit(
                    fetch_cvefeed, f["name"], float(f.get("weight", 0.9)),
                    float(f.get("min_cvss", 7.5)), timeout, os.environ.get("CVEFEED_API_KEY", "")))
        for fut in as_completed(futs):
            raw.extend(fut.result())
    kev = fetch_kev(timeout)
    raw.extend(kev_items(kev, int(ing.get("kev_story_days", 2))))

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

    all_cves = sorted({c for cl in clusters for c in cl["cves"]})
    epss = fetch_epss(all_cves, timeout)

    candidates = []
    for cl in clusters:
        members = sorted(
            cl["members"],
            key=lambda m: (m["pre_score"], m["source_weight"], m["published_iso"]),
            reverse=True,
        )
        best = members[0]
        epss_meta = {c: epss[c] for c in best["cve_ids"] if c in epss}
        pre_score = best["pre_score"]
        reasons = list(best["pre_score_reasons"])
        top_epss = max(epss_meta.values(), default=0.0)
        if top_epss >= 0.5:
            pre_score = min(100.0, pre_score + 15)
            reasons.append(f"+15 EPSS {top_epss:.2f} (high near-term exploitation probability)")
        elif top_epss >= 0.1:
            pre_score = min(100.0, pre_score + 7)
            reasons.append(f"+7 EPSS {top_epss:.2f}")
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
            "epss_meta": epss_meta,
            "pre_score": round(pre_score, 1),
            "pre_score_reasons": reasons,
            # Timing signals the analyst uses to pick the social lane: a fresh
            # primary-source story nobody else has written up yet is the one
            # worth posting now. `corroborations` counts the other feeds that
            # already carried it — 0 means we are ahead of the wire, several
            # means it is already mainstream and the post adds nothing.
            "age_hours": best.get("age_hours"),
            "primary_source": bool(best.get("primary_source")),
            "corroborations": len(members) - 1,
            "breaking": bool(best.get("primary_source")
                             and (best.get("age_hours") if best.get("age_hours") is not None else 99) <= 3
                             and len(members) - 1 <= 1),
            "also_reported_by": [
                {"source": m["source"], "url": m["url"]} for m in members[1:]
            ],
        })

    candidates.sort(key=lambda c: c["pre_score"], reverse=True)
    candidates = candidates[:max_candidates]

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": lookback,
        "min_score": int(settings.get("scoring", {}).get("min_score", 65)),
        # Floor for the paid subscriber feed — deliberately far below the social
        # posting threshold so subscribers get the FULL picture, not a highlights
        # reel. This is the "why pay" line: the free feed is 1-2 posts/day, the
        # paid feed is everything real with an analyst write-up. Tunable via
        # scoring.subscriber_min_score in settings.yaml.
        "subscriber_min_score": int(settings.get("scoring", {}).get("subscriber_min_score", 10)),
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
