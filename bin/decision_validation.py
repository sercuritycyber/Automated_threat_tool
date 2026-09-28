"""Safety checks for model-produced analyst decisions.

Model output is untrusted: a post must reference a URL that was actually in this
run's candidate set, that URL must appear in `comment` (the first-comment text),
and `post_text` must contain no URL at all. But a validation failure must NOT
sink the whole pipeline — `sanitize_decision` drops the offending social posts
and lets the run continue so subscribers still get served.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

URL_RE = re.compile(r"https?://[^\s<>\])]+", re.I)
CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.I)
_TRACKING = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "mkt_tok", "fbclid", "gclid", "ref", "ref_src", "source",
}


def normalise_url(url: str) -> str:
    """Host + path, https, www- and tracking-param stripped — matches the way
    bin/ingest.py stores candidate URLs, plus we drop the query entirely for
    matching so `?utm=…` on an otherwise-real URL still resolves to its source."""
    try:
        s = urlsplit(str(url).strip())
    except ValueError:
        return ""
    host = (s.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = s.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, "", ""))


def _candidate_urls(candidates: dict) -> set[str]:
    return {
        normalise_url(c.get("url"))
        for c in (candidates.get("candidates") or [])
        if isinstance(c, dict) and c.get("url")
    }


def validate_decision(decision: dict, candidates: dict, *, operator: bool = False) -> list[str]:
    """Return a list of blocking problems (empty == clean). Kept for callers
    that want a yes/no; the pipeline uses sanitize_decision instead."""
    errors: list[str] = []
    if decision.get("decision") not in {"post", "skip"}:
        errors.append("decision must be 'post' or 'skip'")
    posts = decision.get("posts")
    if posts is None and isinstance(decision.get("selected"), dict):
        posts = [decision["selected"]]
    if not isinstance(posts, list):
        errors.append("posts must be a list")
        return errors
    allowed = _candidate_urls(candidates)
    for i, post in enumerate(posts):
        errors.extend(f"posts[{i}] {p}" for p in _post_problems(post, allowed, operator))
    return errors


def _post_problems(post: dict, allowed: set[str], operator: bool) -> list[str]:
    out: list[str] = []
    if not isinstance(post, dict):
        return ["is not an object"]
    if operator:
        return out
    url = normalise_url(post.get("url", ""))
    if not url or url not in allowed:
        out.append("URL is not one of this run's candidate sources")
    score = post.get("score")
    try:
        n = float(score)
        if not 0 <= n <= 100:
            out.append("score must be 0-100")
    except (TypeError, ValueError):
        out.append("score is not numeric")
    # The source link lives in `comment` (posted as the first comment), not in
    # post_text — a raw URL in the LinkedIn body throttles reach.
    body = str(post.get("post_text") or "")
    comment = str(post.get("comment") or "")
    if url and url not in {normalise_url(u) for u in URL_RE.findall(comment)}:
        out.append("comment is missing the source URL")
    body_urls = {normalise_url(u) for u in URL_RE.findall(body)}
    if body_urls:
        out.append("post_text contains a URL — links belong in `comment` only")
    return out


def is_backup_analyst(decision: dict) -> bool:
    """True for engines that only see candidates.json (no web access): the
    Gemini backup and the OpenAI-compatible providers. Their output gets the
    strict checks below; Claude's does not, since it reads the articles and can
    legitimately cite a CVE the feed summary lacked."""
    return str(decision.get("analyst") or "").startswith(("antigravity", "api:", "codex-summary"))


def _ungrounded_cves(post: dict, cand_text: str) -> list[str]:
    cited = {c.upper() for c in (post.get("cve_ids") or []) if isinstance(c, str)}
    cited |= {c.upper() for c in CVE_RE.findall(str(post.get("post_text") or ""))}
    return sorted(c for c in cited if c not in cand_text)


def sanitize_decision(decision: dict, candidates: dict) -> tuple[dict, list[str]]:
    """Drop any social post that fails provenance; never raise. Returns
    (possibly-modified decision, human-readable list of what was dropped).
    `alerts[]` (subscriber briefs) are left intact — they carry more analysis
    and less blast radius, and silently losing them would hurt paying users."""
    dropped: list[str] = []
    if decision.get("decision") == "skip":
        return decision, dropped
    posts = decision.get("posts")
    if posts is None and isinstance(decision.get("selected"), dict):
        posts = [decision["selected"]]
    if not isinstance(posts, list):
        return decision, dropped
    allowed = _candidate_urls(candidates)
    strict = is_backup_analyst(decision)
    cand_text = json.dumps(candidates).upper() if strict else ""
    kept = []
    for i, post in enumerate(posts):
        probs = _post_problems(post, allowed, operator=False)
        if strict and isinstance(post, dict):
            bad = _ungrounded_cves(post, cand_text)
            if bad:
                probs.append(f"cites {', '.join(bad)}, not in any candidate (possible invention)")
        if probs:
            title = (post.get("title") or post.get("url") or f"post {i}") if isinstance(post, dict) else f"post {i}"
            dropped.append(f"{str(title)[:60]} — {'; '.join(probs)}")
        else:
            kept.append(post)
    if dropped:
        decision = dict(decision)
        decision["posts"] = kept
        if not kept:
            decision["decision"] = "skip"
            decision["reason"] = (decision.get("reason", "") + " [all public posts failed provenance validation]").strip()
    return decision, dropped
