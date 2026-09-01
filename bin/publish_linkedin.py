#!/usr/bin/env python3
"""Publish stage — reads state/decision.json, posts to LinkedIn, updates state.

Deliberately dumb and deterministic: it does NOT decide anything, it only
executes what the analyst wrote. Safety:

  * dry-run by default (settings.publish.dry_run, or CTI_DRY_RUN env)
  * every draft is archived to drafts/ regardless of dry-run
  * a stale decision.json (older than candidates.json) is treated as a skip
  * post text over the char limit is refused, not truncated
"""
from __future__ import annotations

import argparse
import json
import os
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


def log(msg: str) -> None:
    print(f"[publish] {msg}")


def truthy(v: str | None) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> dict:
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}


def is_dry_run(settings: dict) -> bool:
    env = os.getenv("CTI_DRY_RUN")
    if env is not None:
        return truthy(env)
    return bool(settings.get("publish", {}).get("dry_run", True))


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


def refresh_token() -> str | None:
    cid = os.getenv("LINKEDIN_CLIENT_ID")
    csec = os.getenv("LINKEDIN_CLIENT_SECRET")
    rtok = os.getenv("LINKEDIN_REFRESH_TOKEN")
    if not (cid and csec and rtok):
        return None
    log("access token rejected — attempting refresh")
    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": rtok,
        "client_id": cid,
        "client_secret": csec,
    }, timeout=20)
    r.raise_for_status()
    tok = r.json()["access_token"]
    _persist_token(tok)
    return tok


def _persist_token(tok: str) -> None:
    """Best-effort write of the new token back into config/env."""
    envf = ROOT / "config" / "env"
    if not envf.exists():
        return
    lines = envf.read_text().splitlines()
    done = False
    for i, ln in enumerate(lines):
        if ln.startswith("LINKEDIN_ACCESS_TOKEN="):
            lines[i] = f"LINKEDIN_ACCESS_TOKEN={tok}"
            done = True
    if not done:
        lines.append(f"LINKEDIN_ACCESS_TOKEN={tok}")
    envf.write_text("\n".join(lines) + "\n")
    os.environ["LINKEDIN_ACCESS_TOKEN"] = tok
    log("config/env updated with refreshed token")


def author_urn(token: str, version: str) -> str:
    pinned = os.getenv("LINKEDIN_AUTHOR_URN", "").strip()
    if pinned:
        return pinned
    r = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    r.raise_for_status()
    return f"urn:li:person:{r.json()['sub']}"


def post_to_linkedin(text: str, settings: dict) -> str:
    token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    version = str(settings.get("publish", {}).get("linkedin_api_version", "202506"))
    visibility = settings.get("publish", {}).get("visibility", "PUBLIC")

    def _do(tok: str) -> requests.Response:
        payload = {
            "author": author_urn(tok, version),
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

    resp = _do(token)
    if resp.status_code == 401:
        new = refresh_token()
        if new:
            resp = _do(new)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"LinkedIn {resp.status_code}: {resp.text[:500]}")
    post_id = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id") or ""
    return post_id


def record(decision: dict, post_id: str, dry: bool) -> None:
    sel = decision["selected"]
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "posted_at": now,
        "dry_run": dry,
        "post_id": post_id,
        "title": sel.get("title"),
        "url": sel.get("url"),
        "source": sel.get("source"),
        "score": sel.get("score"),
        "cve_ids": sel.get("cve_ids", []),
    }
    with (STATE / "history.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    if not dry:  # only a real post blocks the URL from ever being reconsidered
        with (STATE / "seen.jsonl").open("a") as f:
            f.write(json.dumps({"url": sel.get("url"), "posted_at": now}) + "\n")
        (STATE / "pending.json").unlink(missing_ok=True)
    (STATE / "last_run.json").write_text(json.dumps({
        "run_at": now, "decision": decision.get("decision"),
        "dry_run": dry, "post_id": post_id,
        "score": sel.get("score"), "title": sel.get("title"),
    }, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decision", default=str(STATE / "decision.json"))
    ap.add_argument("--force-post", action="store_true", help="ignore dry_run for this call")
    ap.add_argument("--approve", metavar="PATH",
                    help="post an operator-approved decision (from the UI); skips review-hold")
    args = ap.parse_args()

    settings = load_settings()
    mode = str(settings.get("publish", {}).get("mode", "auto")).lower()

    if args.approve:
        dpath = Path(args.approve)
        if not dpath.exists():
            log(f"--approve {dpath}: not found")
            return 3
        decision = json.loads(dpath.read_text())
        log("operator-approved decision — proceeding to publish")
    else:
        dpath = Path(args.decision)
        if not dpath.exists():
            log("no decision.json — analyst produced nothing")
            return 3
        cand = STATE / "candidates.json"
        if cand.exists() and dpath.stat().st_mtime < cand.stat().st_mtime:
            log("decision.json is older than candidates.json — stale, treating as skip")
            return 4
        decision = json.loads(dpath.read_text())

    archive_draft(decision)

    if decision.get("decision") != "post":
        log(f"decision=skip — {decision.get('reason')}")
        (STATE / "last_run.json").write_text(json.dumps({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "decision": "skip", "reason": decision.get("reason"),
        }, indent=2))
        return 0

    sel = decision.get("selected") or {}
    text = (sel.get("post_text") or "").strip()
    limit = int(settings.get("publish", {}).get("max_post_chars", 2900))
    if not text:
        log("decision=post but post_text empty — refusing")
        return 5
    if len(text) > limit:
        log(f"post_text {len(text)} chars > limit {limit} — refusing")
        return 5
    if sel.get("score", 0) < decision.get("min_score", 80):
        log(f"score {sel.get('score')} < threshold {decision.get('min_score')} — refusing")
        return 0

    # review mode: hold the post for a human to approve in the web UI
    if mode == "review" and not args.approve:
        pending = {
            "held_at": datetime.now(timezone.utc).isoformat(),
            "decision": decision,
        }
        (STATE / "pending.json").write_text(json.dumps(pending, indent=2))
        log("publish.mode=review — post held in state/pending.json for approval (open the UI)")
        (STATE / "last_run.json").write_text(json.dumps({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "decision": "pending_review", "score": sel.get("score"),
            "title": sel.get("title"),
        }, indent=2))
        return 0

    dry = is_dry_run(settings) and not args.force_post
    if dry:
        log("DRY RUN — not calling LinkedIn. Draft is archived above.")
        record(decision, post_id="", dry=True)
        return 0

    if not os.getenv("LINKEDIN_ACCESS_TOKEN"):
        log("LIVE mode but LINKEDIN_ACCESS_TOKEN is unset — cannot post")
        return 6

    log(f"posting to LinkedIn ({len(text)} chars, score {sel.get('score')})")
    post_id = post_to_linkedin(text, settings)
    log(f"published: {post_id or '(no id header)'}")
    record(decision, post_id=post_id, dry=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
