"""Safety checks for model-produced analyst decisions."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


URL_RE = re.compile(r"https?://[^\s<>\])]+", re.I)


def normalise_url(url: str) -> str:
    """Match ingest URL normalisation without importing the ingest script."""
    try:
        parsed = urlsplit(str(url).strip())
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, parsed.query, ""))


def validate_decision(decision: dict, candidates: dict, *, operator: bool = False) -> list[str]:
    """Return blocking errors; model output must not create new source URLs."""
    errors: list[str] = []
    if decision.get("decision") not in {"post", "skip"}:
        errors.append("decision must be 'post' or 'skip'")
    posts = decision.get("posts")
    if posts is None and isinstance(decision.get("selected"), dict):
        posts = [decision["selected"]]
    if not isinstance(posts, list):
        errors.append("posts must be a list")
        return errors
    allowed_urls = {
        normalise_url(c.get("url"))
        for c in (candidates.get("candidates") or [])
        if isinstance(c, dict) and c.get("url")
    }
    for index, post in enumerate(posts):
        if not isinstance(post, dict):
            errors.append(f"posts[{index}] must be an object")
            continue
        if operator:
            continue
        url = normalise_url(post.get("url", ""))
        if not url or url not in allowed_urls:
            errors.append(f"posts[{index}] URL is not one of the current candidate sources")
        score = post.get("score")
        try:
            numeric_score = float(score)
        except (TypeError, ValueError):
            errors.append(f"posts[{index}] score is not numeric")
        else:
            if not 0 <= numeric_score <= 100:
                errors.append(f"posts[{index}] score must be between 0 and 100")
        text = str(post.get("post_text") or "")
        if url and url not in {normalise_url(u) for u in URL_RE.findall(text)}:
            errors.append(f"posts[{index}] post_text does not contain its source URL")
    return errors
