#!/usr/bin/env python3
"""Dark-web collector + matcher — CLI entry point.

Targeted collection of onion forum pages you have configured (never a crawler),
over Tor, into a local SQLite DB, cross-referenced against a watchlist and your
honeypot IOCs so honeypot telemetry can be tied to market/forum activity.

Subcommands:

    check                     confirm Tor is reachable and exiting
    collect                   fetch every enabled page in the site registry
    match                     scan stored snapshots for watchlist/IOC hits
    report [--since HOURS]    print hits (and write JSONL) for investigation
    run                       collect, then match, then report (the timer path)
    stats                     DB counts

Config:
    darkweb/sites.yaml        the registry (copy sites.yaml.example)
    darkweb/watchlist.yaml    terms/domains/handles to watch
    settings under `darkweb:` in the registry file (proxy, db path, IOC feeds)

Nothing here posts, publishes, or touches the CTI pipeline. It is an
investigation tool.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import match as matchmod  # noqa: E402
import parse  # noqa: E402
import torfetch  # noqa: E402
from store import Store  # noqa: E402

try:
    import yaml
except Exception:
    print("PyYAML is required (pip install PyYAML)", file=sys.stderr)
    raise

DEFAULT_SITES = HERE / "sites.yaml"
DEFAULT_WATCHLIST = HERE / "watchlist.yaml"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


@contextmanager
def lock(db_path: Path):
    """A simple exclusive lock so two collects don't run at once."""
    lk = db_path.with_suffix(db_path.suffix + ".lock")
    lk.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lk), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        age = time.time() - lk.stat().st_mtime
        if age < 3600:
            print(f"another collect holds {lk} (age {age:.0f}s); exiting",
                  file=sys.stderr)
            sys.exit(3)
        print(f"stale lock {lk} ({age:.0f}s) — taking over", file=sys.stderr)
        lk.unlink(missing_ok=True)
        fd = os.open(str(lk), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lk.unlink(missing_ok=True)


def _settings(sites_cfg: dict) -> dict:
    s = dict(sites_cfg.get("darkweb") or {})
    s.setdefault("proxy_host", torfetch.DEFAULT_PROXY_HOST)
    s.setdefault("proxy_port", torfetch.DEFAULT_PROXY_PORT)
    s.setdefault("db", str(HERE.parent / "state" / "darkweb.db"))
    s.setdefault("timeout", 60)
    s.setdefault("max_bytes", 4_000_000)
    s.setdefault("default_rate_seconds", 15)
    s.setdefault("ioc_feeds", [])
    return s


def _resolve(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    return p if p.is_absolute() else (HERE.parent / p)


# --------------------------------------------------------------------------- #
def cmd_check(args) -> int:
    cfg = _settings(_load_yaml(Path(args.sites)))
    try:
        note = torfetch.check_tor(cfg["proxy_host"], int(cfg["proxy_port"]))
        print(f"Tor OK via {cfg['proxy_host']}:{cfg['proxy_port']}")
        print(note)
        return 0
    except Exception as e:
        print(f"Tor check FAILED: {e}", file=sys.stderr)
        print("Is the Tor daemon running? (systemctl status tor / `tor` on :9050)",
              file=sys.stderr)
        return 1


def cmd_collect(args) -> int:
    sites_cfg = _load_yaml(Path(args.sites))
    cfg = _settings(sites_cfg)
    sites = sites_cfg.get("sites") or []
    if not sites:
        print(f"no sites configured in {args.sites} — copy sites.yaml.example "
              "and add onion addresses you have verified", file=sys.stderr)
        return 2
    db_path = _resolve(cfg["db"])
    fetched = new = errors = 0
    with lock(db_path), Store(db_path) as store:
        for site in sites:
            if not site.get("enabled", True):
                continue
            name = site.get("name") or site.get("base", "?")
            base = site.get("base", "").rstrip("/")
            if not base:
                print(f"[{name}] no base url, skipping", file=sys.stderr)
                continue
            cookie = None
            if site.get("cookie_env"):
                cookie = os.environ.get(site["cookie_env"]) or None
                if not cookie:
                    print(f"[{name}] cookie_env {site['cookie_env']} unset — "
                          "authenticated pages will show the login wall")
            rate = float(site.get("rate_seconds", cfg["default_rate_seconds"]))
            paths = site.get("paths") or ["/"]
            for i, path in enumerate(paths):
                url = base + (path if path.startswith("/") else "/" + path) \
                    if not path.startswith("http") else path
                if i:
                    time.sleep(rate)
                try:
                    resp = torfetch.fetch(
                        url,
                        proxy_host=cfg["proxy_host"], proxy_port=int(cfg["proxy_port"]),
                        timeout=float(cfg["timeout"]), max_bytes=int(cfg["max_bytes"]),
                        cookie=cookie,
                    )
                except (torfetch.TorError, torfetch.FetchRefused) as e:
                    errors += 1
                    print(f"[{name}] {url} -> {e}", file=sys.stderr)
                    continue
                fetched += 1
                parsed = parse.parse_page(resp.text, resp.final_url or url)
                _pid, _sid, is_new = store.record_fetch(
                    name, url, title=parsed["title"], status=resp.status,
                    content_type=resp.content_type, bytes_len=resp.bytes_len,
                    text=parsed["text"],
                )
                new += int(is_new)
                flag = "NEW" if is_new else "same"
                print(f"[{name}] {resp.status} {flag} {len(parsed['text'])}c "
                      f"{len(parsed['links'])}links {url}")
    print(f"collect: {fetched} fetched, {new} new snapshots, {errors} errors")
    return 0 if errors == 0 else 0  # errors are per-page, not fatal


def _build_matcher(cfg: dict, watchlist_path: Path) -> matchmod.Matcher:
    indicators: list = []
    if watchlist_path.exists():
        indicators += matchmod.load_watchlist(watchlist_path)
    for feed in cfg.get("ioc_feeds", []):
        p = _resolve(feed["path"]) if not str(feed["path"]).startswith("/") \
            else Path(feed["path"])
        kind = feed.get("kind")
        if not p.exists():
            print(f"IOC feed missing: {p}", file=sys.stderr)
            continue
        if kind == "ips":
            indicators += matchmod.load_honeypot_ips(p, feed.get("column", "src_ip"))
        elif kind == "creds":
            indicators += matchmod.load_honeypot_creds(
                p, feed.get("user_column", "username"),
                feed.get("pass_column", "password"))
        elif kind == "hashes":
            indicators += matchmod.load_honeypot_hashes(p, feed.get("column", "sha256"))
        else:
            print(f"unknown IOC feed kind {kind!r} for {p}", file=sys.stderr)
    return matchmod.Matcher(indicators)


def cmd_match(args) -> int:
    sites_cfg = _load_yaml(Path(args.sites))
    cfg = _settings(sites_cfg)
    matcher = _build_matcher(cfg, Path(args.watchlist))
    if not matcher.indicators:
        print("no indicators loaded (empty watchlist and no IOC feeds)",
              file=sys.stderr)
        return 2
    db_path = _resolve(cfg["db"])
    found = 0
    with Store(db_path) as store:
        snaps = (store.iter_snapshots() if args.all
                 else store.latest_snapshots())
        for snap in snaps:
            for hit in matcher.scan(snap["text"]):
                if store.add_hit(snap["id"], snap["page_id"], hit["kind"],
                                 hit["indicator"], hit["context"]):
                    found += 1
    print(f"match: {found} new hits ({len(matcher.indicators)} indicators)")
    return 0


def cmd_report(args) -> int:
    sites_cfg = _load_yaml(Path(args.sites))
    cfg = _settings(sites_cfg)
    db_path = _resolve(cfg["db"])
    since = time.time() - args.since * 3600 if args.since else None
    rows = []
    with Store(db_path) as store:
        for h in store.hits(unreviewed_only=args.unreviewed, since=since):
            rows.append({
                "found_at": h["found_at"], "site": h["site"], "url": h["url"],
                "kind": h["kind"], "indicator": h["indicator"],
                "context": h["context"],
            })
    if args.json:
        out = _resolve(args.json) if not args.json.startswith("/") else Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(r) for r in rows))
        print(f"wrote {len(rows)} hits -> {out}")
    if not rows:
        print("no hits" + (f" in the last {args.since}h" if args.since else ""))
        return 0
    for r in rows:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["found_at"]))
        print(f"\n[{ts}] {r['kind']}: {r['indicator']}")
        print(f"  {r['site']}  {r['url']}")
        print(f"  …{r['context']}…")
    print(f"\n{len(rows)} hits")
    return 0


def cmd_run(args) -> int:
    rc = cmd_collect(args)
    cmd_match(args)
    cmd_report(args)
    return rc


def cmd_stats(args) -> int:
    cfg = _settings(_load_yaml(Path(args.sites)))
    with Store(_resolve(cfg["db"])) as store:
        for k, v in store.stats().items():
            print(f"{k:16} {v}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sites", default=str(DEFAULT_SITES))
    ap.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("collect").set_defaults(func=cmd_collect)

    m = sub.add_parser("match")
    m.add_argument("--all", action="store_true",
                   help="scan every snapshot, not just the latest per page")
    m.set_defaults(func=cmd_match)

    r = sub.add_parser("report")
    r.add_argument("--since", type=float, default=0, help="only hits from the last N hours")
    r.add_argument("--unreviewed", action="store_true")
    r.add_argument("--json", help="also write hits as JSONL to this path")
    r.set_defaults(func=cmd_report)

    rn = sub.add_parser("run")
    rn.add_argument("--all", action="store_true")
    rn.add_argument("--since", type=float, default=0)
    rn.add_argument("--unreviewed", action="store_true")
    rn.add_argument("--json")
    rn.set_defaults(func=cmd_run)

    sub.add_parser("stats").set_defaults(func=cmd_stats)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
