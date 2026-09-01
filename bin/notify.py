#!/usr/bin/env python3
"""Tiny notification helper. Usage: notify.py "message text" [priority]

Posts to an ntfy.sh-style topic URL taken from NTFY_URL env or
settings.notify.ntfy_url. No-op (exit 0) if none is configured.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent


def target() -> str:
    if os.getenv("NTFY_URL"):
        return os.environ["NTFY_URL"].strip()
    try:
        s = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}
        return (s.get("notify", {}) or {}).get("ntfy_url", "").strip()
    except OSError:
        return ""


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    url = target()
    if not url:
        print("[notify] no ntfy_url configured — skipping")
        return 0
    msg = sys.argv[1]
    prio = sys.argv[2] if len(sys.argv) > 2 else "default"
    try:
        requests.post(url, data=msg.encode(), headers={
            "Title": "Blue Team CTI runner",
            "Priority": prio,
            "Tags": "shield",
        }, timeout=15)
        print("[notify] sent")
    except requests.RequestException as e:
        print(f"[notify] failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
