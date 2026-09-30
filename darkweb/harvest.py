#!/usr/bin/env python3
"""Onion-address aggregator — CLI entry point.

Crawls onion INDEX/SEARCH sources (not the forums themselves), extracts and
aggregates v3 .onion addresses, and validates them over Tor, producing a
maintained LIST of valid addresses for forums like BreachForums / XSS / Dread
for a human to investigate. It does not scrape or store forum content.

CSAM is blocked, fail-closed, at every stage and cannot be disabled: any address
on the blocklist or whose context/title trips the filter is dropped and never
stored or shown. If the safety filter cannot be built, the tool refuses to run.

Subcommands:
    check                     confirm Tor is reachable and exiting
    crawl                     harvest addresses from the configured sources
    checkup [--limit N] [--stale H]   liveness-check discovered addresses over Tor
    list [--tag X] [--up] [--json F]  show / export the aggregated address list
    run                       crawl + checkup + list (the timer path)
    stats                     DB counts

Config: darkweb/sources.yaml (copy sources.yaml.example).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import discover  # noqa: E402
import parse  # noqa: E402
import safety as safetymod  # noqa: E402
import torfetch  # noqa: E402
from store import Store  # noqa: E402

try:
    import yaml
except Exception:
    print("PyYAML is required (pip install PyYAML)", file=sys.stderr)
    raise

DEFAULT_SOURCES = HERE / "sources.yaml"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _settings(cfg: dict) -> dict:
    s = dict(cfg.get("darkweb") or {})
    s.setdefault("proxy_host", torfetch.DEFAULT_PROXY_HOST)
    s.setdefault("proxy_port", torfetch.DEFAULT_PROXY_PORT)
    s.setdefault("db", str(HERE.parent / "state" / "onions.db"))
    s.setdefault("timeout", 90)
    s.setdefault("max_bytes", 3_000_000)
    s.setdefault("rate_seconds", 8)
    s.setdefault("crawl_depth", 1)
    s.setdefault("max_pages_per_source", 25)
    s.setdefault("checkup_limit", 60)
    return s


def _resolve(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    return p if p.is_absolute() else (HERE.parent / p)


def _make_safety(cfg: dict) -> safetymod.SafetyFilter:
    """Build the CSAM filter or abort — the tool never runs without it."""
    try:
        return safetymod.load(cfg.get("safety"), HERE)
    except Exception as e:
        print(f"FATAL: CSAM safety filter failed to load ({e}); refusing to run",
              file=sys.stderr)
        sys.exit(4)


@contextmanager
def lock(db_path: Path):
    lk = db_path.with_suffix(db_path.suffix + ".lock")
    lk.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lk), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = time.time() - lk.stat().st_mtime
        if age < 3600:
            print(f"another run holds {lk} (age {age:.0f}s); exiting", file=sys.stderr)
            sys.exit(3)
        lk.unlink(missing_ok=True)
        fd = os.open(str(lk), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lk.unlink(missing_ok=True)


def _fetch(url, cfg, **kw):
    return torfetch.fetch(
        url, proxy_host=cfg["proxy_host"], proxy_port=int(cfg["proxy_port"]),
        timeout=float(cfg["timeout"]), max_bytes=int(cfg["max_bytes"]), **kw)


# --------------------------------------------------------------------------- #
def cmd_check(args) -> int:
    cfg = _settings(_load_yaml(Path(args.sources)))
    try:
        print(f"Tor OK via {cfg['proxy_host']}:{cfg['proxy_port']}")
        print(torfetch.check_tor(cfg["proxy_host"], int(cfg["proxy_port"])))
        return 0
    except Exception as e:
        print(f"Tor check FAILED: {e}", file=sys.stderr)
        return 1


def cmd_crawl(args) -> int:
    cfg_all = _load_yaml(Path(args.sources))
    cfg = _settings(cfg_all)
    safety = _make_safety(cfg_all)          # fail-closed: aborts if unavailable
    targets = [str(t) for t in (cfg_all.get("targets") or [])]
    sources = [s for s in (cfg_all.get("sources") or []) if s.get("enabled", True)]
    if not sources:
        print(f"no enabled sources in {args.sources} — copy sources.yaml.example",
              file=sys.stderr)
        return 2
    db_path = _resolve(cfg["db"])
    found = new = blocked = pages = 0
    with lock(db_path), Store(db_path) as store:
        for src in sources:
            for name, seed in discover.expand_source_urls(src, targets):
                depth = int(src.get("crawl_depth", cfg["crawl_depth"]))
                seen_urls: set[str] = set()
                q: deque = deque([(seed, 0)])
                host = urlsplit(seed).hostname or ""
                src_pages = 0
                while q and src_pages < int(cfg["max_pages_per_source"]):
                    url, d = q.popleft()
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    try:
                        # all traffic rides Tor; clearnet index hosts are allowed
                        resp = _fetch(url, cfg, allow_clearnet=True)
                    except (torfetch.TorError, torfetch.FetchRefused) as e:
                        print(f"[{name}] {url} -> {e}", file=sys.stderr)
                        continue
                    src_pages += 1
                    pages += 1
                    # extract addresses + context from this index page
                    for s in discover.extract(resp.text, name, targets):
                        found += 1
                        if not safety.allow(s.address, s.context, s.label):
                            store.mark_blocked(s.address)
                            blocked += 1
                            continue
                        if store.is_blocked(s.address):
                            continue
                        if store.add_sighting(s.address, s.source, s.label,
                                              s.context, s.tags):
                            new += 1
                    # bounded crawl: follow same-host index links only
                    if d < depth:
                        for link in parse.extract_links(resp.text, url, same_host_only=True):
                            if urlsplit(link["url"]).hostname == host and link["url"] not in seen_urls:
                                q.append((link["url"], d + 1))
                    time.sleep(float(cfg["rate_seconds"]))
    print(f"crawl: {pages} index pages, {found} address sightings, "
          f"{new} new addresses, {blocked} CSAM-blocked")
    return 0


def cmd_checkup(args) -> int:
    cfg_all = _load_yaml(Path(args.sources))
    cfg = _settings(cfg_all)
    safety = _make_safety(cfg_all)          # fail-closed
    db_path = _resolve(cfg["db"])
    stale = args.stale * 3600 if args.stale else None
    limit = args.limit or int(cfg["checkup_limit"])
    up = down = blocked = 0
    with Store(db_path) as store:
        addrs = store.addresses_to_check(stale_seconds=stale, limit=limit)
        for addr in addrs:
            if safety.address_blocked(addr):   # blocklist can grow between runs
                store.mark_blocked(addr)
                blocked += 1
                continue
            url = f"http://{addr}/"
            try:
                resp = _fetch(url, cfg)
            except (torfetch.TorError, torfetch.FetchRefused):
                store.set_liveness(addr, status="down", http_status=None, title=None)
                down += 1
                continue
            # classify BEFORE storing anything from inside the service
            try:
                title = parse.title(resp.text)
                flagged = safety.text_flagged(title) or safety.text_flagged(resp.text[:20000])
            except Exception:
                # fail-closed: if we can't classify, store no title/content
                store.set_liveness(addr, status="up", http_status=resp.status, title=None)
                up += 1
                continue
            if flagged:
                store.mark_blocked(addr)
                blocked += 1
                continue
            store.set_liveness(addr, status="up", http_status=resp.status,
                               title=title or None)
            up += 1
            time.sleep(float(cfg["rate_seconds"]))
    print(f"checkup: {len(addrs)} checked, {up} up, {down} down, {blocked} CSAM-blocked")
    return 0


def cmd_add(args) -> int:
    """Manually track an onion you found yourself (your workflow: you discover,
    the tool validates + tracks). Stores it, then liveness-checks it now."""
    cfg_all = _load_yaml(Path(args.sources))
    cfg = _settings(cfg_all)
    safety = _make_safety(cfg_all)          # fail-closed
    m = discover.ONION_RE.search(args.address.strip())
    if not m:
        print("no valid v3 .onion address found in input", file=sys.stderr)
        return 2
    addr = m.group(1).lower()
    if safety.address_blocked(addr):
        print("address is on the CSAM blocklist — refused")
        return 5
    db_path = _resolve(cfg["db"])
    with lock(db_path), Store(db_path) as store:
        if store.is_blocked(addr):
            print("address was previously CSAM-blocked — refused")
            return 5
        tags = [args.tag] if args.tag else []
        note = args.note or "manually added"
        new = store.add_sighting(addr, "manual", note, note, tags)
        print(f"{'added' if new else 'already tracked'}: {addr}"
              + (f" [{args.tag}]" if args.tag else ""))
        if args.no_check:
            return 0
        try:
            resp = _fetch(f"http://{addr}/", cfg)
        except (torfetch.TorError, torfetch.FetchRefused) as ex:
            store.set_liveness(addr, status="down", http_status=None, title=None)
            print(f"DOWN — {ex}")
            return 0
        try:
            title = parse.title(resp.text)
            flagged = safety.text_flagged(title) or safety.text_flagged(resp.text[:20000])
        except Exception:
            store.set_liveness(addr, status="up", http_status=resp.status, title=None)
            print(f"UP  {resp.status}  (unclassified — no title stored)")
            return 0
        if flagged:
            store.mark_blocked(addr)
            print("CSAM-flagged on fetch — blocked; nothing stored")
            return 5
        store.set_liveness(addr, status="up", http_status=resp.status, title=title or None)
        print(f"UP  {resp.status}  {title or ''}")
    return 0


def cmd_list(args) -> int:
    cfg = _settings(_load_yaml(Path(args.sources)))
    db_path = _resolve(cfg["db"])
    status = "up" if args.up else None
    rows = []
    with Store(db_path) as store:
        for r in store.listing(tag=args.tag or None, status=status):
            rows.append({
                "address": r["address"], "status": r["status"],
                "http_status": r["http_status"], "title": r["title"],
                "tags": r["tags"], "sources": r["sources"],
                "n_sightings": r["n_sightings"],
                "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                "last_checked": r["last_checked"],
            })
    if args.json:
        out = _resolve(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(r) for r in rows))
        print(f"wrote {len(rows)} addresses -> {out}")
    if not rows:
        print("no addresses yet — run crawl")
        return 0
    for r in rows:
        st = {"up": "UP  ", "down": "down", "unknown": "?   "}.get(r["status"], r["status"])
        tags = f" [{r['tags']}]" if r["tags"] else ""
        title = f"  {r['title']}" if r["title"] else ""
        print(f"{st} {r['address']}{tags}{title}")
    print(f"\n{len(rows)} addresses"
          + (f" (tag={args.tag})" if args.tag else "")
          + (" (up only)" if args.up else ""))
    return 0


def cmd_run(args) -> int:
    rc = cmd_crawl(args)
    cmd_checkup(args)
    cmd_list(args)
    return rc


def cmd_stats(args) -> int:
    cfg = _settings(_load_yaml(Path(args.sources)))
    with Store(_resolve(cfg["db"])) as store:
        for k, v in store.stats().items():
            print(f"{k:16} {v}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sources", default=str(DEFAULT_SOURCES))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("crawl").set_defaults(func=cmd_crawl)

    a = sub.add_parser("add", help="manually track an onion you found yourself")
    a.add_argument("address", help="a v3 .onion address or URL")
    a.add_argument("--tag", default="", help="label it (e.g. the site name)")
    a.add_argument("--note", default="", help="free-text note")
    a.add_argument("--no-check", action="store_true", help="store without liveness-checking now")
    a.set_defaults(func=cmd_add)

    c = sub.add_parser("checkup")
    c.add_argument("--limit", type=int, default=0)
    c.add_argument("--stale", type=float, default=0, help="only re-check if older than N hours")
    c.set_defaults(func=cmd_checkup)

    l = sub.add_parser("list")
    l.add_argument("--tag", default="")
    l.add_argument("--up", action="store_true", help="only live addresses")
    l.add_argument("--json")
    l.set_defaults(func=cmd_list)

    rn = sub.add_parser("run")
    rn.add_argument("--tag", default="")
    rn.add_argument("--up", action="store_true")
    rn.add_argument("--json")
    rn.add_argument("--limit", type=int, default=0)
    rn.add_argument("--stale", type=float, default=0)
    rn.set_defaults(func=cmd_run)

    sub.add_parser("stats").set_defaults(func=cmd_stats)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
