#!/usr/bin/env python3
"""Publish stage — turns analyst decisions into social posts.

Deliberately dumb: it executes what the analyst (or the operator) decided.

  * dry-run unless explicitly turned off (settings.publish.dry_run / CTI_DRY_RUN)
  * every run's drafts are archived to drafts/
  * a stale decision.json (older than candidates.json) is treated as a skip
  * over-limit post text is refused, not truncated
  * DRY-RUN simulations go to state/dry_runs.jsonl, never history.jsonl
  * real posts append history.jsonl + seen.jsonl and clear the review queue

Multi-post: the analyst may hand us several stories in `posts: [...]`. Each is
scored independently. Non-severe extras past the daily cap are held for review;
stories at/above publish.severity_override_score post regardless of the cap, so
a genuinely huge news day goes out in full.

Targets: settings.publish.targets (default ["linkedin"]) — see bin/publishers.py.

CLI:
  publish_linkedin.py --decision state/decision.json     # normal pipeline run
  publish_linkedin.py --approve [PATH]                   # operator-approved queue
  publish_linkedin.py --text "..." --targets linkedin,mastodon   # manual one-off
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import requests  # noqa: F401  (kept for exception typing in publishers)
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
try:
    from lint_post import lint as lint_text
except ImportError:
    def lint_text(_t, _c=None):
        return {"ok": True, "errors": [], "warnings": []}
from postlog import count_last_24h
import publishers

STATE = ROOT / "state"
DRAFTS = ROOT / "drafts"
DRAFTS.mkdir(exist_ok=True)


def log(msg: str) -> None:
    print(f"[publish] {msg}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def truthy(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> dict:
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}


def is_dry_run(settings: dict) -> bool:
    env = os.getenv("CTI_DRY_RUN")
    if env not in (None, ""):
        return truthy(env)
    return bool(settings.get("publish", {}).get("dry_run", True))


def creds() -> dict:
    return dict(os.environ)


def write_last_run(**kw) -> None:
    kw.setdefault("run_at", now_iso())
    (STATE / "last_run.json").write_text(json.dumps(kw, indent=2))


# --------------------------------------------------------------------------- drafts
def archive_draft(decision: dict, posts: list[dict]) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = "post" if decision.get("decision") == "post" and posts else "skip"
    lines = [f"# CTI run {decision.get('run_at', ts)}", "",
             f"decision : {decision.get('decision')}",
             f"reason   : {decision.get('reason')}",
             f"threshold: {decision.get('min_score')}", ""]
    for i, p in enumerate(posts, 1):
        lines += [f"## post {i} — score {p.get('score')} ({p.get('confidence')})",
                  f"{p.get('title')}  <{p.get('url')}>",
                  f"cves: {', '.join(p.get('cve_ids', []) or [])}", "",
                  p.get("post_text", "(none)"), ""]
        if p.get("short_text"):
            lines += [f"[short] {p['short_text']}", ""]
    for c in decision.get("considered", []) or []:
        lines.append(f"  [{c.get('score')}] {c.get('title')} — {c.get('note')}")
    (DRAFTS / f"{ts}-{slug}.md").write_text("\n".join(lines))
    log(f"draft archived -> drafts/{ts}-{slug}.md")


# --------------------------------------------------------------------------- normalising
def _posts_from(decision: dict) -> list[dict]:
    if isinstance(decision.get("posts"), list) and decision["posts"]:
        return [p for p in decision["posts"] if isinstance(p, dict)]
    if isinstance(decision.get("selected"), dict):
        return [decision["selected"]]
    return []


def load_decision(args) -> tuple[dict, list[dict], bool]:
    """returns (decision, posts, operator_approved)"""
    # ---- pure manual one-off ------------------------------------------------
    if args.text:
        p = {"title": args.title or "manual post", "url": "", "source": "manual",
             "cve_ids": [], "score": 100, "confidence": "operator",
             "post_text": args.text.strip(),
             "targets": [t.strip() for t in (args.targets or "").split(",") if t.strip()] or None}
        return {"decision": "post", "reason": "operator composed", "run_at": now_iso(),
                "min_score": 0}, [p], True

    # ---- operator-approved queue -----------------------------------------------
    if args.approve is not None:
        path = Path(args.approve) if args.approve else (STATE / "pending.json")
        if not path.exists():
            log(f"--approve: {path} not found (nothing waiting)")
            sys.exit(3)
        blob = json.loads(path.read_text())
        if isinstance(blob.get("posts"), list):                 # approved.json {posts:[...]}
            dec = {"decision": "post", "reason": "operator approved", "run_at": now_iso(),
                   "min_score": 0, "considered": blob.get("considered", [])}
            return dec, [p for p in blob["posts"] if isinstance(p, dict)], True
        if isinstance(blob.get("queue"), list):                 # new pending.json
            dec = {"decision": "post", "reason": "operator approved", "run_at": now_iso(),
                   "min_score": 0, "considered": blob.get("considered", [])}
            return dec, [p for p in blob["queue"] if isinstance(p, dict)], True
        inner = blob.get("decision", blob)                       # legacy single
        return inner, _posts_from(inner), True

    # ---- normal pipeline run -------------------------------------------------
    path = Path(args.decision)
    if not path.exists():
        log("no decision.json — analyst produced nothing")
        sys.exit(3)
    cand = STATE / "candidates.json"
    if cand.exists() and path.stat().st_mtime < cand.stat().st_mtime:
        log("decision.json older than candidates.json — stale, treating as skip")
        sys.exit(4)
    dec = json.loads(path.read_text())
    return dec, _posts_from(dec), False


# --------------------------------------------------------------------------- recording
def record_dry(posts: list[dict]) -> None:
    with (STATE / "dry_runs.jsonl").open("a") as f:
        for p in posts:
            f.write(json.dumps({"simulated_at": now_iso(), "title": p.get("title"),
                                "url": p.get("url"), "score": p.get("score")}) + "\n")


def record_real(p: dict, results: list[dict]) -> dict:
    ok = [r for r in results if r.get("ok")]
    now = now_iso()
    row = {
        "posted_at": now, "dry_run": False,
        "title": p.get("title"), "url": p.get("url"), "source": p.get("source"),
        "score": p.get("score"), "cve_ids": p.get("cve_ids", []),
        "targets": [r["provider"] for r in results],
        "results": results,
        "post_id": (ok[0]["id"] if ok else ""),
        "post_url": next((r["url"] for r in ok if r.get("url")), ""),
    }
    with (STATE / "history.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    if p.get("url"):
        with (STATE / "seen.jsonl").open("a") as f:
            f.write(json.dumps({"url": p.get("url"), "posted_at": now}) + "\n")
    return row


def reconcile_queue(posted_titles: set, posted_urls: set) -> None:
    """Drop just-posted items from pending.json; delete it if now empty."""
    (STATE / "approved.json").unlink(missing_ok=True)
    pj = STATE / "pending.json"
    if not pj.exists():
        return
    try:
        q = json.loads(pj.read_text())
    except (json.JSONDecodeError, OSError):
        return
    queue = q.get("queue")
    if not isinstance(queue, list):
        pj.unlink(missing_ok=True)
        return
    keep = [it for it in queue
            if not ((it.get("url") and it["url"] in posted_urls) or it.get("title") in posted_titles)]
    if keep:
        q["queue"] = keep
        pj.write_text(json.dumps(q, indent=2))
    else:
        pj.unlink(missing_ok=True)


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decision", default=str(STATE / "decision.json"))
    ap.add_argument("--force-post", action="store_true", help="ignore dry_run for this call")
    ap.add_argument("--approve", nargs="?", const="", metavar="PATH",
                    help="publish an operator-approved queue (default: state/pending.json)")
    ap.add_argument("--text", help="post this exact text now (operator one-off)")
    ap.add_argument("--title", help="title to record for --text")
    ap.add_argument("--targets", help="comma list of targets for --text (default: settings)")
    args = ap.parse_args()

    settings = load_settings()
    pub = settings.get("publish", {})
    mode = str(pub.get("mode", "auto")).lower()
    limit = int(pub.get("max_post_chars", 2900))
    min_score = None  # set per decision below
    max_day = int(pub.get("max_per_day", 5))
    sev = int(pub.get("severity_override_score", 90))
    runaway = int(pub.get("runaway_guard", 20))

    decision, posts, operator = load_decision(args)
    min_score = int(decision.get("min_score", settings.get("scoring", {}).get("min_score", 65)))
    archive_draft(decision, posts)

    if decision.get("decision") != "post" or not posts:
        log(f"decision=skip — {decision.get('reason')}")
        write_last_run(decision="skip", published=False, reason=decision.get("reason"))
        return 0

    C = creds()
    cfg_targets = publishers.resolve_targets(settings, C)
    ready = publishers.configured(C)

    # ---- classify every candidate post -----------------------------------------
    to_post: list[dict] = []
    held: list[dict] = []
    used = count_last_24h()
    would_post = 0

    for p in posts:
        text = (p.get("post_text") or "").strip()
        title = p.get("title") or "(untitled)"
        score = int(p.get("score", 0) or 0)
        if not text:
            log(f"skip “{title[:50]}” — empty post_text")
            continue
        if len(text) > limit:
            log(f"skip “{title[:50]}” — {len(text)} chars > {limit}")
            continue
        if not operator and score < min_score:
            log(f"skip “{title[:50]}” — score {score} < {min_score}")
            continue

        ql = lint_text(text, p.get("cve_ids") or [])
        if ql.get("warnings"):
            log(f"“{title[:40]}” quality nits: " + " | ".join(ql["warnings"][:4]))
        if ql.get("errors") and not operator:
            log(f"hold “{title[:40]}” — quality: " + " | ".join(ql["errors"][:4]))
            held.append({**p, "held_because": "quality", "min_score": min_score, "lint": ql})
            continue

        severe = score >= sev
        if not operator and not severe and (used + would_post) >= max_day:
            held.append({**p, "held_because": "cap", "min_score": min_score})
            continue
        if not operator and would_post >= runaway:
            log(f"runaway guard ({runaway}/run) — holding the rest for review")
            held.append({**p, "held_because": "runaway", "min_score": min_score})
            continue
        to_post.append(p)
        would_post += 1

    log(f"{len(posts)} candidate(s): {len(to_post)} to post, {len(held)} held "
        f"(cap {used}/{max_day}, severe overrides)")

    # ---- review mode: hold everything for a human -----------------------------
    if mode == "review" and not operator and to_post:
        held = to_post + held
        to_post = []
        for h in held:
            h.setdefault("held_because", "review")
            h.setdefault("min_score", min_score)

    if held:
        existing = []
        pj = STATE / "pending.json"
        if pj.exists():
            try:
                existing = json.loads(pj.read_text()).get("queue", [])
            except (json.JSONDecodeError, OSError):
                existing = []
        pj.write_text(json.dumps({
            "held_at": now_iso(), "manual": False,
            "queue": existing + held,
            "considered": decision.get("considered", []),
        }, indent=2))

    if not to_post:
        note = f"held {len(held)} post(s) for review" if held else "nothing cleared the bar"
        log(note)
        write_last_run(decision=("pending_review" if held else "skip"), published=False,
                       held=len(held), posted=0,
                       title=(held[0]["title"] if held else None),
                       score=(held[0].get("score") if held else None),
                       note=note, reason=(None if held else decision.get("reason")))
        return 0

    # ---- dry run -------------------------------------------------------------
    if is_dry_run(settings) and not args.force_post:
        log(f"DRY-RUN — {len(to_post)} post(s) simulated, nothing sent "
            f"(logged to state/dry_runs.jsonl)")
        record_dry(to_post)
        write_last_run(decision="post", published=False, dry_run=True, posted=0,
                       held=len(held), simulated=len(to_post),
                       title=to_post[0].get("title"), score=to_post[0].get("score"),
                       note=f"dry-run — {len(to_post)} post(s) not sent")
        return 0

    # ---- live -------------------------------------------------------------
    posted_rows: list[dict] = []
    failures: list[str] = []
    for p in to_post:
        only = p.get("targets") or None
        tset = publishers.resolve_targets(settings, C, only)
        missing = [t for t in tset if t not in ready]
        if missing:
            log(f"“{p.get('title','')[:40]}” — no creds for {missing}, skipping those")
            tset = [t for t in tset if t in ready]
        if not tset:
            failures.append(f"{p.get('title','')[:40]}: no configured target")
            continue
        log(f"posting “{p.get('title','')[:50]}” (score {p.get('score')}) → {', '.join(tset)}")
        results = publishers.post_all(p.get("post_text", ""), C, settings,
                                      short_text=p.get("short_text"), only=tset, log=log)
        for r in results:
            tag = "ok" if r["ok"] else f"FAIL {r['error']}"
            log(f"  {r['provider']}: {tag}  {r.get('url', '')}")
        if any(r["ok"] for r in results):
            posted_rows.append(record_real(p, results))
        else:
            failures.append(f"{p.get('title','')[:40]}: " + "; ".join(r["error"] for r in results)[:200])

    if posted_rows:
        reconcile_queue({r["title"] for r in posted_rows},
                        {r["url"] for r in posted_rows if r.get("url")})
        first = posted_rows[0]
        write_last_run(decision="post", published=True, dry_run=False,
                       posted=len(posted_rows), held=len(held), failed=len(failures),
                       title=first["title"], score=first["score"],
                       post_id=first["post_id"], post_url=first["post_url"],
                       targets=sorted({t for r in posted_rows for t in r["targets"]}),
                       note=(f"{len(failures)} failed" if failures else None))
        log(f"done — {len(posted_rows)} posted, {len(held)} held, {len(failures)} failed")
        return 0

    err = " | ".join(failures) or "no posts succeeded"
    log(f"POST FAILED: {err}")
    write_last_run(decision="post", published=False, error=err[:600],
                   title=to_post[0].get("title"))
    return 7


if __name__ == "__main__":
    sys.exit(main())
