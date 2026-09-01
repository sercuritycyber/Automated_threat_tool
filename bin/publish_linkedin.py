#!/usr/bin/env python3
"""Publish stage — turns an analyst decision into a LinkedIn post.

Deliberately dumb: it does not decide anything, it executes what the analyst
(or the operator, via --approve) already decided. Safety:

  * dry-run unless explicitly turned off (settings.publish.dry_run / CTI_DRY_RUN)
  * every draft is archived to drafts/ regardless
  * a stale decision.json (older than candidates.json) is treated as a skip
  * over-limit post text is refused, not truncated
  * DRY-RUN simulations are logged to state/dry_runs.jsonl, NOT history.jsonl,
    so they never pollute the analyst's "already posted" dedup
  * real posts write history.jsonl + seen.jsonl and clear the review queue
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
DRAFTS = ROOT / "drafts"
DRAFTS.mkdir(exist_ok=True)

USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
URN_RE = re.compile(r"^urn:li:(person|organization):[A-Za-z0-9_-]+$")
# LinkedIn only keeps ~12 monthly versions active; an old pin returns HTTP 426.
DEFAULT_LI_VERSION = "202509"


def log(msg: str) -> None:
    print(f"[publish] {msg}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def truthy(v: str | None) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> dict:
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}


def is_dry_run(settings: dict) -> bool:
    env = os.getenv("CTI_DRY_RUN")
    if env not in (None, ""):
        return truthy(env)
    return bool(settings.get("publish", {}).get("dry_run", True))


def write_last_run(**kw) -> None:
    kw.setdefault("run_at", now_iso())
    (STATE / "last_run.json").write_text(json.dumps(kw, indent=2))


def post_url_from_urn(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/" if urn.startswith("urn:li:") else ""


# --------------------------------------------------------------------------- draft archive
def archive_draft(decision: dict) -> Path:
    sel = decision.get("selected") or {}
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = "skip" if decision.get("decision") != "post" else "post"
    path = DRAFTS / f"{ts}-{slug}.md"
    body = textwrap.dedent(f"""\
        # CTI run {decision.get('run_at', ts)}

        decision : {decision.get('decision')}
        reason   : {decision.get('reason')}
        score    : {sel.get('score')}  (threshold {decision.get('min_score')})
        confidence: {sel.get('confidence')}
        title    : {sel.get('title')}
        source   : {sel.get('source')}
        url      : {sel.get('url')}
        cves     : {', '.join(sel.get('cve_ids', []) or [])}

        ## score_breakdown
        {chr(10).join('  - ' + b for b in sel.get('score_breakdown', []) or [])}

        ## post_text
        {sel.get('post_text', '(none)')}

        ## considered
        {chr(10).join(f"  [{c.get('score')}] {c.get('title')} — {c.get('note')}" for c in decision.get('considered', []) or [])}
        """)
    path.write_text(body)
    log(f"draft archived -> {path.relative_to(ROOT)}")
    return path


# --------------------------------------------------------------------------- LinkedIn
def refresh_token() -> str | None:
    cid = os.getenv("LINKEDIN_CLIENT_ID")
    csec = os.getenv("LINKEDIN_CLIENT_SECRET")
    rtok = os.getenv("LINKEDIN_REFRESH_TOKEN")
    if not (cid and csec and rtok):
        log("token rejected and no CLIENT_ID/SECRET/REFRESH_TOKEN set — cannot refresh")
        return None
    log("access token rejected — attempting refresh")
    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token", "refresh_token": rtok,
        "client_id": cid, "client_secret": csec,
    }, timeout=20)
    r.raise_for_status()
    tok = r.json()["access_token"]
    _persist_token(tok)
    return tok


def _persist_token(tok: str) -> None:
    envf = ROOT / "config" / "env"
    if not envf.exists():
        return
    lines = envf.read_text().splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("LINKEDIN_ACCESS_TOKEN="):
            lines[i] = f"LINKEDIN_ACCESS_TOKEN={tok}"
            break
    else:
        lines.append(f"LINKEDIN_ACCESS_TOKEN={tok}")
    envf.write_text("\n".join(lines) + "\n")
    os.chmod(envf, 0o600)
    os.environ["LINKEDIN_ACCESS_TOKEN"] = tok
    log("config/env updated with refreshed token")


def userinfo(token: str) -> dict:
    r = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    r.raise_for_status()
    return r.json()


def resolve_author_urn(token: str) -> str:
    """Pinned LINKEDIN_AUTHOR_URN wins — but a *person* URN is sanity-checked
    against /userinfo and silently corrected if it doesn't match the token."""
    pinned = os.getenv("LINKEDIN_AUTHOR_URN", "").strip()
    real_sub = None
    try:
        real_sub = userinfo(token).get("sub")
    except requests.RequestException as e:
        log(f"userinfo lookup failed ({e}) — relying on pinned URN")

    if pinned and URN_RE.match(pinned):
        if pinned.startswith("urn:li:organization:"):
            return pinned
        if real_sub and pinned != f"urn:li:person:{real_sub}":
            log(f"pinned LINKEDIN_AUTHOR_URN ({pinned}) does not match this token "
                f"(urn:li:person:{real_sub}) — using the token's own URN")
            return f"urn:li:person:{real_sub}"
        return pinned
    if pinned:
        log(f"ignoring malformed LINKEDIN_AUTHOR_URN: {pinned!r}")
    if real_sub:
        return f"urn:li:person:{real_sub}"
    raise RuntimeError("cannot determine author URN: no valid LINKEDIN_AUTHOR_URN and /userinfo failed")


def _version_candidates(pinned: str) -> list[str]:
    """The configured version first, then a walk forward through recent months,
    so a stale pin (HTTP 426) self-heals without an edit."""
    out = [pinned] if pinned else []
    now = datetime.now(timezone.utc)
    for back in range(12, -2, -1):          # ~12 months ago → ~1 month ahead
        y, m = now.year, now.month - back
        while m <= 0:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        v = f"{y}{m:02d}"
        if v not in out:
            out.append(v)
    return out


def post_to_linkedin(text: str, settings: dict) -> tuple[str, str]:
    token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    pinned = str(settings.get("publish", {}).get("linkedin_api_version", DEFAULT_LI_VERSION))
    visibility = settings.get("publish", {}).get("visibility", "PUBLIC")

    def _do(tok: str, version: str) -> requests.Response:
        payload = {
            "author": resolve_author_urn(tok),
            "commentary": text,
            "visibility": visibility,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        return requests.post(POSTS_URL, headers={
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": version,
        }, json=payload, timeout=30)

    resp = None
    for version in _version_candidates(pinned):
        resp = _do(token, version)
        if resp.status_code == 401:
            new = refresh_token()
            if new:
                resp = _do(new, version)
        if resp.status_code == 426:          # version not active — try the next one
            log(f"LinkedIn-Version {version} not active, trying another")
            continue
        break
    if resp is None or resp.status_code not in (200, 201):
        body = resp.text[:600] if resp is not None else "no response"
        code = resp.status_code if resp is not None else "?"
        raise RuntimeError(f"LinkedIn HTTP {code}: {body}")
    if version != pinned:
        log(f"posted with LinkedIn-Version {version} — update publish.linkedin_api_version in Settings")
    urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id") or ""
    return urn, post_url_from_urn(urn)


# --------------------------------------------------------------------------- recording
def record_dry(decision: dict) -> None:
    sel = decision["selected"]
    row = {"simulated_at": now_iso(), "title": sel.get("title"), "url": sel.get("url"),
           "score": sel.get("score")}
    with (STATE / "dry_runs.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    write_last_run(decision="post", published=False, dry_run=True,
                   title=sel.get("title"), score=sel.get("score"),
                   note="dry-run simulation — nothing sent to LinkedIn")


def record_real(decision: dict, urn: str, url: str) -> None:
    sel = decision["selected"]
    now = now_iso()
    row = {
        "posted_at": now, "dry_run": False, "post_id": urn, "post_url": url,
        "title": sel.get("title"), "url": sel.get("url"), "source": sel.get("source"),
        "score": sel.get("score"), "cve_ids": sel.get("cve_ids", []),
    }
    with (STATE / "history.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    with (STATE / "seen.jsonl").open("a") as f:
        f.write(json.dumps({"url": sel.get("url"), "posted_at": now}) + "\n")
    for tmp in ("pending.json", "approved.json"):
        (STATE / tmp).unlink(missing_ok=True)
    write_last_run(decision="post", published=True, dry_run=False,
                   title=sel.get("title"), score=sel.get("score"),
                   post_id=urn, post_url=url)


# --------------------------------------------------------------------------- main
def load_decision(args) -> tuple[dict, bool]:
    """returns (decision, operator_approved)"""
    if args.approve is not None:
        path = Path(args.approve) if args.approve else (STATE / "pending.json")
        if not path.exists():
            log(f"--approve: {path} not found (nothing waiting)")
            sys.exit(3)
        blob = json.loads(path.read_text())
        decision = blob.get("decision", blob)  # pending.json wraps it; approved.json is bare
        log(f"operator-approved decision from {path.name}")
        return decision, True

    path = Path(args.decision)
    if not path.exists():
        log("no decision.json — analyst produced nothing")
        sys.exit(3)
    cand = STATE / "candidates.json"
    if cand.exists() and path.stat().st_mtime < cand.stat().st_mtime:
        log("decision.json is older than candidates.json — stale, treating as skip")
        sys.exit(4)
    return json.loads(path.read_text()), False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decision", default=str(STATE / "decision.json"))
    ap.add_argument("--force-post", action="store_true", help="ignore dry_run for this call")
    ap.add_argument("--approve", nargs="?", const="", metavar="PATH",
                    help="publish an operator-approved post (default: state/pending.json)")
    args = ap.parse_args()

    settings = load_settings()
    mode = str(settings.get("publish", {}).get("mode", "auto")).lower()

    decision, operator = load_decision(args)
    archive_draft(decision)

    if decision.get("decision") != "post":
        log(f"decision=skip — {decision.get('reason')}")
        write_last_run(decision="skip", published=False, reason=decision.get("reason"))
        return 0

    sel = decision.get("selected") or {}
    text = (sel.get("post_text") or "").strip()
    limit = int(settings.get("publish", {}).get("max_post_chars", 2900))
    if not text:
        log("decision=post but post_text is empty — refusing")
        write_last_run(decision="post", published=False, error="empty post_text")
        return 5
    if len(text) > limit:
        log(f"post_text {len(text)} chars > limit {limit} — refusing")
        write_last_run(decision="post", published=False, error=f"post too long ({len(text)}>{limit})")
        return 5
    if not operator and sel.get("score", 0) < decision.get("min_score", 80):
        log(f"score {sel.get('score')} < threshold {decision.get('min_score')} — not posting")
        write_last_run(decision="skip", published=False,
                       reason=f"score {sel.get('score')} below threshold {decision.get('min_score')}")
        return 0

    # review mode: an automatic run holds the post for a human; --approve skips this
    if mode == "review" and not operator:
        (STATE / "pending.json").write_text(json.dumps(
            {"held_at": now_iso(), "decision": decision}, indent=2))
        log("publish.mode=review — held in state/pending.json for approval (open the panel)")
        write_last_run(decision="pending_review", published=False,
                       title=sel.get("title"), score=sel.get("score"))
        return 0

    dry = is_dry_run(settings) and not args.force_post
    if dry:
        log("DRY-RUN — nothing sent to LinkedIn (logged to state/dry_runs.jsonl). "
            "Turn off dry-run in Settings to post for real.")
        record_dry(decision)
        return 0

    if not os.getenv("LINKEDIN_ACCESS_TOKEN"):
        log("LIVE mode but LINKEDIN_ACCESS_TOKEN is unset — cannot post")
        write_last_run(decision="post", published=False, error="no LINKEDIN_ACCESS_TOKEN")
        return 6

    log(f"posting to LinkedIn ({len(text)} chars, score {sel.get('score')}) …")
    try:
        urn, url = post_to_linkedin(text, settings)
    except (requests.RequestException, RuntimeError) as e:
        log(f"POST FAILED: {e}")
        write_last_run(decision="post", published=False, error=str(e)[:600],
                       title=sel.get("title"))
        return 7
    log(f"published: {urn or '(no id header)'}  {url}")
    record_real(decision, urn, url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
