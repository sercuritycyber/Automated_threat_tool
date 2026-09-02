#!/usr/bin/env python3
"""Push this run's analyst posts to the CTI Alerts Worker for subscriber fan-out.

No-op unless CTI_WORKER_URL + CTI_INGEST_SECRET are set (in config/env). The
Worker owns all subscriber state, delivery and the daily digest; this just hands
it the fresh posts. Never fatal — the brand's own social post already went out.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("title", "url", "source", "score", "cve_ids", "post_text", "short_text")


def main() -> int:
    url = os.getenv("CTI_WORKER_URL", "").rstrip("/")
    secret = os.getenv("CTI_INGEST_SECRET", "")
    if not (url and secret):
        print("[push_to_worker] CTI_WORKER_URL / CTI_INGEST_SECRET not set — skipping")
        return 0

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else (ROOT / "state" / "decision.json")
    try:
        dec = json.loads(src.read_text())
    except (OSError, json.JSONDecodeError):
        print("[push_to_worker] no decision.json — nothing to push")
        return 0
    if dec.get("decision") != "post":
        return 0
    posts = dec.get("posts") or ([dec["selected"]] if isinstance(dec.get("selected"), dict) else [])
    payload = {"posts": [{k: p.get(k) for k in FIELDS} for p in posts if (p.get("post_text") or "").strip()]}
    if not payload["posts"]:
        return 0

    try:
        r = requests.post(f"{url}/internal/ingest", json=payload,
                          headers={"Authorization": f"Bearer {secret}"}, timeout=25)
        print(f"[push_to_worker] {r.status_code} {r.text[:160]}")
    except requests.RequestException as e:
        print(f"[push_to_worker] request failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
