#!/usr/bin/env python3
"""Signed, URL-safe, expiring tokens for subscriber links.

    tok = sign({"sub": 42, "act": "manage"}, ttl=7*86400)
    data = verify(tok, "manage")   # -> {"sub": 42, "act": "manage"} or raises BadToken

Format:  base64url(json_payload) + "." + base64url(hmac_sha256)
The payload carries "exp" (unix seconds). Secret = SUBS_TOKEN_SECRET from the
environment; if unset, a 32-byte secret is generated and written to config/env
so links stay valid across restarts.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

_ENV = Path(__file__).resolve().parent.parent / "config" / "env"


class BadToken(Exception):
    pass


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret() -> bytes:
    s = os.getenv("SUBS_TOKEN_SECRET", "").strip()
    if s:
        return s.encode()
    gen = secrets.token_urlsafe(48)
    try:
        lines = _ENV.read_text().splitlines() if _ENV.exists() else []
        if not any(ln.startswith("SUBS_TOKEN_SECRET=") for ln in lines):
            lines.append(f"SUBS_TOKEN_SECRET={gen}")
            _ENV.write_text("\n".join(lines) + "\n")
            os.chmod(_ENV, 0o600)
    except OSError:
        pass
    os.environ["SUBS_TOKEN_SECRET"] = gen
    return gen.encode()


def sign(payload: dict, ttl: int = 7 * 86400) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + int(ttl)
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    p = _b64e(raw)
    sig = _b64e(hmac.new(_secret(), p.encode(), hashlib.sha256).digest())
    return f"{p}.{sig}"


def verify(token: str, expect_act: str | None = None) -> dict:
    try:
        p, sig = token.split(".", 1)
    except ValueError:
        raise BadToken("malformed token")
    good = _b64e(hmac.new(_secret(), p.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        raise BadToken("bad signature")
    try:
        data = json.loads(_b64d(p))
    except (ValueError, json.JSONDecodeError):
        raise BadToken("bad payload")
    if int(data.get("exp", 0)) < int(time.time()):
        raise BadToken("expired")
    if expect_act is not None and data.get("act") != expect_act:
        raise BadToken("wrong action")
    return data


if __name__ == "__main__":
    t = sign({"sub": 1, "act": "manage"}, ttl=60)
    print(t)
    print(verify(t, "manage"))
