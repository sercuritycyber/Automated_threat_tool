#!/usr/bin/env python3
"""Antigravity (Gemini) backup analyst for the CTI pipeline.

Runs when `claude -p` is unavailable (session limit, outage, bad response).
Unlike analyst_fallback.py (which is deterministic and always skips public
posts), this actually reads and scores the stories so it can post when
something genuinely warrants it.

Requires:  GEMINI_API_KEY in config/env  (or the environment)
Model:     gemini-2.5-pro  (falls back to gemini-2.0-flash on quota error)

The analyst prompt is read from prompts/analyst.md — identical to Claude's.
Output schema is identical to what claude -p writes to state/decision.json.

Exit codes:
  0  decision.json written (post or skip)
  1  could not run (no key, API error, bad JSON from model) — caller falls
     through to analyst_fallback.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
PROMPT_FILE = ROOT / "prompts" / "analyst.md"

# Model preference order — fall through on 429/503
MODELS = [
    "gemini-2.5-pro",
    "gemini-2.0-flash",
]
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT = 300  # seconds per attempt


def load_env() -> dict[str, str]:
    """Load config/env (KEY=value lines, # comments stripped)."""
    env: dict[str, str] = {}
    env_file = ROOT / "config" / "env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def build_user_message() -> str:
    """Combine the analyst prompt with the current candidates."""
    prompt = PROMPT_FILE.read_text()
    cands_path = STATE / "candidates.json"
    cands_raw = cands_path.read_text()

    # The analyst prompt instructs Claude to read state/candidates.json itself.
    # For the REST API we inject the file content directly so the model doesn't
    # need file-system access.
    return (
        prompt
        + "\n\n---\n\n"
        + "**The file `state/candidates.json` contains:**\n\n"
        + "```json\n"
        + cands_raw
        + "\n```\n\n"
        + "Write `state/decision.json` as specified. "
        + "Output ONLY the raw JSON — no markdown fences, no explanation before or after."
    )


def call_gemini(api_key: str, model: str, user_msg: str) -> str:
    """Call Gemini generateContent. Returns the text response."""
    url = f"{API_BASE}/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 65536,
            "responseMimeType": "application/json",
        },
        "systemInstruction": {
            "parts": [{
                "text": (
                    "You are a senior blue-team cyber-threat-intelligence analyst. "
                    "You write in clear British English for defenders. "
                    "Facts only — never invent CVE IDs, CVSS scores, version numbers, "
                    "patch names, IOCs, or attributions. "
                    "You output valid JSON only, exactly matching the schema given."
                )
            }]
        },
    }
    resp = requests.post(url, json=payload, timeout=TIMEOUT)
    if resp.status_code == 429:
        raise RuntimeError(f"quota: {resp.text[:200]}")
    if resp.status_code >= 500:
        raise RuntimeError(f"server error {resp.status_code}: {resp.text[:200]}")
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"unexpected response shape: {data}") from exc


def extract_json(raw: str) -> dict:
    """Strip optional markdown fences and parse JSON."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        # drop first and last fence lines
        raw = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
    return json.loads(raw)


def main() -> int:
    # ── env / key ──────────────────────────────────────────────────────────
    env = load_env()
    api_key = os.environ.get("GEMINI_API_KEY") or env.get("GEMINI_API_KEY", "")
    if not api_key:
        print("[analyst_antigravity] no GEMINI_API_KEY — skipping (will use fallback)")
        return 1

    # ── inputs ─────────────────────────────────────────────────────────────
    cands_path = STATE / "candidates.json"
    if not cands_path.exists():
        print("[analyst_antigravity] no candidates.json — skipping")
        return 1

    try:
        cands = json.loads(cands_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[analyst_antigravity] bad candidates.json: {exc}")
        return 1

    # ── check if a fresh analyst decision already exists ────────────────────
    dec_path = STATE / "decision.json"
    if dec_path.exists():
        try:
            dec = json.loads(dec_path.read_text())
            if (dec.get("run_at", "") >= cands.get("generated_at", "")
                    and not dec.get("fallback")):
                print("[analyst_antigravity] fresh claude decision already exists — leaving it")
                return 0
        except (OSError, json.JSONDecodeError):
            pass

    print("[analyst_antigravity] claude unavailable — engaging Gemini backup analyst")
    user_msg = build_user_message()

    # ── call Gemini, try models in order ───────────────────────────────────
    last_err = ""
    for model in MODELS:
        print(f"[analyst_antigravity] trying {model} …")
        try:
            raw = call_gemini(api_key, model, user_msg)
            break
        except RuntimeError as exc:
            last_err = str(exc)
            print(f"[analyst_antigravity] {model} failed: {last_err}")
            if "quota" in last_err:
                time.sleep(2)
            continue
    else:
        print(f"[analyst_antigravity] all models failed — last error: {last_err}")
        return 1

    # ── parse + validate ───────────────────────────────────────────────────
    try:
        decision = extract_json(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[analyst_antigravity] model returned invalid JSON: {exc}")
        print(f"[analyst_antigravity] raw (first 500 chars): {raw[:500]}")
        return 1

    if "decision" not in decision or "posts" not in decision:
        print(f"[analyst_antigravity] JSON missing required keys: {list(decision.keys())}")
        return 1

    # Stamp the analyst
    decision["analyst"] = "antigravity-gemini"
    decision.setdefault("run_at", datetime.now(timezone.utc).isoformat())

    # ── write ──────────────────────────────────────────────────────────────
    tmp = dec_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(decision, indent=2, ensure_ascii=False))
    tmp.replace(dec_path)

    n_posts = len(decision.get("posts") or [])
    n_alerts = len(decision.get("alerts") or [])
    print(
        f"[analyst_antigravity] wrote decision={decision['decision']} "
        f"posts={n_posts} alerts={n_alerts} analyst=gemini"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
