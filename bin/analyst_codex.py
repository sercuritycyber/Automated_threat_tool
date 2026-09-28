#!/usr/bin/env python3
"""Codex (ChatGPT plan) backup analyst. Runs after Claude and the API bridge
have failed, before the paid/keyed backups, because it bills to Charlie's
ChatGPT subscription rather than per token.

The model sees the analyst prompt with candidates.json inlined and nothing
else: `codex exec` runs in an empty scratch dir with a read-only sandbox,
browser/computer-use/apps disabled, user config ignored, and a scrubbed
environment (run.sh exports config/env, so the inherited env holds every
secret). Its output is stamped analyst="codex:<model>", so run.sh applies the
strict backup checks and publish_linkedin holds its public posts for approval.

Usage: analyst_codex.py [--out PATH]   (default state/decision.json)
Exit codes: 0 decision written, 1 could not run (caller falls through).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyst_antigravity import build_user_message, extract_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
TIMEOUT = 900
# Only what codex needs to find its binary and its ChatGPT login (~/.codex).
KEEP_ENV = ("HOME", "PATH", "LANG", "USER", "TMPDIR")
DISABLE = ("browser_use", "browser_use_external", "computer_use", "apps")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(STATE / "decision.json"))
    ap.add_argument("--model", default=os.environ.get("CTI_CODEX_MODEL", ""))
    args = ap.parse_args()

    codex = shutil.which("codex") or str(Path.home() / ".local/bin/codex")
    if not Path(codex).exists():
        print("[analyst_codex] codex not installed — skipping")
        return 1
    if not (STATE / "candidates.json").exists():
        print("[analyst_codex] no candidates.json — skipping")
        return 1

    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    with tempfile.TemporaryDirectory(prefix="cti-codex-") as work:
        last = Path(work) / "last.txt"
        cmd = [codex, "exec", "--ephemeral", "--skip-git-repo-check",
               "--ignore-user-config", "--ignore-rules",
               "--sandbox", "read-only", "-C", work, "-o", str(last)]
        for f in DISABLE:
            cmd += ["--disable", f]
        if args.model:
            cmd += ["-m", args.model]
        cmd.append("-")  # prompt on stdin: candidates can exceed argv limits
        print("[analyst_codex] claude unavailable — engaging Codex backup analyst")
        try:
            p = subprocess.run(cmd, input=build_user_message(), text=True, env=env,
                               cwd=work, capture_output=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"[analyst_codex] timed out after {TIMEOUT}s")
            return 1
        if p.returncode != 0 or not last.exists():
            tail = (p.stderr or p.stdout or "").strip().splitlines()[-3:]
            print(f"[analyst_codex] codex exited {p.returncode}: {' | '.join(tail)[:300]}")
            return 1
        raw = last.read_text()

    try:
        decision = extract_json(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[analyst_codex] invalid JSON: {exc} — first 300 chars: {raw[:300]!r}")
        return 1
    if not isinstance(decision, dict) or "decision" not in decision or "posts" not in decision:
        print("[analyst_codex] JSON missing decision/posts")
        return 1

    decision["analyst"] = f"codex:{args.model or 'default'}"
    decision.setdefault("run_at", datetime.now(timezone.utc).isoformat())
    out = Path(args.out)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(decision, indent=2, ensure_ascii=False))
    tmp.replace(out)
    print(f"[analyst_codex] wrote decision={decision['decision']} "
          f"posts={len(decision.get('posts') or [])} alerts={len(decision.get('alerts') or [])} "
          f"analyst={decision['analyst']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
