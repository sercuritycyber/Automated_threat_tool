#!/usr/bin/env python3
"""Local-model analyst pass, to spare Claude tokens.

Sends `state/candidates.json` to an Ollama model (the one the retired Hermes
agent used — IBM Granite by default) and asks it for a triage `decision.json`:
short, unverified `alerts[]` for every real candidate, and `decision: skip`
always (a 3B local model does not get to decide what posts publicly).

Guardrails — a small local model is not trusted, so:
  * It never decides `post`. `decision` is forced to `skip`, `posts` to `[]`.
  * Its JSON is validated field-by-field; anything malformed is dropped, not
    guessed. If the whole response is unusable, exit non-zero and let run.sh
    fall through to `analyst_fallback.py`.
  * Every `score` is clamped 0-100; a KEV-recent item floors at 80.
  * `subscriber_text` is truncated to 5 sentences / 900 chars and stamped as
    an automated triage summary.
  * URLs/titles/CVE ids are taken from the ORIGINAL candidate, never from the
    model's echo — so it cannot fabricate a link or a CVE.
  * `confidence` is forced to "low".

Escalation: writes `state/escalate.json` listing candidates that look
high-impact (KEV-recent, or pre_score >= escalate_score) so run.sh can run a
narrow `claude -p` deep pass on just those, if configured.

Env / settings (config/settings.yaml -> analyst:):
  OLLAMA_URL         default http://100.112.208.108:11434   (hermes-ollama tailnet)
  OLLAMA_MODEL       default granite3.1-moe:3b
  analyst.ollama_timeout_s   default 180
  analyst.escalate_score     default 75

Reads  state/candidates.json
Writes state/decision.json, state/escalate.json
Exit 0 on a usable result, non-zero otherwise.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
PROMPT = (ROOT / "prompts" / "analyst_ollama.md").read_text()


def _settings() -> dict:
    try:
        import yaml
        return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}
    except Exception:
        return {}


def _sentences(text: str, n: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    return " ".join(parts[:n]).strip()


def _warm(url: str, model: str) -> None:
    try:
        req = urllib.request.Request(url.rstrip("/") + "/api/generate",
            data=json.dumps({"model": model, "prompt": "ok", "stream": False,
                             "options": {"num_predict": 1}}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=90)
    except Exception:
        pass


def ask_ollama(url: str, model: str, candidates: dict, timeout: int) -> str:
    _warm(url, model)
    # A 3B model cannot chew 49 fat candidates. Send only the fields it needs,
    # capped, summaries clipped.
    slim = {
        "min_score": candidates.get("min_score", 60),
        "subscriber_min_score": candidates.get("subscriber_min_score", 15),
        "candidates": [
            {
                "title": c.get("title"), "url": c.get("url"), "source": c.get("source"),
                "cve_ids": c.get("cve_ids") or [],
                "kev_recent_cves": c.get("kev_recent_cves") or [],
                "pre_score": round(float(c.get("pre_score") or 0)),
                "summary": (c.get("summary") or "")[:400],
            }
            for c in sorted(candidates.get("candidates", []),
                            key=lambda x: -(x.get("pre_score") or 0))[:35]
        ],
    }
    payload = {
        "model": model,
        "prompt": PROMPT + "\n\n## INPUT\n" + json.dumps(slim, default=str),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_ctx": 16384, "num_predict": 6144},
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("response", "")


def coerce(raw: str, cands: dict) -> dict | None:
    """Validate the model's JSON against the real candidates. Drop anything junk."""
    try:
        m = json.loads(raw)
    except json.JSONDecodeError:
        # some models wrap it; grab the first {...}
        try:
            m = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            return None
    if not isinstance(m, dict):
        return None

    by_url = {c.get("url"): c for c in cands.get("candidates", []) if c.get("url")}
    by_title = {c.get("title"): c for c in cands.get("candidates", [])}
    floor = int(cands.get("subscriber_min_score", 15))

    out_alerts = []
    for a in (m.get("alerts") or []):
        if not isinstance(a, dict):
            continue
        src = by_url.get(a.get("url")) or by_title.get(a.get("title"))
        if not src:
            continue  # model referenced something that isn't a real candidate
        pre = float(src.get("pre_score") or 0)
        kev_recent = src.get("kev_recent_cves") or []
        score = max(pre, 80) if kev_recent else pre
        score = max(0, min(100, round(score)))
        if score < floor:
            continue
        body = _sentences(a.get("subscriber_text", ""), 5)[:900].strip()
        if not body:
            body = (src.get("summary") or "").strip()[:500]
        if "automated" not in body.lower() and "triage" not in body.lower():
            body += "\n\nAutomated triage summary — confirm against the linked advisory before acting."
        act = str(a.get("action") or "").strip()
        if not act:
            act = ("Check exposure and apply the vendor fix — this is on CISA KEV."
                   if kev_recent else
                   "Review the linked advisory and confirm whether your stack is affected.")
        out_alerts.append({
            # identity ALWAYS from the real candidate, never the model
            "title": src.get("title") or (src.get("cve_ids") or ["Threat advisory"])[0],
            "url": src.get("url") or "",
            "source": src.get("source") or "",
            "cve_ids": src.get("cve_ids") or [],
            "score": score,
            "confidence": "low",
            "action": act[:200],
            "subscriber_text": body,
        })

    if not out_alerts:
        return None

    out_alerts.sort(key=lambda x: -x["score"])
    trimmed = len(out_alerts) > 30
    out_alerts = out_alerts[:30]

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "decision": "skip",  # forced — local model never posts
        "reason": (f"local triage pass (ollama): {len(out_alerts)} unverified subscriber alert(s)"
                   + (", trimmed to top 30" if trimmed else "") + ", no public post"),
        "min_score": int(cands.get("min_score", 60)),
        "posts": [],
        "alerts": out_alerts,
        "considered": [
            {"title": c.get("title"), "url": c.get("url"),
             "score": round(float(c.get("pre_score") or 0)), "note": "local triage"}
            for c in cands.get("candidates", [])[:40]
        ],
        "engine": "ollama",
        "fallback": True,  # so downstream treats it like the deterministic fallback (unverified)
    }


def write_escalation(cands: dict, threshold: int) -> int:
    hot = []
    for c in cands.get("candidates", []):
        if (c.get("kev_recent_cves") or []) or float(c.get("pre_score") or 0) >= threshold:
            hot.append({k: c.get(k) for k in ("title", "url", "source", "cve_ids",
                                              "summary", "pre_score", "kev_recent_cves")})
    (STATE / "escalate.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "threshold": threshold, "candidates": hot}, indent=2, default=str))
    return len(hot)


def main() -> int:
    st = _settings().get("analyst", {}) if isinstance(_settings().get("analyst"), dict) else {}
    url = os.getenv("OLLAMA_URL") or st.get("ollama_url") or "http://100.112.208.108:11434"
    model = os.getenv("OLLAMA_MODEL") or st.get("ollama_model") or "granite3.1-moe:3b"
    timeout = max(240, int(st.get("ollama_timeout_s", 300)))
    esc_score = int(st.get("escalate_score", 75))

    try:
        cands = json.loads((STATE / "candidates.json").read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[analyst_ollama] no candidates.json ({e})")
        return 2

    if not cands.get("candidates"):
        (STATE / "decision.json").write_text(json.dumps({
            "run_at": datetime.now(timezone.utc).isoformat(), "decision": "skip",
            "reason": "no items in window", "min_score": int(cands.get("min_score", 60)),
            "posts": [], "alerts": [], "considered": [], "engine": "ollama"}, indent=2))
        return 0

    try:
        raw = ask_ollama(url, model, cands, timeout)
    except Exception as e:  # noqa: BLE001 - any failure => fall through
        print(f"[analyst_ollama] {model} @ {url} unreachable: {e}")
        return 3

    dec = coerce(raw, cands)
    if not dec:
        print("[analyst_ollama] model output unusable after validation")
        return 4

    (STATE / "decision.json").write_text(json.dumps(dec, indent=2))
    n_esc = write_escalation(cands, esc_score)
    print(f"[analyst_ollama] wrote {len(dec['alerts'])} triage alert(s); "
          f"{n_esc} candidate(s) flagged for a claude deep pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
