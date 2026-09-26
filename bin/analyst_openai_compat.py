#!/usr/bin/env python3
"""Backup analyst over any OpenAI-compatible chat API (OpenRouter, OpenAI,
Mistral, Groq, DeepSeek, ...). Runs after Claude and Gemini have failed.

Providers are tried in the order listed in settings.yaml:

  analyst:
    backup_providers:
    - {name: openrouter, base_url: https://openrouter.ai/api/v1,
       key_env: OPENROUTER_API_KEY, model: <pinned model id>}

A provider is skipped unless its key is in config/env AND a model is pinned.
Pinning is deliberate: "latest"/auto routers change the model underneath us,
and with it the analysis. Output from this engine is stamped
analyst="api:<name>/<model>", which makes run.sh apply the strict checks
(no CVE the candidates don't contain) and publish_linkedin hold every public
post for approval (analyst.backup_review).

Exit codes: 0 decision.json written, 1 could not run (caller falls through).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyst_antigravity import build_user_message, extract_json, load_env  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
TIMEOUT = 300
SYSTEM = ("You are a senior blue-team cyber-threat-intelligence analyst. "
          "You write in clear British English for defenders. "
          "Facts only: never invent CVE IDs, CVSS scores, version numbers, "
          "patch names, IOCs, or attributions. Use only facts present in the "
          "candidates you are given. Output valid JSON only, matching the schema.")


def providers() -> list[dict]:
    try:
        s = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return []
    return [p for p in ((s.get("analyst") or {}).get("backup_providers") or [])
            if isinstance(p, dict)]


def call(base_url: str, key: str, model: str, user_msg: str) -> str:
    r = requests.post(f"{base_url.rstrip('/')}/chat/completions",
                      headers={"Authorization": f"Bearer {key}",
                               "User-Agent": "SecurityCyber-CTI/1.0"},
                      json={"model": model, "temperature": 0.2,
                            "response_format": {"type": "json_object"},
                            "messages": [{"role": "system", "content": SYSTEM},
                                         {"role": "user", "content": user_msg}]},
                      timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError(f"unexpected response shape: {r.text[:200]}") from exc


def main() -> int:
    env = load_env()
    usable = [p for p in providers()
              if p.get("model") and p.get("base_url")
              and (os.environ.get(p.get("key_env", "")) or env.get(p.get("key_env", "")))]
    if not usable:
        print("[analyst_api] no backup provider with a key and a pinned model — skipping")
        return 1
    if not (STATE / "candidates.json").exists():
        print("[analyst_api] no candidates.json — skipping")
        return 1

    user_msg = build_user_message()
    for p in usable:
        name, model = p.get("name") or p["base_url"], p["model"]
        key = os.environ.get(p["key_env"]) or env.get(p["key_env"])
        print(f"[analyst_api] trying {name} / {model} …")
        try:
            decision = extract_json(call(p["base_url"], key, model, user_msg))
        except (RuntimeError, requests.RequestException, json.JSONDecodeError, ValueError) as exc:
            print(f"[analyst_api] {name} failed: {str(exc)[:200]}")
            continue
        if not isinstance(decision, dict) or "decision" not in decision or "posts" not in decision:
            print(f"[analyst_api] {name} returned JSON without decision/posts — next provider")
            continue
        decision["analyst"] = f"api:{name}/{model}"
        decision.setdefault("run_at", datetime.now(timezone.utc).isoformat())
        dec = STATE / "decision.json"
        tmp = dec.with_suffix(".tmp")
        tmp.write_text(json.dumps(decision, indent=2, ensure_ascii=False))
        tmp.replace(dec)
        print(f"[analyst_api] wrote decision={decision['decision']} "
              f"posts={len(decision.get('posts') or [])} alerts={len(decision.get('alerts') or [])} "
              f"analyst={decision['analyst']}")
        return 0
    print("[analyst_api] all backup providers failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
