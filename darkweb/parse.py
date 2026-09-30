#!/usr/bin/env python3
"""HTML -> text extraction, stdlib only (html.parser).

We deliberately avoid a DOM library. Two outputs:

* :func:`extract_text` — readable text with script/style/nav stripped, used for
  storage and matching.
* :func:`extract_links` — same-host hrefs with their anchor text. The collector
  does NOT auto-follow these; they are surfaced so an analyst can decide which
  thread URLs to add to the registry. (Targeted collection, by design.)
* :func:`title` — the ``<title>`` text.

Forum front-ends differ wildly and the onion addresses churn, so there is no
attempt at per-site DOM scraping here — plain text + link inventory is robust
across BreachForums/XSS/Dread-style boards and survives redesigns.
"""
from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

_SKIP = {"script", "style", "noscript", "template", "svg"}
_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
          "article", "section", "table", "ul", "ol", "blockquote"}


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self.title_parts: list[str] = []
        self._in_title = False
        self.links: list[tuple[str, str]] = []
        self._cur_href: str | None = None
        self._cur_anchor: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self._cur_href = v
                    self._cur_anchor = []
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._cur_href is not None:
            self.links.append((self._cur_href, " ".join(self._cur_anchor).strip()))
            self._cur_href = None
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        if self._cur_href is not None:
            self._cur_anchor.append(data)
        self.parts.append(data)


def _collapse(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    out: list[str] = []
    blank = False
    for ln in lines:
        if ln:
            out.append(" ".join(ln.split()))
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def _parse(html: str) -> _TextParser:
    p = _TextParser()
    try:
        p.feed(html)
    except Exception:
        pass  # best-effort on malformed markup
    return p


def extract_text(html: str) -> str:
    return _collapse(unescape("".join(_parse(html).parts)))


def title(html: str) -> str:
    return " ".join("".join(_parse(html).title_parts).split())[:300]


def extract_links(html: str, base_url: str, same_host_only: bool = True) -> list[dict]:
    p = _parse(html)
    base_host = urlsplit(base_url).hostname or ""
    seen: set[str] = set()
    out: list[dict] = []
    for href, anchor in p.links:
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(base_url, href)
        host = urlsplit(absolute).hostname or ""
        if same_host_only and host != base_host:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append({"url": absolute, "anchor": anchor[:200]})
    return out


def parse_page(html: str, base_url: str) -> dict:
    """One-shot: everything the collector stores about a page."""
    p = _parse(html)
    text = _collapse(unescape("".join(p.parts)))
    ttl = " ".join("".join(p.title_parts).split())[:300]
    base_host = urlsplit(base_url).hostname or ""
    links = []
    seen: set[str] = set()
    for href, anchor in p.links:
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(base_url, href)
        if (urlsplit(absolute).hostname or "") != base_host:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append({"url": absolute, "anchor": anchor[:200]})
    return {"title": ttl, "text": text, "links": links}
