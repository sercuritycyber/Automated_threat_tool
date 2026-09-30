#!/usr/bin/env python3
"""Extract and tag v3 .onion addresses from index/search pages — stdlib only.

Discovery works on *aggregator* pages (onion search engines, verified-mirror
lists), not on the forums themselves: we harvest the addresses those sources
publish, then dedupe/track/liveness-check them. This module is the pure logic
(no network, no DB) so it is trivially testable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# v3 onion services only (v2 was retired in 2021): 56 chars of base32 + .onion.
ONION_RE = re.compile(r"\b([a-z2-7]{56}\.onion)\b", re.IGNORECASE)

# A little context either side of the address, to capture the label a source
# gives it (e.g. "BreachForums official mirror").
CONTEXT_CHARS = 90


@dataclass
class Sighting:
    address: str
    source: str          # which source page produced it
    label: str           # nearby text / anchor, best-effort
    context: str         # wider surrounding text
    tags: list[str] = field(default_factory=list)  # matched target names


def extract(text: str, source: str, targets: list[str] | None = None) -> list[Sighting]:
    """Find every v3 onion in ``text`` with its surrounding context.

    ``targets`` are forum names of interest (breachforums/xss/dread/…); any that
    appear in an address's context are attached as tags.
    """
    targets = [t.lower() for t in (targets or [])]
    out: list[Sighting] = []
    # Some index sites wrap onions in percent-encoded redirect links
    # (…redirect_url=http%3A%2F%2F<addr>…). Decoding first catches those too.
    from urllib.parse import unquote
    decoded = unquote(text)
    text = text if decoded == text else text + "\n" + decoded
    seen_here: set[str] = set()
    for m in ONION_RE.finditer(text):
        addr = m.group(1).lower()
        start = max(0, m.start() - CONTEXT_CHARS)
        end = min(len(text), m.end() + CONTEXT_CHARS)
        context = " ".join(text[start:end].split())
        ctx_lower = context.lower()
        tags = sorted({t for t in targets if t in ctx_lower})
        # De-dupe repeats of the same address on one page, but keep the richest
        # (tagged) sighting if we see the address both tagged and untagged.
        key = addr
        if key in seen_here and not tags:
            continue
        seen_here.add(key)
        label = context[:120]
        out.append(Sighting(addr, source, label, context, tags))
    return out


def expand_source_urls(source: dict, targets: list[str]) -> list[tuple[str, str]]:
    """Turn one source config into concrete (name, url) pairs.

    A url containing ``{target}`` is expanded once per target (e.g. an Ahmia
    search); otherwise it is used verbatim.
    """
    name = source.get("name") or source.get("url", "?")
    url = source.get("url", "")
    if not url:
        return []
    if "{target}" in url:
        pairs = []
        for t in targets:
            pairs.append((f"{name}:{t}", url.replace("{target}", _q(t))))
        return pairs
    return [(name, url)]


def _q(s: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(s)
