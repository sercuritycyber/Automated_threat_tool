#!/usr/bin/env python3
"""Deterministic stand-in for the `claude -p` analyst.

Runs when the LLM analyst is unavailable — token limits, an outage, a bad
response. It cannot do judgement, so it does NOT decide to post anything
publicly (`decision: "skip"`, `posts: []`). What it CAN do is keep the paid
subscriber feed alive: it turns every pre-scored candidate at or above the
subscriber floor into an `alerts[]` entry with a plain, honest write-up built
from the fields ingest already collected — clearly marked as an unverified
auto-summary so nobody mistakes it for the analyst's work.

Reads  state/candidates.json
Writes state/decision.json   (only if the analyst left none, or left a stale one)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"


def _summary_text(c: dict) -> str:
    bits = []
    kev = c.get("kev_recent_cves") or c.get("kev_cves") or []
    if kev:
        bits.append(f"On the CISA KEV catalog: {', '.join(kev)}. Treat as actively exploited.")
    cves = c.get("cve_ids") or []
    if cves:
        bits.append(f"CVE: {', '.join(cves)}.")
    summ = (c.get("summary") or "").strip()
    if summ:
        bits.append(summ if len(summ) <= 600 else summ[:600].rsplit(" ", 1)[0] + "…")
    reasons = c.get("pre_score_reasons") or []
    if reasons:
        bits.append("Scoring signals: " + "; ".join(reasons) + ".")
    bits.append(
        "This is an automated summary — the source has not been read by an analyst. "
        "Confirm affected versions and exploitation status against the linked advisory before acting."
    )
    return "\n\n".join(bits)


def build(cands: dict) -> dict:
    floor = int(cands.get("subscriber_min_score", 10))
    items = cands.get("candidates") or []
    alerts = []
    for c in sorted(items, key=lambda x: -(x.get("pre_score") or 0)):
        score = round(float(c.get("pre_score") or 0))
        if score < floor:
            continue
        kev = c.get("kev_recent_cves") or c.get("kev_cves") or []
        action = ("Check exposure and apply the vendor fix — this CVE is on CISA KEV."
                  if kev else "Review the linked advisory and assess whether your stack is affected.")
        alerts.append({
            "title": c.get("title") or (c.get("cve_ids") or ["Threat advisory"])[0],
            "url": c.get("url") or "",
            "source": c.get("source") or "",
            "cve_ids": c.get("cve_ids") or [],
            "score": score,
            "confidence": "low",
            "action": action,
            "subscriber_text": _summary_text(c),
        })
        if len(alerts) >= 40:
            break

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "decision": "skip",
        "reason": f"analyst unavailable — deterministic fallback produced {len(alerts)} unverified subscriber alert(s), no public post",
        "min_score": int(cands.get("min_score", 60)),
        "posts": [],
        "alerts": alerts,
        "considered": [
            {"title": c.get("title"), "url": c.get("url"), "score": round(float(c.get("pre_score") or 0)),
             "note": "fallback: not analyst-reviewed"}
            for c in items[:40]
        ],
        "fallback": True,
    }


def main() -> int:
    src = STATE / "candidates.json"
    try:
        cands = json.loads(src.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[analyst_fallback] no usable candidates.json ({e})")
        return 0

    dec_path = STATE / "decision.json"
    # Only write if the analyst genuinely left nothing fresh for this run.
    if dec_path.exists():
        try:
            dec = json.loads(dec_path.read_text())
            if dec.get("run_at", "") >= cands.get("generated_at", "") and not dec.get("fallback"):
                print("[analyst_fallback] a fresh analyst decision already exists — leaving it")
                return 0
        except (OSError, json.JSONDecodeError):
            pass

    out = build(cands)
    dec_path.write_text(json.dumps(out, indent=2))
    print(f"[analyst_fallback] wrote {len(out['alerts'])} unverified alert(s), decision=skip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
