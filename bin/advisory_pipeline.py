#!/usr/bin/env python3
"""Deterministic source advisories and an idempotent, durable delivery outbox.
No models, scraping, IOC attribution, or third-party submissions.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import requests
from decision_validation import leaked_secrets

ROOT = Path(__file__).resolve().parent.parent
NOTICE = "Source-derived advisory; not independently verified. Source excerpts are evidence of what the source reports, not confirmation of exploitation or attribution."
ACTION = "Check the linked source, identify affected assets, and validate vendor guidance before making changes."

def canonical_url(value):
    u = urlsplit(str(value))
    if u.scheme not in {"https", "http"} or not u.hostname or u.username or u.password:
        raise ValueError("invalid public source URL")
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path or "/", u.query, ""))

def timestamp(value):
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("timestamp needs timezone")
    return d.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def build(candidate, generated_at):
    url = canonical_url(candidate.get("url", ""))
    title = str(candidate.get("title") or "").strip()
    detail = str(candidate.get("summary") or "").strip()
    if not title or not detail or len(title) > 1000 or len(detail) > 32000:
        raise ValueError("missing or oversized source text")
    score = float(candidate.get("pre_score", 0))
    if not math.isfinite(score) or not 0 <= score <= 100:
        raise ValueError("invalid score")
    published = timestamp(candidate.get("published") or generated_at)
    # CVEs must be present in source text, not inferred from model output.
    cves = sorted(set(re.findall(r"\bCVE-\d{4}-\d{4,}\b", title + "\n" + detail, re.I)))
    cves = sorted(set(x.upper() for x in cves))
    content = {"title": title, "source_url": url, "source": str(candidate.get("source") or "Source"),
               "source_text": detail, "cve_ids": cves}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {"schema_version": 1, "id": digest, **content, "published": published,
            "score": round(score), "method": "deterministic-source-excerpt", "verification": "source-reported",
            "takeaways": ["Reported: " + title, ACTION, NOTICE], "action": ACTION,
            "limitations": NOTICE, "indicators": []}

def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def enqueue(candidates, output, secrets=None, floor=50):
    if not isinstance(candidates, dict) or not isinstance(candidates.get("candidates"), list):
        raise ValueError("invalid candidates")
    accepted, rejected = 0, 0
    for item in candidates["candidates"]:
        try:
            doc = build(item, candidates.get("generated_at"))
            if doc["score"] < floor: continue
            if leaked_secrets(doc, secrets or {}): raise ValueError("secret detected")
        except (ValueError, TypeError, AttributeError, OverflowError):
            rejected += 1
            continue
        path = output / "documents" / (doc["id"] + ".json")
        if not path.exists(): atomic_json(path, doc)
        accepted += 1
    return {"accepted": accepted, "rejected": rejected}

def flush(output, endpoint, secret, session=requests):
    pending = sorted((output / "documents").glob("*.json"))
    sent = failed = attempted = 0
    for path in pending:
        receipt = output / "receipts" / path.name
        if receipt.exists(): continue
        if attempted >= 100: break
        attempted += 1
        if not endpoint or not secret:
            failed += 1
            continue
        try:
            doc = json.loads(path.read_text())
            response = session.post(endpoint.rstrip("/") + "/internal/advisories", json=doc,
                                    headers={"Authorization": "Bearer " + secret}, timeout=25)
            response.raise_for_status()
            ack = response.json()
            if ack.get("id") != doc["id"] or ack.get("stored") is not True:
                raise ValueError("invalid acknowledgement")
            atomic_json(receipt, {"id": doc["id"], "delivered_at": datetime.now(timezone.utc).isoformat()})
            sent += 1
        except (requests.RequestException, ValueError, OSError):
            failed += 1
            # Stop on an outage; next scheduled run retries, including quiet runs.
            break
    return {"sent": sent, "failed": failed}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--min-score", type=int, default=50)
    ap.add_argument("--flush", action="store_true", help="send queued documents to configured Worker")
    args = ap.parse_args()
    if not 0 <= args.min_score <= 100: ap.error("min-score must be 0..100")
    if args.candidates:
        # The scheduled runner supplies its loaded environment. No credentials enter artifacts.
        secrets = {k: v for k, v in os.environ.items() if re.search(r"SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY", k)}
        print(json.dumps(enqueue(json.loads(args.candidates.read_text()), args.output_dir, secrets, args.min_score)))
    if args.flush:
        result = flush(args.output_dir, os.getenv("CTI_WORKER_URL", ""), os.getenv("CTI_INGEST_SECRET", ""))
        print(json.dumps(result))
        return 1 if result["failed"] else 0
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
