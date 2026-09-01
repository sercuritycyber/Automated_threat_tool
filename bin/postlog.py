"""Shared read helpers for state/history.jsonl (real LinkedIn posts only)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "state"


def real_posts(hours: float | None = None) -> list[dict]:
    p = STATE / "history.jsonl"
    if not p.exists():
        return []
    rows: list[dict] = []
    for ln in p.read_text().splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if r.get("dry_run"):
            continue
        rows.append(r)
    if hours is None:
        return rows
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for r in rows:
        try:
            t = datetime.fromisoformat(str(r["posted_at"]).replace("Z", "+00:00"))
            if t >= cut:
                out.append(r)
        except (KeyError, ValueError):
            pass
    return out


def count_last_24h() -> int:
    return len(real_posts(24))


if __name__ == "__main__":
    print(count_last_24h())
