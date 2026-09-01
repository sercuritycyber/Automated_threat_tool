#!/usr/bin/env python3
"""Append a one-line summary of the run that just finished to state/runs.jsonl.

Feeds the dashboard's activity timeline. Reads state/last_run.json (always
written by the pipeline, one way or another) plus the candidate counts.
Kept to the last 300 rows.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "state"
LEDGER = STATE / "runs.jsonl"
KEEP = 300


def _load(name: str) -> dict:
    try:
        return json.loads((STATE / name).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def append(trigger: str = "timer") -> None:
    lr = _load("last_run.json")
    counts = (_load("candidates.json").get("counts") or {})
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "decision": lr.get("decision"),
        "published": bool(lr.get("published")),
        "dry_run": bool(lr.get("dry_run")),
        "title": lr.get("title"),
        "score": lr.get("score"),
        "note": lr.get("reason") or lr.get("note"),
        "error": lr.get("error"),
        "post_url": lr.get("post_url"),
        "candidates": counts.get("candidates"),
        "clusters": counts.get("clusters"),
    }
    with LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")
    lines = LEDGER.read_text().splitlines()
    if len(lines) > KEEP:
        LEDGER.write_text("\n".join(lines[-KEEP:]) + "\n")


def read(limit: int = 40) -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for ln in LEDGER.read_text().splitlines():
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    return out[-limit:][::-1]


if __name__ == "__main__":
    append(sys.argv[1] if len(sys.argv) > 1 else "timer")
