#!/usr/bin/env python3
"""Push free source alerts to the Worker and existing shared Discord channel.
The durable advisory outbox is separate. Explicit empty alerts mean no delivery;
legacy post fallback is used only when alerts are absent.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent

# subscriber_text is retained as a compatibility field for free technical text.
FIELDS = ("title", "url", "source", "score", "cve_ids", "category", "exposure",
          "confidence", "subscriber_text", "action", "post_text", "short_text")


def _clean(item: dict) -> dict:
    out = {k: item.get(k) for k in FIELDS if item.get(k) not in (None, "")}
    # The Worker needs something to actually say.
    if not (out.get("subscriber_text") or out.get("post_text")):
        return {}
    return out


def collect(dec: dict) -> list[dict]:
    """Alerts to deliver, newest analyst shape first, older shape as fallback."""
    alerts = dec.get("alerts")
    if "alerts" in dec:
        if not isinstance(alerts, list) or any(not isinstance(a, dict) for a in alerts):
            raise ValueError("invalid alerts")
        return [c for c in (_clean(a) for a in alerts) if c]
    # Legacy: only the posted items were ever available.
    posts = dec.get("posts") or []
    if not posts and isinstance(dec.get("selected"), dict):
        posts = [dec["selected"]]
    return [c for c in (_clean(p) for p in posts) if c]


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
        print("[push_to_worker] no readable decision.json — nothing to push")
        return 0

    alerts = collect(dec)
    if not alerts:
        print(f"[push_to_worker] decision={dec.get('decision')!r}, no deliverable alerts")
        return 0

    depth = sum(1 for a in alerts if a.get("subscriber_text"))
    print(f"[push_to_worker] sending {len(alerts)} alert(s), {depth} with subscriber depth")
    try:
        r = requests.post(f"{url}/internal/ingest", json={"posts": alerts},
                          headers={"Authorization": f"Bearer {secret}"}, timeout=25)
        print(f"[push_to_worker] {r.status_code} {r.text[:160]}")
        if r.status_code >= 400:
            return 1
    except requests.RequestException as e:
        print(f"[push_to_worker] request failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
