#!/usr/bin/env python3
"""Match watchlist terms and honeypot IOCs against collected text.

Two indicator sources:

* **Watchlist** (``watchlist.yaml``) — brands, domains, email addresses,
  handles, project names you care about. The reason to watch these forums:
  spotting your own / your clients' exposure early.
* **Honeypot IOCs** — the payoff for pairing this with honeyintel. Attacker
  IPs, the usernames and passwords they sprayed at the honeypot, and file
  hashes. When a credential the honeypot captured turns up for sale, or an
  attacking IP is discussed, that's a direct link between your telemetry and
  the market.

Guards against noise: a minimum token length, a stoplist of ubiquitous
credentials (``admin``/``root``/``123456`` match everything), and IP/hash
matching only on well-formed values. Credential *pairs* found together are
flagged as higher confidence than a lone username.
"""
from __future__ import annotations

import csv
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml  # PyYAML is already a pipeline dependency
except Exception:  # pragma: no cover
    yaml = None

# Credentials so common that a match tells you nothing.
COMMON_CREDS = {
    "admin", "root", "test", "guest", "user", "password", "pass", "123456",
    "12345678", "123456789", "qwerty", "111111", "000000", "1234", "12345",
    "letmein", "welcome", "changeme", "oracle", "postgres", "ubuntu", "pi",
    "raspberry", "support", "default", "toor", "administrator", "",
}
MIN_TERM_LEN = 4
CONTEXT_CHARS = 120

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_HASH = re.compile(r"\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|\b[a-fA-F0-9]{64}\b")


@dataclass(frozen=True)
class Indicator:
    kind: str          # watchlist | honeypot-ip | honeypot-cred | honeypot-user | honeypot-hash
    value: str         # canonical form used for reporting
    needle: str        # lowercased form searched for
    word_boundary: bool = False


def _ctx(text_lower: str, text: str, idx: int, needle_len: int) -> str:
    start = max(0, idx - CONTEXT_CHARS)
    end = min(len(text), idx + needle_len + CONTEXT_CHARS)
    return " ".join(text[start:end].split())


class Matcher:
    def __init__(self, indicators: list[Indicator]):
        # De-dupe by (kind, needle); keep the richer canonical value.
        uniq: dict[tuple[str, str], Indicator] = {}
        for ind in indicators:
            if len(ind.needle) < MIN_TERM_LEN:
                continue
            uniq.setdefault((ind.kind, ind.needle), ind)
        self.indicators = list(uniq.values())
        # Pre-compile word-boundary patterns once.
        self._wb = {
            ind.needle: re.compile(r"\b" + re.escape(ind.needle) + r"\b", re.I)
            for ind in self.indicators if ind.word_boundary
        }

    def scan(self, text: str) -> list[dict]:
        """Return hit dicts: {kind, indicator, context}."""
        hits: list[dict] = []
        lower = text.lower()
        for ind in self.indicators:
            if ind.word_boundary:
                m = self._wb[ind.needle].search(text)
                if m:
                    hits.append({
                        "kind": ind.kind, "indicator": ind.value,
                        "context": _ctx(lower, text, m.start(), len(m.group(0))),
                    })
            else:
                idx = lower.find(ind.needle)
                if idx != -1:
                    hits.append({
                        "kind": ind.kind, "indicator": ind.value,
                        "context": _ctx(lower, text, idx, len(ind.needle)),
                    })
        return hits


# --------------------------------------------------------------------------- #
#  Indicator loaders
# --------------------------------------------------------------------------- #
def load_watchlist(path: str | Path) -> list[Indicator]:
    if yaml is None:
        raise RuntimeError("PyYAML required to load the watchlist")
    data = yaml.safe_load(Path(path).read_text()) or {}
    out: list[Indicator] = []
    for term in data.get("terms", []) or []:
        if term:
            out.append(Indicator("watchlist", str(term), str(term).lower(),
                                  word_boundary=True))
    for dom in data.get("domains", []) or []:
        if dom:
            out.append(Indicator("watchlist", str(dom), str(dom).lower()))
    for eml in data.get("emails", []) or []:
        if eml:
            out.append(Indicator("watchlist", str(eml), str(eml).lower()))
    for h in data.get("handles", []) or []:
        if h:
            out.append(Indicator("watchlist", str(h), str(h).lower(),
                                  word_boundary=True))
    return out


def load_honeypot_ips(path: str | Path, column: str = "src_ip") -> list[Indicator]:
    """Load attacker IPs from a CSV (e.g. honeyintel iocs.csv)."""
    out: list[Indicator] = []
    for row in _csv_rows(path):
        raw = (row.get(column) or "").strip()
        for m in _IPV4.findall(raw):
            try:
                ip = ipaddress.ip_address(m)
            except ValueError:
                continue
            if ip.is_global:  # ignore RFC1918 / loopback noise
                out.append(Indicator("honeypot-ip", str(ip), str(ip).lower()))
    return out


def load_honeypot_creds(
    path: str | Path, user_col: str = "username", pass_col: str = "password",
) -> list[Indicator]:
    """Load usernames/passwords the honeypot captured (e.g. creds.csv).

    Passwords that aren't ubiquitous become high-signal needles; usernames too,
    but common service accounts are dropped.
    """
    out: list[Indicator] = []
    for row in _csv_rows(path):
        user = (row.get(user_col) or "").strip()
        pw = (row.get(pass_col) or "").strip()
        if user and user.lower() not in COMMON_CREDS and len(user) >= MIN_TERM_LEN:
            out.append(Indicator("honeypot-user", user, user.lower(),
                                 word_boundary=True))
        if pw and pw.lower() not in COMMON_CREDS and len(pw) >= MIN_TERM_LEN:
            out.append(Indicator("honeypot-cred", pw, pw.lower()))
    return out


def load_honeypot_hashes(path: str | Path, column: str = "sha256") -> list[Indicator]:
    out: list[Indicator] = []
    for row in _csv_rows(path):
        raw = (row.get(column) or "").strip()
        for m in _HASH.findall(raw):
            out.append(Indicator("honeypot-hash", m.lower(), m.lower()))
    return out


def _csv_rows(path: str | Path):
    p = Path(path)
    if not p.exists():
        return
    with p.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            yield row
