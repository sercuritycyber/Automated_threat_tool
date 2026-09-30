#!/usr/bin/env python3
"""CSAM safety filter for the onion aggregator — stdlib only.

The ONLY category this tool blocks is child sexual abuse material. Criminal
threat-intel forums (breaches, exploits, malware, actor chatter) are in scope;
this filter must not touch them. So it uses HIGH-PRECISION indicators only:

* an onion **blocklist** — addresses (or md5 hashes of "<addr>.onion", the
  format Ahmia's abuse blacklist uses) that are never fetched, stored, or shown;
* a small set of **specific** CSAM terms/abbreviations. Deliberately NOT the
  bare token "cp" — it appears constantly in legitimate tech discussion (the
  `cp` command, "cp/m", "cp = control plane") and would destroy the intel feed.

Anything the filter catches is dropped and counted; its address and context are
never persisted. Tunable via the ``safety:`` block in sources.yaml.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

# High-precision CSAM indicators. Kept specific to avoid false-positives on
# legitimate criminal-forum threat intel. Extend via safety.extra_terms.
_DEFAULT_TERMS = [
    "child porn", "child pornography", "childporn", "child sex",
    "csam", "pthc", "hurtcore", "jailbait", "preteen", "pre-teen",
    "pedomom", "pedo mom", "kinderporn", "loli hardcore",
    "childfuck", "toddlercore", "babyfuck", "infantcore",
]
# Phrases that only count when paired with a minor reference — reduces the
# chance of nuking legit forum names.
_MINOR = re.compile(r"\b(child|children|kid|kids|minor|minors|underage|"
                    r"preteen|pre-teen|toddler|infant|boy|girl)\b", re.I)
_SEXUAL = re.compile(r"\b(porn|pornography|sex|nude|nudes|xxx|hardcore|rape)\b", re.I)


class SafetyFilter:
    def __init__(self, terms: list[str] | None = None,
                 blocked_addresses: set[str] | None = None,
                 blocked_hashes: set[str] | None = None):
        all_terms = list(_DEFAULT_TERMS) + list(terms or [])
        # word/phrase match, case-insensitive
        self._patterns = [re.compile(r"(?<![a-z0-9])" + re.escape(t.lower()).replace(r"\ ", r"\s+") + r"(?![a-z0-9])")
                          for t in all_terms]
        self.blocked_addresses = {a.lower().strip() for a in (blocked_addresses or set())}
        self.blocked_hashes = {h.lower().strip() for h in (blocked_hashes or set())}
        self.blocked_count = 0

    # -- address-level ------------------------------------------------------ #
    def address_blocked(self, address: str) -> bool:
        a = address.lower().strip()
        if a in self.blocked_addresses:
            return True
        h = hashlib.md5(a.encode()).hexdigest()  # Ahmia blacklist format
        return h in self.blocked_hashes

    # -- text-level --------------------------------------------------------- #
    def text_flagged(self, text: str) -> bool:
        if not text:
            return False
        low = text.lower()
        for pat in self._patterns:
            if pat.search(low):
                return True
        # co-occurrence: a minor reference AND a sexual reference close together
        if _MINOR.search(text) and _SEXUAL.search(text):
            return True
        return False

    def allow(self, address: str, *context_texts: str) -> bool:
        """True if this address+context is safe to keep. Increments the blocked
        counter and returns False on any CSAM signal."""
        if self.address_blocked(address):
            self.blocked_count += 1
            return False
        for t in context_texts:
            if self.text_flagged(t):
                self.blocked_count += 1
                return False
        return True


def load(cfg: dict | None, base_dir: Path) -> SafetyFilter:
    """Build a filter from the ``safety:`` config block."""
    cfg = cfg or {}
    terms = cfg.get("extra_terms") or []
    addrs: set[str] = set()
    hashes: set[str] = set()
    for key, bucket in (("blocklist_addresses", addrs), ("blocklist_hashes", hashes)):
        path = cfg.get(key)
        if not path:
            continue
        p = Path(path)
        if not p.is_absolute():
            p = base_dir / p
        if p.exists():
            for line in p.read_text(errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    bucket.add(line.lower())
    return SafetyFilter(terms=terms, blocked_addresses=addrs, blocked_hashes=hashes)
