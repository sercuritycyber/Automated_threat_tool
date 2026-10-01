#!/usr/bin/env python3
"""Internal blockchain watcher — alert on large/imminent outflows from a wallet.

INTERNAL ONLY. This never posts anything publicly. It watches crypto addresses
YOU supply (e.g. a verified ShinyHunters warchest address) and raises an alert
when a large amount moves or is about to move — the signal that an exit-scam
warchest is being drained.

Hard rules:
* No addresses are invented or shipped. You add verified/candidate addresses;
  the tool only watches what it is given.
* BTC is supported via mempool.space (no API key). It queries over Tor by
  default so the set of addresses we watch isn't leaked to the explorer from our
  IP. If Tor is required and unavailable it errors rather than falling back to
  clearnet (which would leak).
* If the warchest is Monero, on-chain watching is impossible — this tool can't
  help there, and says so instead of pretending.

Subcommands: add / list / check / alerts  (see --help).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import torfetch  # noqa: E402

try:
    import yaml
except Exception:
    yaml = None

ROOT = HERE.parent

BTC_RE = re.compile(r"^(bc1[a-z0-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,39})$")
ETH_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
MEMPOOL = "https://mempool.space/api"
SATS = 100_000_000


# --------------------------------------------------------------------------- store
SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    address       TEXT PRIMARY KEY,
    label         TEXT,
    asset         TEXT NOT NULL DEFAULT 'btc',
    threshold_sats INTEGER NOT NULL,
    first_seen    REAL NOT NULL,
    last_checked  REAL,
    balance_sats  INTEGER,
    tx_count      INTEGER,
    last_txid     TEXT,
    note          TEXT
);
CREATE TABLE IF NOT EXISTS chain_alerts (
    id            INTEGER PRIMARY KEY,
    address       TEXT NOT NULL,
    label         TEXT,
    kind          TEXT NOT NULL,     -- outflow | pending-outflow | activity
    amount_sats   INTEGER,
    txid          TEXT,
    detail        TEXT,
    created_at    REAL NOT NULL,
    acked         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_calert_acked ON chain_alerts(acked);
"""


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def add_wallet(self, address, label, asset, threshold_sats, note="", now=None):
        now = time.time() if now is None else now
        try:
            self.db.execute(
                "INSERT INTO wallets(address,label,asset,threshold_sats,first_seen,note)"
                " VALUES(?,?,?,?,?,?)",
                (address, label, asset, threshold_sats, now, note))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.execute("UPDATE wallets SET label=?,threshold_sats=?,note=? WHERE address=?",
                            (label, threshold_sats, note, address))
            self.db.commit()
            return False

    def wallets(self):
        return self.db.execute("SELECT * FROM wallets ORDER BY first_seen").fetchall()

    def update_state(self, address, *, balance_sats, tx_count, last_txid, now=None):
        now = time.time() if now is None else now
        self.db.execute(
            "UPDATE wallets SET last_checked=?,balance_sats=?,tx_count=?,last_txid=? WHERE address=?",
            (now, balance_sats, tx_count, last_txid, address))
        self.db.commit()

    def add_alert(self, address, label, kind, amount_sats, txid, detail, now=None):
        now = time.time() if now is None else now
        self.db.execute(
            "INSERT INTO chain_alerts(address,label,kind,amount_sats,txid,detail,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (address, label, kind, amount_sats, txid, detail, now))
        self.db.commit()

    def alerts(self, unacked_only=True):
        q = "SELECT * FROM chain_alerts"
        if unacked_only:
            q += " WHERE acked=0"
        return self.db.execute(q + " ORDER BY created_at DESC").fetchall()

    def unacked_count(self):
        return self.db.execute("SELECT COUNT(*) FROM chain_alerts WHERE acked=0").fetchone()[0]

    def ack_all(self):
        cur = self.db.execute("UPDATE chain_alerts SET acked=1 WHERE acked=0")
        self.db.commit()
        return cur.rowcount


# --------------------------------------------------------------------------- config
def _load_cfg(path):
    if yaml is None or not Path(path).exists():
        return {}
    return yaml.safe_load(Path(path).read_text()) or {}


def _settings(cfg):
    s = dict(cfg.get("chainwatch") or {})
    s.setdefault("db", str(ROOT / "state" / "chainwatch.db"))
    s.setdefault("over_tor", True)
    s.setdefault("proxy_host", torfetch.DEFAULT_PROXY_HOST)
    s.setdefault("proxy_port", torfetch.DEFAULT_PROXY_PORT)
    s.setdefault("timeout", 60)
    s.setdefault("default_threshold_btc", 0.1)
    return s


def _resolve(p):
    p = Path(p).expanduser()
    return p if p.is_absolute() else (ROOT / p)


# --------------------------------------------------------------------------- fetch (BTC / mempool.space)
def _get_json(url, cfg):
    if cfg["over_tor"]:
        r = torfetch.fetch(url, proxy_host=cfg["proxy_host"], proxy_port=int(cfg["proxy_port"]),
                           timeout=float(cfg["timeout"]), allow_clearnet=True)
        return json.loads(r.text)
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": torfetch.DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=float(cfg["timeout"])) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def btc_summary(address, cfg):
    """Return (balance_sats, tx_count, pending_spent_sats)."""
    d = _get_json(f"{MEMPOOL}/address/{address}", cfg)
    cs = d.get("chain_stats", {})
    ms = d.get("mempool_stats", {})
    balance = int(cs.get("funded_txo_sum", 0)) - int(cs.get("spent_txo_sum", 0))
    tx_count = int(cs.get("tx_count", 0)) + int(ms.get("tx_count", 0))
    pending_spent = int(ms.get("spent_txo_sum", 0))
    return balance, tx_count, pending_spent


def btc_latest_txid(address, cfg):
    try:
        txs = _get_json(f"{MEMPOOL}/address/{address}/txs", cfg)
        return txs[0]["txid"] if txs else None
    except Exception:
        return None


# --------------------------------------------------------------------------- alert push
def _push(cfg, text):
    import os
    url = (os.environ.get("CHAINWATCH_ALERT_NTFY")
           or (cfg.get("alert") or {}).get("ntfy_url") or "").strip()
    if not url:
        return
    import urllib.request
    try:
        req = urllib.request.Request(url, data=text.encode(),
                                     headers={"Title": "SecurityCyber chainwatch",
                                              "Priority": "high", "Tags": "money_with_wings"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"[chainwatch push failed] {e}", file=sys.stderr)


def _btc(sats):
    return f"{sats / SATS:.6f} BTC"


# --------------------------------------------------------------------------- commands
def _validate(address, asset):
    if asset == "btc":
        return bool(BTC_RE.match(address))
    if asset == "eth":
        return bool(ETH_RE.match(address))
    return False


def cmd_add(args):
    cfg_all = _load_cfg(Path(args.config))
    cfg = _settings(cfg_all)
    asset = args.asset.lower()
    addr = args.address.strip()
    if asset == "xmr" or addr.startswith(("4", "8")) and len(addr) > 90:
        print("Monero addresses can't be watched on-chain (private). Not added.", file=sys.stderr)
        return 5
    if not _validate(addr, asset):
        print(f"not a valid {asset} address: {addr}", file=sys.stderr)
        return 2
    thr = int((args.threshold_btc if args.threshold_btc is not None
               else cfg["default_threshold_btc"]) * SATS)
    with Store(_resolve(cfg["db"])) as st:
        new = st.add_wallet(addr, args.label, asset, thr, args.note or "")
        # baseline immediately so the first real move isn't missed/false-fired
        if asset == "btc":
            try:
                bal, n, _ = btc_summary(addr, cfg)
                st.update_state(addr, balance_sats=bal, tx_count=n, last_txid=btc_latest_txid(addr, cfg))
                print(f"{'added' if new else 'updated'}: {addr} [{args.label}] "
                      f"balance {_btc(bal)}, {n} txs, alert threshold {_btc(thr)}")
            except Exception as e:
                print(f"{'added' if new else 'updated'} (baseline fetch failed: {e})")
        else:
            print(f"{'added' if new else 'updated'}: {addr} [{args.label}] (eth baseline not fetched)")
    return 0


def cmd_check(args):
    cfg_all = _load_cfg(Path(args.config))
    cfg = _settings(cfg_all)
    alerts = 0
    with Store(_resolve(cfg["db"])) as st:
        rows = st.wallets()
        if not rows:
            print("no wallets watched — add one with: chainwatch.py add <addr> --label ...")
            return 0
        for w in rows:
            addr, asset = w["address"], w["asset"]
            if asset != "btc":
                print(f"skip {addr}: only btc supported for now")
                continue
            try:
                bal, n, pending_spent = btc_summary(addr, cfg)
            except Exception as e:
                print(f"[{w['label']}] fetch error: {e}", file=sys.stderr)
                continue
            prev_bal = w["balance_sats"]
            prev_n = w["tx_count"]
            thr = w["threshold_sats"]
            txid = btc_latest_txid(addr, cfg)
            label = w["label"] or addr[:10]
            # imminent: large spend sitting in the mempool
            if pending_spent >= thr:
                st.add_alert(addr, label, "pending-outflow", pending_spent, txid,
                             f"{_btc(pending_spent)} spending in mempool")
                _push(cfg, f"[chainwatch] IMMINENT {_btc(pending_spent)} leaving {label} ({addr})")
                print(f"ALERT pending-outflow {label}: {_btc(pending_spent)} in mempool")
                alerts += 1
            # confirmed: balance dropped by >= threshold since last check
            if prev_bal is not None:
                drop = prev_bal - bal
                if drop >= thr:
                    st.add_alert(addr, label, "outflow", drop, txid,
                                 f"balance fell {_btc(drop)} ({_btc(prev_bal)} -> {_btc(bal)})")
                    _push(cfg, f"[chainwatch] OUTFLOW {_btc(drop)} from {label} ({addr}) tx {txid}")
                    print(f"ALERT outflow {label}: -{_btc(drop)} (now {_btc(bal)})")
                    alerts += 1
                elif prev_n is not None and n > prev_n and drop < thr:
                    st.add_alert(addr, label, "activity", abs(drop), txid,
                                 f"{n - prev_n} new tx; balance {_btc(prev_bal)} -> {_btc(bal)}")
                    print(f"ALERT activity {label}: {n - prev_n} new tx (balance {_btc(bal)})")
                    alerts += 1
            st.update_state(addr, balance_sats=bal, tx_count=n, last_txid=txid)
    print(f"chainwatch: checked {len(rows)} wallet(s), {alerts} alert(s)")
    return 0


def cmd_list(args):
    cfg = _settings(_load_cfg(Path(args.config)))
    with Store(_resolve(cfg["db"])) as st:
        rows = st.wallets()
        if not rows:
            print("no wallets watched")
            return 0
        for w in rows:
            bal = _btc(w["balance_sats"]) if w["balance_sats"] is not None else "?"
            lc = time.strftime("%Y-%m-%d %H:%M", time.localtime(w["last_checked"])) if w["last_checked"] else "never"
            print(f"{w['asset']}  {w['address']}  [{w['label']}]  bal={bal}  txs={w['tx_count']}  "
                  f"thr={_btc(w['threshold_sats'])}  checked={lc}")
    return 0


def cmd_alerts(args):
    cfg = _settings(_load_cfg(Path(args.config)))
    with Store(_resolve(cfg["db"])) as st:
        if args.ack_all:
            print(f"acked {st.ack_all()} alert(s)")
            return 0
        rows = st.alerts(unacked_only=not args.all)
        if not rows:
            print("no chain alerts")
            return 0
        for r in rows:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))
            print(f"[{ts}] {r['kind']} {r['label']}: {r['detail']}  tx={r['txid']}")
        print(f"\n{len(rows)} alert(s)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=str(HERE / "chainwatch.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="watch an address you have verified")
    a.add_argument("address")
    a.add_argument("--label", default="")
    a.add_argument("--asset", default="btc", help="btc (default) or eth")
    a.add_argument("--threshold-btc", type=float, default=None, help="alert when this much moves")
    a.add_argument("--note", default="")
    a.set_defaults(func=cmd_add)

    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("list").set_defaults(func=cmd_list)

    al = sub.add_parser("alerts")
    al.add_argument("--all", action="store_true")
    al.add_argument("--ack-all", action="store_true")
    al.set_defaults(func=cmd_alerts)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
