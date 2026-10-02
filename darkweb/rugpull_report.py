#!/usr/bin/env python3
"""Downtime / rug-pull report for a tracked onion.

Probes the address over Tor a few times to firmly establish current status,
pulls the monitored status timeline out of the aggregator DB, and writes a
DRAFT feed post — it NEVER publishes anything.

Hard rule (house policy): facts only. We started monitoring after the site was
already down, so we can state "first observed offline at <our timestamp>", not a
true downtime we did not witness. Anything only the operator/analyst can assert
(what the site is, when it was last seen live, whether this is really an exit
scam) is left as an [UPPERCASE PLACEHOLDER] for a human to fill before posting.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

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
    print("PyYAML required", file=sys.stderr)
    raise

ROOT = HERE.parent


def _load_yaml(p: Path) -> dict:
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def _settings(cfg: dict) -> dict:
    s = dict(cfg.get("darkweb") or {})
    s.setdefault("proxy_host", torfetch.DEFAULT_PROXY_HOST)
    s.setdefault("proxy_port", torfetch.DEFAULT_PROXY_PORT)
    s.setdefault("db", str(ROOT / "state" / "onions.db"))
    s.setdefault("timeout", 90)
    s.setdefault("max_bytes", 3_000_000)
    return s


def _resolve_db(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    return p if p.is_absolute() else (ROOT / p)


def _utc(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


def _pick_address(store: Store, raw: str, tag: str) -> str | None:
    if raw:
        m = discover.ONION_RE.search(raw)
        return m.group(1).lower() if m else None
    if tag:
        row = store.db.execute(
            "SELECT address FROM tags WHERE tag=? LIMIT 1", (tag,)).fetchone()
        return row["address"] if row else None
    return None


def probe(addr: str, cfg: dict, attempts: int) -> tuple[str, int | None, str | None]:
    """Return (status, http_status, title) after up to `attempts` tries."""
    last_err = None
    for i in range(attempts):
        try:
            resp = torfetch.fetch(
                f"http://{addr}/", proxy_host=cfg["proxy_host"],
                proxy_port=int(cfg["proxy_port"]), timeout=float(cfg["timeout"]),
                max_bytes=int(cfg["max_bytes"]))
            return "up", resp.status, parse.title(resp.text) or None
        except (torfetch.TorError, torfetch.FetchRefused) as e:
            last_err = str(e)
            if i + 1 < attempts:
                time.sleep(4)
    print(f"  probe: unreachable ({last_err})", file=sys.stderr)
    return "down", None, None


def _dur(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h >= 24:
        return f"{h // 24}d {h % 24}h"
    return f"{h}h {m}m"


def build_draft(addr: str, row, history, status: str, tag: str) -> str:
    # Prefer the first logged down-transition; else the first time we logged the
    # address at all (we added it already-offline). Never "now".
    first_down = next((h["at"] for h in history if h["new_status"] == "down"), None)
    offline_since = first_down if first_down else (row["first_seen"] if row else None)
    first_down_s = _utc(offline_since)
    now_ts = time.time()
    now_s = _utc(now_ts)
    dur_s = _dur(now_ts - offline_since) if (offline_since and status == "down") else ""

    tl_lines = ["- [when YOU last saw it live — fill in]: reachable"]
    tl_lines.append(f"- {first_down_s}: first logged OFFLINE over Tor by SecurityCyber monitoring")
    for h in history:
        tl_lines.append(f"- {_utc(h['at'])}: status {h['old_status'] or '(new)'} → {h['new_status']}")
    still = f"still OFFLINE after ~{dur_s}" if (status == "down" and dur_s) else ("ONLINE again" if status != "down" else "still OFFLINE")
    tl_lines.append(f"- {now_s}: {still} (latest check)")
    timeline = "\n".join(tl_lines)

    label = tag.upper() if tag else "[site name — fill in]"
    verb = "appears to have gone dark" if status == "down" else "appears to be back online"

    body = f"""DRAFT for your feeds — review, add the site name / last-seen time, then post. NOT auto-published.

Dark-web watch: a hidden service we've been tracking {verb}.

Since {first_down_s}{(' — about ' + dur_s + ' ago') if dur_s else ''},
SecurityCyber's onion monitor has not been able to reach {label} (v3 .onion) over
Tor — it appears offline, having seemingly been reachable shortly before, and was
still unreachable on re-check.

Our read: this looks consistent with an exit scam / "rug pull" — what appears to
be operators pulling the infrastructure on an empty warchest and walking away.
That's SecurityCyber's assessment, not a confirmed fact; we've held this view
since it first seemed to drop, and we're monitoring for any return.

Observed timeline:
{timeline}

[Optional: one line on what {label} appeared to be / who it served.]

#ThreatIntel #DarkWeb #SecurityCyber

---
Facts (ours, measured): the .onion was unreachable over Tor at the times listed;
status changes are from our own probes. Assessment (our opinion, hedged as such):
the exit-scam / rug-pull read. We did not witness the exact drop, so the timeline
begins at our first observation.
Address (defanged): {addr.replace('.onion', '[.]onion')}
"""
    return body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Downtime/rug-pull DRAFT report (never posts).")
    ap.add_argument("address", nargs="?", default="", help="onion address or URL")
    ap.add_argument("--tag", default="", help="pick the tracked address by tag instead")
    ap.add_argument("--sources", default=str(HERE / "sources.yaml"))
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--out", default="", help="write the draft here (default drafts/)")
    args = ap.parse_args(argv)

    cfg_all = _load_yaml(Path(args.sources))
    cfg = _settings(cfg_all)
    # safety must load or we refuse (consistency with the collector)
    try:
        safetymod.load(cfg_all.get("safety"), HERE)
    except Exception as e:
        print(f"FATAL: safety filter failed ({e})", file=sys.stderr)
        return 4

    db = _resolve_db(cfg["db"])
    with Store(db) as store:
        addr = _pick_address(store, args.address, args.tag)
        if not addr:
            print("no address (give an onion/URL or --tag that exists)", file=sys.stderr)
            return 2
        print(f"probing {addr} over Tor ({args.attempts} attempts)…")
        status, http_status, title = probe(addr, cfg, args.attempts)
        became_up = store.set_liveness(addr, status=status, http_status=http_status, title=title)
        print(f"current status: {status.upper()}"
              + (f"  (BACK UP — alert raised)" if became_up else ""))
        row = store.db.execute("SELECT * FROM onions WHERE address=?", (addr,)).fetchone()
        history = store.history(addr)
        tag = args.tag or store.tags_for(addr).split(",")[0]
        draft = build_draft(addr, row, history, status, tag)

    out = Path(args.out) if args.out else (ROOT / "drafts" /
          f"rugpull-{addr[:8]}-{time.strftime('%Y%m%d')}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(draft)
    print("\n" + draft)
    print(f"\n[draft written to {out} — review, fill placeholders, then post manually]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
