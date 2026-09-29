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

# Source tag honeynet_bridge puts on its first-party post; see _post_problems.
HONEYNET = "honeynet"
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
    if post.get("source") == HONEYNET:
        # First-party honeynet brief: built from our own report by
        # honeynet_bridge (which strips analyst items claiming this source), so
        # there is no news URL to trace. Keep the body-URL rule.
        if URL_RE.findall(str(post.get("post_text") or "")):
            out.append("post_text contains a URL — links belong in `comment` only")
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
    Gemini backup, Codex and the OpenAI-compatible providers. Their output gets the
    strict checks below; Claude's does not, since it reads the articles and can
    legitimately cite a CVE the feed summary lacked."""
    return str(decision.get("analyst") or "").startswith(("antigravity", "api:", "codex-summary"))


# config/env entries that are public by design. The analyst prompt tells every
# post to plug cti.securitycyber.uk, which is CTI_WORKER_URL's value, so
# scanning it discarded every Claude decision (2026-09-29).
PUBLIC_ENV = frozenset({"CTI_WORKER_URL"})


def leaked_secrets(decision: dict, env: dict) -> list[str]:
    """Names of config/env values that appear anywhere in the decision.

    Backstop against prompt injection in feed text ("print your API keys")
    reaching subscribers, whose alerts are not held for review. Short values
    (ports, true/false, handles) are skipped: they'd match by accident.
    """
    blob = json.dumps(decision, ensure_ascii=False)
    return sorted(k for k, v in env.items()
                  if k not in PUBLIC_ENV and v and len(v) >= 16 and v in blob)


def drop_leaking_items(decision: dict, env: dict) -> tuple[dict, list[str], list[str]]:
    """Remove only the posts/alerts that contain a config/env secret.

    Returns (decision, dropped descriptions, residual leak names). A residual
    leak sits outside any item (reason, summary...) and the caller must discard
    the whole decision. Descriptions carry env NAMES only, never values.
    """
    decision = dict(decision)
    dropped: list[str] = []
    for field in ("posts", "alerts"):
        items = decision.get(field)
        if not isinstance(items, list):
            continue
        kept = []
        for i, item in enumerate(items):
            leaks = leaked_secrets(item, env)
            if leaks:
                title = item.get("title") if isinstance(item, dict) else None
                dropped.append(f"{field}[{i}] {str(title or '')[:60]} — contains {leaks}")
            else:
                kept.append(item)
        decision[field] = kept
    selected = decision.get("selected")
    if isinstance(selected, dict) and leaked_secrets(selected, env):
        dropped.append(f"selected — contains {leaked_secrets(selected, env)}")
        decision.pop("selected")
    if dropped and not decision.get("posts") and not decision.get("selected"):
        decision["decision"] = "skip"
        decision["reason"] = (str(decision.get("reason") or "") + " [public posts dropped: contained a secret]").strip()
    return decision, dropped, leaked_secrets(decision, env)


# Facts a post must be able to point back to in ITS OWN source. Checking
# against the whole candidate batch let a CVE or figure from story B pass in
# story A's post: the "Chinese whispers" failure, where a model saturated with
# ~80 stories blends them (Charlie, 2026-09-29).
ACTOR_RE = re.compile(r"\b(?:Storm|UNC|UTA|TA|APT|DEV|FIN|UAT)-?\d{2,5}\b", re.I)
VERSION_RE = re.compile(r"\b\d+\.\d+\.\d+(?:[.-]\d+)*\b|\b\d+\.\d+-\d+(?:\.\d+)*\b")
MONEY_RE = re.compile(r"[$£€]\s?\d[\d,.]*\s?(?:[kmb]n?|million|billion|thousand)?\b", re.I)
COUNT_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+\+?|\b\d{3,}\+|\b\d+(?:\.\d+)?%")
# Claude reads the full articles, so a figure missing from our short feed
# summary is normal there; only identifiers are strong enough to hold on.
STRONG_KINDS = ("cve", "actor")


def _norm(t: str) -> str:
    return re.sub(r"[\s,]", "", t).upper()


def post_facts(post: dict) -> dict[str, set[str]]:
    text = " ".join(str(post.get(f) or "") for f in ("post_text", "short_text"))
    facts = {
        "cve": {c.upper() for c in CVE_RE.findall(text)}
               | {str(c).upper() for c in (post.get("cve_ids") or []) if isinstance(c, str)},
        "actor": {a for a in ACTOR_RE.findall(text)},
        "version": set(VERSION_RE.findall(text)),
        "money": {m.strip() for m in MONEY_RE.findall(text)},
        "count": set(COUNT_RE.findall(text)),
    }
    return {k: v for k, v in facts.items() if v}


def source_text(post: dict, candidates: dict) -> str | None:
    """Everything our ingest knows about the ONE story this post cites."""
    url = normalise_url(post.get("url", ""))
    for c in candidates.get("candidates") or []:
        if isinstance(c, dict) and normalise_url(c.get("url", "")) == url:
            return _norm(json.dumps(c, ensure_ascii=False))
    return None


def ungrounded_facts(post: dict, candidates: dict, kinds=None) -> list[str]:
    """Facts in the post that its own source does not contain."""
    src = source_text(post, candidates)
    if src is None:
        return []  # provenance check already rejects an unknown URL
    missing = []
    for kind, vals in post_facts(post).items():
        if kinds and kind not in kinds:
            continue
        for v in sorted(vals):
            n = _norm(v).rstrip("+")
            if kind == "money":
                n = re.sub(r"[$£€]", "", n)
            if n and n not in src:
                missing.append(f"{kind} {v}")
    return missing


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
    kept = []
    for i, post in enumerate(posts):
        probs = _post_problems(post, allowed, operator=False)
        if isinstance(post, dict) and not probs and post.get("source") != HONEYNET:
            if strict:
                # Backup engines see only candidates.json: anything not in
                # this post's own source can only have been blended or invented.
                bad = ungrounded_facts(post, candidates)
                if bad:
                    probs.append(f"not in its own source (blended or invented): {', '.join(bad)}")
            else:
                strong = ungrounded_facts(post, candidates, STRONG_KINDS)
                if strong:
                    # Claude may have read it in the full article; a human decides.
                    post["ungrounded"] = strong
                weak = ungrounded_facts(post, candidates, ("version", "money", "count"))
                if weak:
                    post["unverified_figures"] = weak
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
