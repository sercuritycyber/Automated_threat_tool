#!/usr/bin/env python3
"""Data-retention purge — deletes personal data past its retention window.

Runs weekly (cti-retention.timer). Deletes:
  * cancelled subscribers  after subscriptions.retention_days_cancelled
  * unconfirmed sign-ups    after subscriptions.retention_days_unconfirmed

Erasure is the same code path as a DSAR deletion (subscribers.erase_subscriber):
all PII rows gone, one anonymous audit line kept.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "bin"))

import subsconfig       # noqa: E402
import subscribers as S  # noqa: E402


def main() -> int:
    dry = "--dry" in sys.argv
    cfg = subsconfig.load()
    S.ensure_schema()
    d_cancel = int(cfg.get("retention_days_cancelled", 180))
    d_unconf = int(cfg.get("retention_days_unconfirmed", 7))

    if dry:
        import db
        cutc = S._iso_days_ago(d_cancel)
        cutu = S._iso_days_ago(d_unconf)
        nc = db.get_db().query(
            "SELECT COUNT(*) n FROM subscribers WHERE status='cancelled' AND "
            "COALESCE(cancelled_at, created_at) < ?", [cutc])[0]["n"]
        nu = db.get_db().query(
            "SELECT COUNT(*) n FROM subscribers WHERE status IN ('pending_payment','unconfirmed') "
            "AND created_at < ?", [cutu])[0]["n"]
        print(f"[retention] DRY: would erase {nc} cancelled (>{d_cancel}d) "
              f"and {nu} unconfirmed (>{d_unconf}d)")
        return 0

    nc = S.purge_cancelled(d_cancel)
    nu = S.purge_unconfirmed(d_unconf)
    ni = S.purge_intents(48)
    S.kv_set("last_retention_run", S._now())
    print(f"[retention] erased {nc} cancelled (>{d_cancel}d), {nu} unconfirmed (>{d_unconf}d), "
          f"{ni} stale signup intent(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
