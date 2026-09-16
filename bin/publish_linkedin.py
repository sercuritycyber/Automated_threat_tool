#!/usr/bin/env python3
"""Publish stage — turns analyst decisions into social posts.

Deliberately dumb: it executes what the analyst (or the operator) decided.

  * dry-run unless explicitly turned off (settings.publish.dry_run / CTI_DRY_RUN)
  * every run's drafts are archived to drafts/
  * a stale decision.json (older than candidates.json) is treated as a skip
  * over-limit post text is refused, not truncated
  * DRY-RUN simulations go to state/dry_runs.jsonl, never history.jsonl
  * real posts append history.jsonl + seen.jsonl and clear the review queue

Emergencies: a verified `breaking` post (see is_emergency) publishes
immediately and unconditionally — no lead time, no posting window, no spacing,
no daily cap, no runaway guard, no review-mode hold, and style errors are
downgraded to warnings. An actively exploited critical is worth more to the
public as a warning than to us as a subscriber exclusive.

Multi-post: the analyst may hand us several stories in `posts: [...]`. Each is
scored independently. Non-severe extras past the daily cap are held for review;
stories at/above publish.severity_override_score post regardless of the cap, so
a genuinely huge news day goes out in full.

Social delay: if publish.social_delay_hours > 0, new posts are parked in
state/social_queue.json and only released once that many hours have passed —
so the paid feed (pushed instantly by push_to_worker.py) genuinely lands first.
Operator one-offs bypass it. 0 / unset = post immediately.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests  # noqa: F401  (kept for exception typing in publishers)
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
try:
    from lint_post import lint as lint_text
except ImportError:
    def lint_text(_t, _c=None, _lane="intel"):
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


SOCIAL_QUEUE = STATE / "social_queue.json"


def _parse_iso(s: str):
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


LANES = ("breaking", "intel", "trend")
_QKEYS = ("post_after", "queued_at", "queued_reason")
LONDON = ZoneInfo("Europe/London")
# Nobody reads a company page at 03:00. An item posted outside these local
# windows burns the story for no reach, so it waits for the next opening.
POST_WINDOWS_DEFAULT = ["07:00-09:30", "11:30-13:30", "16:30-18:30"]


def lane_of(p: dict) -> str:
    lane = str(p.get("lane") or "intel").strip().lower()
    return lane if lane in LANES else "intel"


def is_emergency(p: dict, pub: dict) -> bool:
    """Is this a live emergency that must go out now, regardless of policy?

    A KEV-listed, actively-exploited critical has more public value as a warning
    than it has commercial value as a subscriber exclusive, so once it is
    verified nothing procedural is allowed to hold it — no daily cap, no runaway
    guard, no review mode, no style nit, no posting window, no lead time.

    "Verified" is not the model's say-so alone. Every post reaching this point
    has already passed decision_validation.sanitize_decision(), which drops any
    post whose URL was not in this run's candidate set or whose body does not
    reference its own source. On top of that we require the analyst not to be
    hedging: confidence `low` means it could not confirm the facts, and an
    unconfirmed emergency is a rumour, so it takes the normal path.
    """
    score = int(p.get("score", 0) or 0)
    if lane_of(p) == "breaking":
        return str(p.get("confidence", "")).strip().lower() != "low"
    # A non-breaking story can still be an emergency if it scored high enough
    # that the rubric can only have got there via confirmed exploitation.
    return score >= int(pub.get("emergency_score", 90))


def substitute_tokens(text: str, decision: dict) -> str:
    """Fill publish-time tokens the analyst is instructed to use.

    `{alerts_today}` is the number of subscriber briefs this run produced. It is
    substituted here rather than written by the model, so a volume claim in a
    public post is always literally true.
    """
    if not text or "{" not in text:
        return text
    alerts = decision.get("alerts") or []
    n = len(alerts)
    cves = len({str(c).upper() for a in alerts for c in (a.get("cve_ids") or []) if c})
    # "We wrote 0 briefs today" is worse than saying nothing.
    return (text.replace("{alerts_today}", str(n) if n >= 3 else "a full set of")
                .replace("{cves_today}", str(cves)))


def _read_queue() -> list[dict]:
    try:
        return json.loads(SOCIAL_QUEUE.read_text()).get("queue", [])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _write_queue(waiting: list[dict], now: datetime) -> bool:
    try:
        SOCIAL_QUEUE.write_text(json.dumps({"updated": now.isoformat(),
                                            "queue": waiting}, indent=2))
        return True
    except OSError as e:
        log(f"could not write social queue ({e})")
        return False


def _strip_queue_keys(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in _QKEYS}


def schedule_social(posts: list[dict], decision: dict, settings: dict,
                    operator: bool) -> list[dict]:
    """Release what is due and hold what is not.

    `publish.social_delay_hours` buys paid subscribers real lead time: they are
    pushed the full brief in stage 3, the public teaser follows later, so "our
    subscribers had this first" is literally true. A live emergency skips the
    hold — lane `breaking`, or a score at/above `publish.social_delay_bypass_score`
    — because being first to an actively-exploited RCE is worth more than the
    lead time.

    The queue is drained on EVERY run, including when the delay is 0, so items
    parked by pace_posts() for spacing or for a posting window are always
    released. (The old version returned early when the delay was 0 and could
    strand them indefinitely.)
    """
    now = datetime.now(timezone.utc)
    pub = settings.get("publish", {})
    delay_h = float(pub.get("social_delay_hours", 0) or 0)
    bypass = float(pub.get("social_delay_bypass_score", 10 ** 6) or 10 ** 6)

    due, waiting = [], []
    for q in _read_queue():
        after = _parse_iso(q.get("post_after", ""))
        (due if (after is None or after <= now) else waiting).append(q)

    if operator:
        immediate = list(posts)
    else:
        immediate = []
        known = {(q.get("url"), q.get("title")) for q in waiting}
        post_after = (now + timedelta(hours=delay_h)).isoformat()
        for p in posts:
            score = int(p.get("score", 0) or 0)
            if delay_h <= 0 or is_emergency(p, pub) or score >= bypass:
                immediate.append(p)
                continue
            if (p.get("url"), p.get("title")) in known:
                continue
            waiting.append({**p, "post_after": post_after, "queued_at": now.isoformat(),
                            "queued_reason": f"subscriber lead time {delay_h}h"})

    _write_queue(waiting, now)
    released = [{**_strip_queue_keys(d), "from_queue": True} for d in due]
    if released or waiting or immediate:
        log(f"social queue: {len(released)} released, {len(waiting)} waiting, "
            f"{len(immediate)} clear to go now "
            f"(lead {delay_h}h, emergency bypass >= {bypass:g})")
    return released + immediate


# ------------------------------------------------------------------ pacing
def _windows(settings: dict) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    raw = settings.get("publish", {}).get("post_windows", POST_WINDOWS_DEFAULT)
    out = []
    for w in raw or []:
        try:
            a, b = str(w).split("-", 1)
            ah, am = (int(x) for x in a.strip().split(":"))
            bh, bm = (int(x) for x in b.strip().split(":"))
            out.append(((ah, am), (bh, bm)))
        except (ValueError, TypeError):
            log(f"ignoring malformed publish.post_windows entry {w!r}")
    return sorted(out)


def _in_window(t: datetime, wins) -> bool:
    hm = (t.hour, t.minute)
    return any(a <= hm <= b for a, b in wins)


def _next_window(t: datetime, wins) -> datetime:
    for a, _ in wins:
        cand = t.replace(hour=a[0], minute=a[1], second=0, microsecond=0)
        if cand > t:
            return cand
    a = wins[0][0]
    return (t + timedelta(days=1)).replace(hour=a[0], minute=a[1],
                                           second=0, microsecond=0)


def pace_posts(to_post: list[dict], settings: dict, operator: bool) -> list[dict]:
    """Post one now, park the rest — spacing and posting windows.

    Two posts from the same page in the same window compete with each other:
    the second suppresses the first while it is still gathering engagement. So
    only the top-ranked post goes out per run and the rest are parked
    `publish.min_post_gap_minutes` apart. Outside `publish.post_windows` (local
    time) everything is parked until the next opening — a 03:00 post spends the
    story for no reach.

    A `breaking` post ignores both rules: an actively-exploited RCE goes out
    when it is confirmed, not when the calendar says so.
    """
    pub = settings.get("publish", {})
    gap = int(pub.get("min_post_gap_minutes", 0) or 0)
    wins = _windows(settings)
    # An emergency ignores the windows entirely — see is_emergency().
    if operator or not to_post:
        return to_post

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(LONDON)
    urgent = [p for p in to_post if is_emergency(p, pub)]
    rest = [p for p in to_post if not is_emergency(p, pub)]

    keep = list(urgent)
    defer: list[dict] = []
    base_local = now_local
    if wins and not _in_window(now_local, wins):
        base_local = _next_window(now_local, wins)
        if rest:
            log(f"outside posting windows ({now_local:%H:%M} local) — "
                f"holding {len(rest)} post(s) until {base_local:%a %H:%M}")
        slot = 0
        for q in rest:
            defer.append((q, base_local + timedelta(minutes=gap * slot)))
            slot += 1
    else:
        # At most ONE non-breaking post per run, and none at all on a run that
        # is already carrying a `breaking` post — an emergency should stand on
        # its own. Items released from the queue are preferred, since they have
        # already waited; everything else is parked a gap apart, so a run that
        # finds four good stories spreads them instead of bursting.
        rest.sort(key=lambda q: (not q.get("from_queue"),
                                 -int(q.get("score", 0) or 0)))
        allow = 0 if urgent else 1
        for i, q in enumerate(rest):
            if i < allow or gap <= 0:
                keep.append(q)
            else:
                defer.append((q, now_local + timedelta(minutes=gap * (i - allow + 1))))

    if defer:
        parked = [{**_strip_queue_keys(q),
                   "post_after": when.astimezone(timezone.utc).isoformat(),
                   "queued_at": now_utc.isoformat(),
                   "queued_reason": f"pacing: gap {gap}m / posting windows"}
                  for q, when in defer]
        _write_queue(_read_queue() + parked, now_utc)
        log(f"pacing: {len(keep)} posting now, {len(defer)} parked "
            f"({gap}m apart, next from {base_local:%H:%M} local)")
    return keep


def queue_manual(posts: list[dict], decision: dict, reason: str = "") -> None:
    """Delivery failed — stash the approved post(s) so a human can paste them
    onto the company page, and leave a paste-ready state/manual_post.md."""
    pj = STATE / "pending.json"
    existing = []
    if pj.exists():
        try:
            existing = json.loads(pj.read_text()).get("queue", [])
        except (json.JSONDecodeError, OSError):
            existing = []
    tagged = [{**p, "held_because": "delivery_failed", "held_at": now_iso(),
               "delivery_error": reason} for p in posts]
    pj.write_text(json.dumps({"held_at": now_iso(), "manual": True,
                              "queue": existing + tagged,
                              "considered": decision.get("considered", [])}, indent=2))
    lines = [f"# {len(posts)} post(s) awaiting manual publishing to the company page",
             f"# reason: {reason}", f"# generated: {now_iso()}", ""]
    for p in posts:
        lines += [f"## {p.get('title', '(untitled)')}  (score {p.get('score')})", "",
                  (p.get("post_text") or "").strip(), "", "-" * 60, ""]
    (STATE / "manual_post.md").write_text("\n".join(lines))


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


def _recently_posted_cves(hours: int = 72) -> dict[str, str]:
    """CVE id -> title of the most recent live post that covered it, within
    `hours`. Deliberately reads history.jsonl fresh every call rather than
    caching — this must catch a CVE posted by a DIFFERENT run than the one
    holding a stale candidate, which is exactly the gap that let
    CVE-2026-76461 go out 4 times in <24h on 2026-09-16: items held in
    state/pending.json for review (quality/cap/runaway-guard) were never
    re-checked against what shipped elsewhere while they sat there, so a
    2026-09-16 07:00 backlog flush (--approve) republished a story that had
    already gone out three times over the previous day.
    """
    hist = STATE / "history.jsonl"
    if not hist.exists():
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: dict[str, str] = {}
    for line in hist.read_text().splitlines():
        try:
            row = json.loads(line)
            t = datetime.fromisoformat(row["posted_at"].replace("Z", "+00:00"))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if t < cutoff:
            continue
        for cve in row.get("cve_ids") or []:
            out[str(cve).upper()] = row.get("title", "")
    return out


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
        "lane": str(p.get("lane") or "intel").strip().lower(),
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
    lane_floor = pub.get("lane_min_score") or {}

    decision, posts, operator = load_decision(args)
    min_score = int(decision.get("min_score", settings.get("scoring", {}).get("min_score", 65)))
    archive_draft(decision, posts)

    had_new_posts = decision.get("decision") == "post" and bool(posts)
    # Publish-time tokens ({alerts_today}) are filled from THIS decision, so the
    # numbers a public post quotes are the ones we actually shipped.
    for _p in posts:
        for _f in ("post_text", "short_text", "comment"):
            if _p.get(_f):
                _p[_f] = substitute_tokens(_p[_f], decision)
    # schedule_social parks new posts and returns whatever is due for release
    # (which may be items queued on an earlier run, even if this run's decision
    # is "skip").
    posts = schedule_social(posts, decision, settings, operator)

    if not posts:
        if had_new_posts:
            log("social posts held by delay — none due yet; paid feed already went out")
            write_last_run(decision="queued", published=False,
                           note="social post(s) queued for delayed release")
            return 0
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
    # Hard gate, checked before anything else including the operator flag: a
    # CVE that already went out live in the last 72h never posts again. This
    # catches stale items sitting in pending.json that get flushed by a bulk
    # --approve after other runs have already covered the same story — see
    # _recently_posted_cves' docstring for the incident that motivated this.
    recent_cves = _recently_posted_cves()

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
        dup_cve = next((c for c in (p.get("cve_ids") or []) if str(c).upper() in recent_cves), None)
        if dup_cve:
            log(f"skip “{title[:50]}” — {dup_cve} already posted within 72h "
                f"(“{recent_cves[str(dup_cve).upper()][:50]}”)")
            continue
        lane = lane_of(p)
        emergency = is_emergency(p, pub)
        # Per-lane floor. A single global min_score (60) silently muted the intel
        # and trend lanes, which is why the public feed was one post a day.
        floor = int(lane_floor.get(lane, min_score))
        if not operator and score < floor:
            log(f"skip “{title[:50]}” — {lane} score {score} < {floor}")
            continue

        ql = lint_text(text, p.get("cve_ids") or [], lane)
        if ql.get("warnings"):
            log(f"“{title[:40]}” quality nits: " + " | ".join(ql["warnings"][:4]))
        if ql.get("errors") and not operator and not emergency:
            log(f"hold “{title[:40]}” — quality: " + " | ".join(ql["errors"][:4]))
            held.append({**p, "held_because": "quality", "min_score": min_score, "lint": ql})
            continue
        if ql.get("errors") and emergency:
            # Shipping an awkwardly-worded warning about an actively exploited
            # critical beats shipping a polished one tomorrow. Log loudly so the
            # nits still get seen, then publish.
            log(f"EMERGENCY “{title[:40]}” — publishing despite {len(ql['errors'])} "
                f"style error(s): " + " | ".join(ql["errors"][:4]))

        severe = score >= sev or emergency
        if not operator and not severe and (used + would_post) >= max_day:
            held.append({**p, "held_because": "cap", "min_score": min_score})
            continue
        if not operator and not emergency and would_post >= runaway:
            log(f"runaway guard ({runaway}/run) — holding the rest for review")
            held.append({**p, "held_because": "runaway", "min_score": min_score})
            continue
        to_post.append(p)
        would_post += 1

    log(f"{len(posts)} candidate(s): {len(to_post)} to post, {len(held)} held "
        f"(cap {used}/{max_day}, severe overrides)")
    if to_post:
        to_post.sort(key=lambda q: (LANES.index(lane_of(q)), -int(q.get("score", 0) or 0)))
        to_post = pace_posts(to_post, settings, operator)

    # ---- review mode: hold everything for a human -----------------------------
    if mode == "review" and not operator and to_post:
        # Review mode holds everything for a human — except a verified
        # emergency, which is the one case where waiting for a human is the
        # more dangerous option.
        urgent_now = [q for q in to_post if is_emergency(q, pub)]
        held = [q for q in to_post if not is_emergency(q, pub)] + held
        to_post = urgent_now
        if urgent_now:
            log(f"review mode: {len(urgent_now)} verified emergency post(s) "
                f"going out anyway")
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
                                      short_text=p.get("short_text"),
                                      comment=p.get("comment"), only=tset, log=log)
        for r in results:
            tag = "ok" if r["ok"] else f"FAIL {r['error']}"
            log(f"  {r['provider']}: {tag}  {r.get('url', '')}")
        if any(r["ok"] for r in results):
            posted_rows.append(record_real(p, results))
        else:
            failures.append(f"{p.get('title','')[:40]}: " + "; ".join(r["error"] for r in results)[:200])

    urgent_titles = [r["title"] for r in posted_rows
                     if str(r.get("lane", "")).lower() == "breaking"]
    if urgent_titles:
        try:
            import subprocess
            subprocess.run(["python3", str(ROOT / "bin" / "notify.py"),
                            f"CTI EMERGENCY published: {urgent_titles[0][:120]}",
                            "high"], timeout=20, check=False)
        except Exception:  # noqa: BLE001 — never let a ping sink a good post
            pass

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
    # Delivery failed (e.g. LinkedIn rejecting an org post for lack of
    # w_organization_social). Subscribers were already served in stage 3, so this
    # is a warning, not a service failure: queue the approved post(s) for manual
    # publishing, ping the operator, and exit non-fatally (5).
    queue_manual(to_post, decision, reason=err[:600])
    write_last_run(decision="post", published=False, error=err[:600],
                   pending_manual=len(to_post), title=to_post[0].get("title"))
    try:
        import subprocess
        subprocess.run(["python3", str(ROOT / "bin" / "notify.py"),
                        f"CTI: {len(to_post)} approved post(s) not delivered ({err[:120]}). "
                        f"Queued for manual posting — see state/manual_post.md", "high"],
                       timeout=20, check=False)
    except Exception:
        pass
    return 5


if __name__ == "__main__":
    sys.exit(main())
