"""Tests for the internal blockchain watcher — no network (explorer mocked)."""
import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "darkweb"))

import chainwatch as C

BTC_BECH32 = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
BTC_LEGACY = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
ETH = "0x" + "a" * 40
SATS = C.SATS


def test_address_validation():
    assert C._validate(BTC_BECH32, "btc")
    assert C._validate(BTC_LEGACY, "btc")
    assert C._validate(ETH, "eth")
    assert not C._validate("not-an-address", "btc")
    assert not C._validate(BTC_BECH32, "eth")
    assert not C._validate("4" + "9" * 94, "xmr")  # unsupported asset


def _cfg(tmp):
    p = Path(tmp) / "chainwatch.yaml"
    db = Path(tmp) / "cw.db"
    p.write_text(f"chainwatch:\n  db: {db}\n  over_tor: false\n  default_threshold_btc: 0.1\n")
    return str(p)


def test_outflow_alert_lifecycle(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)
        monkeypatch.setattr(C, "btc_latest_txid", lambda a, c: "txABC")
        # baseline: 1.0 BTC, 5 txs
        monkeypatch.setattr(C, "btc_summary", lambda a, c: (1 * SATS, 5, 0))
        rc = C.cmd_add(argparse.Namespace(config=cfg, address=BTC_BECH32,
                       label="warchest", asset="btc", threshold_btc=0.25, note=""))
        assert rc == 0

        # no change -> no alert
        C.cmd_check(argparse.Namespace(config=cfg))
        with C.Store(Path(tmp) / "cw.db") as st:
            assert st.unacked_count() == 0

        # big drain: balance 1.0 -> 0.4 BTC (0.6 out, >= 0.25 threshold)
        monkeypatch.setattr(C, "btc_summary", lambda a, c: (int(0.4 * SATS), 6, 0))
        C.cmd_check(argparse.Namespace(config=cfg))
        with C.Store(Path(tmp) / "cw.db") as st:
            al = st.alerts(unacked_only=True)
            assert len(al) == 1 and al[0]["kind"] == "outflow"
            assert al[0]["amount_sats"] == int(0.6 * SATS)
            assert st.ack_all() == 1 and st.unacked_count() == 0


def test_pending_outflow_alert(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)
        monkeypatch.setattr(C, "btc_latest_txid", lambda a, c: "txP")
        monkeypatch.setattr(C, "btc_summary", lambda a, c: (2 * SATS, 10, 0))
        C.cmd_add(argparse.Namespace(config=cfg, address=BTC_LEGACY, label="wc2",
                  asset="btc", threshold_btc=0.5, note=""))
        # 1.5 BTC sitting in the mempool as an outgoing spend -> imminent alert
        monkeypatch.setattr(C, "btc_summary", lambda a, c: (2 * SATS, 10, int(1.5 * SATS)))
        C.cmd_check(argparse.Namespace(config=cfg))
        with C.Store(Path(tmp) / "cw.db") as st:
            al = st.alerts(unacked_only=True)
            assert any(a["kind"] == "pending-outflow" for a in al)


def test_monero_refused(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)
        rc = C.cmd_add(argparse.Namespace(config=cfg, address="8" + "9" * 94,
                       label="xmr", asset="xmr", threshold_btc=None, note=""))
        assert rc == 5  # cannot watch monero
