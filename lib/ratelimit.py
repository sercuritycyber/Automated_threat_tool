#!/usr/bin/env python3
"""In-process fixed-window rate limiter for the public app.

    if not allow(ip, limit=20, window=60): -> 429

Single-process (one uvicorn worker behind Caddy) so an in-memory dict is enough;
limits reset on restart, which is fine for basic abuse protection.
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_BUCKETS: dict[str, tuple[float, int]] = {}   # key -> (window_start, count)


def allow(key: str, limit: int = 20, window: int = 60) -> bool:
    now = time.time()
    with _LOCK:
        start, count = _BUCKETS.get(key, (now, 0))
        if now - start >= window:
            start, count = now, 0
        count += 1
        _BUCKETS[key] = (start, count)
        if len(_BUCKETS) > 5000:                      # crude GC
            for k, (s, _) in list(_BUCKETS.items()):
                if now - s >= window:
                    _BUCKETS.pop(k, None)
        return count <= limit


def retry_after(key: str, window: int = 60) -> int:
    with _LOCK:
        start, _ = _BUCKETS.get(key, (time.time(), 0))
    return max(1, int(window - (time.time() - start)))
