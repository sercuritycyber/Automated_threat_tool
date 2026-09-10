#!/usr/bin/env python3
"""Style/quality lint for a drafted LinkedIn post.

Usage:
    python3 bin/lint_post.py --file state/pending.json      # reads .decision.selected.post_text
    python3 bin/lint_post.py --text "..."
    echo "..." | python3 bin/lint_post.py

Prints JSON: {"ok": bool, "errors": [...], "warnings": [...], "stats": {...}}
`errors` are things that should block an automatic post; `warnings` are advisory.
Exit code: 0 unless --strict and there are errors (then 1).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BANNED = [
    r"in today'?s (?:threat )?landscape",
    r"\b(?:threat actors|attackers|adversaries|criminals|hackers) are increasingly",
    r"now more than ever",
    r"\bwe are seeing\b",
    r"\bstay safe\b",
    r"\bthoughts\?\s*$",
    r"\bagree\?", r"\bam i wrong\b", r"\bdrop a comment\b",
    r"\bwho else\b.{0,15}\?", r"\bsound off\b",
    r"(?:like|share|comment|repost)\b.{0,20}\bif you",
    r"#PatchNow\b", r"#StaySafe\b", r"#MustRead\b",
    r"\bgame[- ]chang", r"\bunprecedented\b",
    # sales clichés — security practitioners tune out hard-sell
    r"\bless than (?:the price of )?a (?:cup of )?coffee\b",
    r"\bmorning coffee\b",
    r"\benterprise[- ]grade\b",
    r"\bpeace of mind\b",
    r"\bdelve\b", r"\bunlock\b(?! the)", r"\bseamless(?:ly)?\b",
    r"\bcutting[- ]edge\b", r"\bworld[- ]class\b", r"\bbest[- ]in[- ]class\b",
    r"\brest easy\b", r"\bsleep (?:easy|soundly)\b",
]
ALLCAPS_OK = {
    "CVE", "CVSS", "RCE", "KEV", "SOC", "IOC", "IOCS", "MFA", "VPN", "EDR", "XDR",
    "NG", "MF", "US", "UK", "EU", "BOD", "CISA", "NIST", "NVD", "API", "APIS",
    "POC", "TTP", "TTPS", "C2", "AD", "DNS", "TLS", "SSL", "HTTP", "HTTPS", "SSRF",
    "XSS", "SQLI", "LPE", "PE", "OS", "IT", "OT", "ICS", "SIEM", "SSO", "IDP",
    "AWS", "GCP", "SaaS", "CI", "CD", "PR", "URL", "URLS", "ASAP", "FYI",
    "FMC", "ASA", "FTD", "EPMM", "ITSM", "NAS", "VPNS", "SSH", "RDP", "SMB",
    "PHP", "IIS", "WAF", "MDM", "PSIRT", "CERT", "KQL", "NDR", "MSP", "MSPS",
}
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿]"
)
URL = re.compile(r"https?://[^\s)]+")
HASHTAG = re.compile(r"#\w+")
CVE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)


def split_sentences(text: str) -> list[str]:
    """Prose sentences only, split line by line.

    This used to flatten newlines into spaces before splitting on `.!?`. Bullet
    lines rarely end in a full stop, so the whole breakdown block collapsed into
    a single "sentence" of 60-120 words and tripped the >40-word ERROR — which
    held otherwise-good posts for manual review instead of publishing them.
    Bullets and hashtag lines are excluded here; bullets have their own length
    rules below.
    """
    out: list[str] = []
    for line in text.splitlines():
        ln = line.strip()
        if not ln or ln.startswith(("\u2022", "-", "*", "#")):
            continue
        for part in re.split(r"(?<=[.!?])\s+", ln):
            part = part.strip()
            if part:
                out.append(part)
    return out


LANES = ("breaking", "intel", "trend")

# Signals that a post carries a conversion line at all — the "gap" line
# (what the subscriber brief has that this post does not) or the plug.
# A post with neither gives everything away and asks for nothing.
CONVERSION = re.compile(
    r"subscriber|paid feed|private feed|briefs? like this|in the comments"
    r"|cti\.securitycyber|\{alerts_today\}", re.I)


def lint(text: str, cve_ids: list[str] | None = None, lane: str = "intel") -> dict:
    """`lane` is the post's social lane (see prompts/analyst.md step 4).
    The breakdown lanes and the trend lane have different shapes, so the
    structural checks differ; everything else applies to all posts."""
    lane = (lane or "intel").strip().lower()
    if lane not in LANES:
        lane = "intel"
    breakdown = lane in ("breaking", "intel")
    errors: list[str] = []
    warnings: list[str] = []
    t = text.strip()
    lines = t.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]

    n = len(t)
    if n < 300:
        errors.append(f"too short ({n} chars) — needs real substance")
    if n > 2900:
        errors.append(f"over LinkedIn's 3000-char limit ({n})")
    elif n > 1800:
        warnings.append(f"long ({n} chars) — aim for 600–1600, shorter posts perform better")

    # hook = first non-empty line
    hook = non_empty[0] if non_empty else ""
    if len(hook) > 200:
        errors.append(f"hook (line 1) is {len(hook)} chars — it's all readers see "
                      f"before '…more'; hard cap is 200, aim under 140")
    elif len(hook) > 140:
        warnings.append(f"hook is {len(hook)} chars — mobile truncates around 140")
    # Only ask for a CVE in the hook when the story actually has one; campaign,
    # breach and trend posts legitimately have none.
    if breakdown and (cve_ids or []) and not CVE.search(hook):
        warnings.append("hook has no CVE id — name the product and the CVE in line 1")

    # bullets
    bullets = [ln for ln in lines if ln.lstrip().startswith(("•", "-", "*"))]
    want_bullets = 4 if breakdown else 2
    if len(bullets) < want_bullets:
        shape = ("the breakdown expects 4 (What/Impact/Affected/Do this)" if breakdown
                 else "the trend lane expects 2-3 action bullets")
        warnings.append(f"{len(bullets)} bullet lines — {shape}")
    for b in bullets:
        if len(b) > 200:
            errors.append(f"bullet is {len(b)} chars (max ~180) — one line, one idea: “{b[:60]}…”")
        elif len(b) > 180:
            warnings.append(f"bullet is {len(b)} chars — tighten: “{b[:60]}…”")
        if b.count(";") >= 2 or b.count(",") >= 4:
            warnings.append(f"bullet is a clause-chain — split it: “{b[:60]}…”")

    # labelled blocks present? — the two lane shapes differ
    joined = t.lower()
    if breakdown:
        for want in ("impact", "affected"):
            if want not in joined:
                warnings.append(f"no “{want.title()}:” line in the breakdown")
        if not any(k in joined for k in ("do this", "action")):
            warnings.append("no “Do this:” line in the breakdown")
        if (cve_ids or []) and "cvss" not in joined:
            warnings.append("no CVSS on a post that names a CVE")
        if "what to look for" not in joined and "what to hunt" not in joined:
            warnings.append("no “What to look for:” hunt hint")
    else:
        if "why it matters" not in joined:
            warnings.append("trend post has no “Why it matters:” block")
        if "what to actually do" not in joined and "what to do" not in joined:
            warnings.append("trend post has no “What to actually do:” block")

    # sentence length
    for s in split_sentences(t):
        wc = len(s.split())
        if wc > 40:
            errors.append(f"sentence is {wc} words — split it: “{s[:70]}…”")
        elif wc > 25:
            warnings.append(f"sentence is {wc} words (aim ≤25): “{s[:70]}…”")

    # links — a raw URL in the body throttles LinkedIn reach; it belongs in the
    # first comment (the `comment` field), which this linter doesn't see.
    urls = URL.findall(t)
    if urls:
        errors.append(f"URL in the post body ({urls[0][:40]}…) — links go in the first comment only")
    # The post must ask for something. The gap line + plug are the whole
    # conversion mechanic; without one the post is a free giveaway.
    if not CONVERSION.search(t):
        warnings.append("no conversion line — the post gives the analysis away and "
                        "points nowhere; add the gap line and the plug")

    # hashtags
    tags = HASHTAG.findall(t)
    low = {x.lower() for x in tags}
    # Three complete tags reach further than five; more reads as tag-stuffing
    # and is down-ranked. publishers.py trims a 4th+ deterministically.
    if len(tags) != 3:
        warnings.append(f"{len(tags)} hashtags — exactly 3 (#CyberSecurity, "
                        f"#ThreatIntel, +1 topical)")
    # truncated / malformed tag (the "#VulnerabilityManage" problem)
    for tag in tags:
        if tag.lower() in {"#vulnerabilitymanage", "#vulnmanage", "#threatint", "#cybersec"}:
            errors.append(f"truncated hashtag “{tag}” — use the full tag or drop it")
    if "#cybersecurity" not in low:
        warnings.append("missing #CyberSecurity")
    if "#threatintel" not in low:
        warnings.append("missing #ThreatIntel")

    # banned phrases
    for pat in BANNED:
        m = re.search(pat, t, re.I | re.M)
        if m:
            errors.append(f"banned phrasing: “{m.group(0).strip()}”")

    # emoji
    em = EMOJI.findall(t)
    # arrows/√ etc caught by range; only flag true pictographs loosely
    picto = [c for c in em if ord(c) >= 0x1F000 or 0x2600 <= ord(c) <= 0x27BF]
    if len(picto) > 1:
        warnings.append(f"{len(picto)} emoji — at most one, at the very start")

    # em-dashes
    if t.count("—") > 4:
        warnings.append(f"{t.count('—')} em-dashes — leaning on them; use full stops")

    # shouty all-caps words
    for w in re.findall(r"\b[A-Z]{3,}\b", t):
        if w not in ALLCAPS_OK and not CVE.search(w):
            warnings.append(f"all-caps “{w}” reads as shouting")
            break

    # CVE consistency
    body_cves = {c.upper() for c in CVE.findall(t)}
    for c in (cve_ids or []):
        if c and c.upper() not in body_cves:
            warnings.append(f"{c} is in cve_ids but not mentioned in the post")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "chars": n, "lines": len(non_empty), "hook_chars": len(hook),
            "bullets": len(bullets), "hashtags": len(tags), "links": len(urls),
            "lane": lane, "conversion_line": bool(CONVERSION.search(t)),
        },
    }


def _extract(args) -> tuple[str, list[str]]:
    if args.text is not None:
        return args.text, []
    if args.file:
        p = Path(args.file)
        raw = p.read_text()
        if p.suffix == ".json":
            d = json.loads(raw)
            node = d.get("decision") if isinstance(d.get("decision"), dict) else d
            sel = (node.get("selected") if isinstance(node, dict) else None) or {}
            return sel.get("post_text", "") or "", sel.get("cve_ids", []) or []
        return raw, []
    return sys.stdin.read(), []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--text")
    ap.add_argument("--lane", default="intel", choices=list(LANES))
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    text, cves = _extract(args)
    result = lint(text, cves, args.lane)
    print(json.dumps(result, indent=2))
    return 1 if (args.strict and not result["ok"]) else 0


if __name__ == "__main__":
    sys.exit(main())
