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
    r"threat actors are increasingly",
    r"now more than ever",
    r"\bwe are seeing\b",
    r"\bstay safe\b",
    r"\bthoughts\?\s*$",
    r"(?:like|share|comment|repost)\b.{0,20}\bif you",
    r"#PatchNow\b", r"#StaySafe\b", r"#MustRead\b",
    r"\bgame[- ]chang", r"\bunprecedented\b",
]
ALLCAPS_OK = {
    "CVE", "CVSS", "RCE", "KEV", "SOC", "IOC", "IOCS", "MFA", "VPN", "EDR", "XDR",
    "NG", "MF", "US", "UK", "EU", "BOD", "CISA", "NIST", "NVD", "API", "APIS",
    "POC", "TTP", "TTPS", "C2", "AD", "DNS", "TLS", "SSL", "HTTP", "HTTPS", "SSRF",
    "XSS", "SQLI", "LPE", "PE", "OS", "IT", "OT", "ICS", "SIEM", "SSO", "IDP",
    "AWS", "GCP", "SaaS", "CI", "CD", "PR", "URL", "URLS", "ASAP", "FYI",
}
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿]"
)
URL = re.compile(r"https?://[^\s)]+")
HASHTAG = re.compile(r"#\w+")
CVE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)


def split_sentences(text: str) -> list[str]:
    # naive but good enough — split on . ! ? followed by space/newline
    parts = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    return [p.strip() for p in parts if p.strip()]


def lint(text: str, cve_ids: list[str] | None = None) -> dict:
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
    if len(hook) > 220:
        errors.append(f"hook (line 1) is {len(hook)} chars — it's all readers see before "
                      f"'…more'; keep it under 200")
    elif len(hook) > 200:
        warnings.append(f"hook is {len(hook)} chars — trim toward 150")
    if CVE.search(hook):
        warnings.append("hook leads with a CVE id — lead with the stake for the reader instead")

    # bullets
    bullets = [ln for ln in lines if ln.lstrip().startswith(("•", "-", "*"))]
    if len(bullets) < 3:
        warnings.append(f"{len(bullets)} bullet lines — the format expects 3 (Vulnerability/Affected/Do now)")
    for b in bullets:
        if len(b) > 200:
            errors.append(f"bullet is {len(b)} chars (max ~180) — one line, one idea: “{b[:60]}…”")
        elif len(b) > 180:
            warnings.append(f"bullet is {len(b)} chars — tighten: “{b[:60]}…”")
        if b.count(";") >= 2 or b.count(",") >= 4:
            warnings.append(f"bullet is a clause-chain — split it: “{b[:60]}…”")

    # labelled bullets present?
    joined = t.lower()
    for want in ("vulnerability", "affected", "do now"):
        if want not in joined:
            warnings.append(f"no “{want.title()}:” takeaway found")

    # sentence length
    for s in split_sentences(t):
        wc = len(s.split())
        if wc > 40:
            errors.append(f"sentence is {wc} words — split it: “{s[:70]}…”")
        elif wc > 25:
            warnings.append(f"sentence is {wc} words (aim ≤25): “{s[:70]}…”")

    # links
    urls = URL.findall(t)
    if not urls:
        errors.append("no source URL")
    elif len(urls) > 2:
        warnings.append(f"{len(urls)} links — one source link is enough")

    # hashtags
    tags = HASHTAG.findall(t)
    low = {x.lower() for x in tags}
    if len(tags) < 2:
        errors.append("fewer than 2 hashtags")
    elif len(tags) > 6:
        warnings.append(f"{len(tags)} hashtags — 3–4 is the sweet spot")
    if "#blueteam" not in low:
        warnings.append("missing #BlueTeam")
    if "#cybersecurity" not in low:
        warnings.append("missing #CyberSecurity")

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
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    text, cves = _extract(args)
    result = lint(text, cves)
    print(json.dumps(result, indent=2))
    return 1 if (args.strict and not result["ok"]) else 0


if __name__ == "__main__":
    sys.exit(main())
