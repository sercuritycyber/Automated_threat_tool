#!/usr/bin/env python3
"""Back up the subscriber database.

Local SQLite  -> a consistent copy via the online backup API into
                 state/backups/subscribers-YYYYMMDD-HHMMSS.db (keeps last 14).
Turso         -> no-op: Turso keeps point-in-time backups server-side. Prints a
                 reminder to check the retention window in the Turso dashboard.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

KEEP = 14


def main() -> int:
    if os.getenv("TURSO_DATABASE_URL") and os.getenv("TURSO_AUTH_TOKEN"):
        print("[backup] Turso backend — server-side point-in-time backups apply. "
              "Check the retention window in the Turso dashboard; nothing to do locally.")
        return 0

    src = Path(os.getenv("SUBS_DB_PATH") or (ROOT / "state" / "subscribers.db"))
    if not src.exists():
        print("[backup] no local subscribers.db yet — nothing to back up")
        return 0

    outdir = ROOT / "state" / "backups"
    outdir.mkdir(parents=True, exist_ok=True)
    dst = outdir / f"subscribers-{time.strftime('%Y%m%d-%H%M%S')}.db"

    con = sqlite3.connect(src)
    bck = sqlite3.connect(dst)
    with bck:
        con.backup(bck)
    bck.close()
    con.close()
    os.chmod(dst, 0o600)
    print(f"[backup] wrote {dst.relative_to(ROOT)} ({dst.stat().st_size} bytes)")

    olds = sorted(outdir.glob("subscribers-*.db"))
    for f in olds[:-KEEP]:
        f.unlink()
        print(f"[backup] pruned {f.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
