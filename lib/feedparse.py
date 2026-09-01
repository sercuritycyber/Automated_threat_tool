"""Minimal, dependency-free RSS 2.0 / Atom 1.0 parser.

Only needs the stdlib. Returns a list of normalised entry dicts:
    {title, link, summary, published (aware datetime, UTC), source}

This exists so the ingest step is deterministic and does not rely on
feedparser being installed system-wide on Fedora's externally-managed Python.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

_WS = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")


def _local(tag: str) -> str:
    """Strip the XML namespace: '{http://...}entry' -> 'entry'."""
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _text(el) -> str:
    if el is None:
        return ""
    raw = "".join(el.itertext()) if len(el) else (el.text or "")
    raw = _TAGS.sub(" ", raw)
    return _WS.sub(" ", raw).strip()


def _first(parent, *names):
    want = {n.lower() for n in names}
    for child in parent:
        if _local(child.tag) in want:
            return child
    return None


def _parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    # RFC 822 (RSS)
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    # RFC 3339 / ISO 8601 (Atom)
    iso = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _atom_link(entry) -> str:
    fallback = ""
    for child in entry:
        if _local(child.tag) != "link":
            continue
        rel = child.get("rel", "alternate")
        href = child.get("href", "")
        if rel == "alternate" and href:
            return href
        fallback = fallback or href
    return fallback


def parse(content: bytes | str, source: str = "") -> list[dict]:
    if isinstance(content, bytes):
        content = content.decode("utf-8", "replace")
    # Drop a leading BOM / whitespace that breaks ElementTree.
    content = content.lstrip("﻿ \r\n\t")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    entries: list[dict] = []
    root_tag = _local(root.tag)

    if root_tag == "rss" or root_tag == "channel":
        channel = root if root_tag == "channel" else _first(root, "channel")
        if channel is None:
            return []
        nodes = [c for c in channel if _local(c.tag) == "item"]
        for it in nodes:
            title = _text(_first(it, "title"))
            link = _text(_first(it, "link"))
            summary = _text(_first(it, "description", "summary", "encoded"))
            date_el = _first(it, "pubdate", "date", "published", "updated")
            published = _parse_date(_text(date_el)) if date_el is not None else None
            entries.append(_mk(title, link, summary, published, source))
    elif root_tag == "feed":  # Atom
        nodes = [c for c in root if _local(c.tag) == "entry"]
        for it in nodes:
            title = _text(_first(it, "title"))
            link = _atom_link(it)
            summary = _text(_first(it, "summary", "content"))
            date_el = _first(it, "published", "updated", "issued")
            published = _parse_date(_text(date_el)) if date_el is not None else None
            entries.append(_mk(title, link, summary, published, source))

    return [e for e in entries if e["title"] and e["link"]]


def _mk(title, link, summary, published, source) -> dict:
    return {
        "title": title,
        "link": link,
        "summary": summary[:1200],
        "published": published,
        "source": source,
    }
