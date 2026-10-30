#!/usr/bin/env bash
# SecurityCyber — CTI harvest + publish orchestrator.
# Stages: ingest (python) -> analyst -> publish (python).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- config/env (secrets) ------------------------------------------------
if [[ -f "$ROOT/config/env" ]]; then
  set -a; # shellcheck disable=SC1091
  source "$ROOT/config/env"; set +a
fi

mkdir -p "$ROOT/state" "$ROOT/logs" "$ROOT/drafts"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$ROOT/logs/run-$TS.log"

# --- single-run lock ---------------------------------------------------
exec 9>"$ROOT/state/.lock"
if ! flock -n 9; then
  echo "[run] another run holds the lock — exiting"
  exit 0
fi

# tee everything to a timestamped log
exec > >(tee -a "$LOG") 2>&1

TRIGGER="${CTI_TRIGGER:-timer}"

# record every finished run (any exit path past this point) for the dashboard
trap 'python3 "$ROOT/bin/runlog.py" "$TRIGGER" 2>/dev/null || true' EXIT

fail() {
  echo "[run] FAILED at stage: $1 (exit $2)"
  python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys, time
from pathlib import Path
Path("state/last_run.json").write_text(json.dumps({
    "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "decision": "error", "published": False,
    "error": f"stage '{sys.argv[1]}' exited {sys.argv[2]}",
}, indent=2))
PY
  python3 "$ROOT/bin/notify.py" "CTI run FAILED at '$1' (exit $2). Log: $LOG" high || true
  exit "$2"
}

echo "=== CTI run $TS ==============================================="

# --- pre-flight: agent-config integrity guard (opt-in) -----------------
# Detects the AGENTS.md/CLAUDE.md agent-hijack class (rogue ANTHROPIC_BASE_URL,
# injected instruction files in Downloads, broad Codex trust). OFF by default so
# it can never break a run on deploy; set AGENT_GUARD=1 to enable, and
# AGENT_GUARD_STRICT=1 to abort on a finding (default: warn + continue).
if [[ "${AGENT_GUARD:-0}" == "1" ]]; then
  echo "[run] pre-flight : agent-config guard"
  if ! python3 "$ROOT/bin/agent_guard.py" --alert; then
    if [[ "${AGENT_GUARD_STRICT:-0}" == "1" ]]; then
      fail agent_guard 2
    else
      echo "[run] agent_guard flagged indicators (non-strict: continuing; set AGENT_GUARD_STRICT=1 to abort)"
    fi
  fi
fi

echo "[run] stage 1/3 : ingest"
python3 "$ROOT/bin/ingest.py" || fail ingest $?

# Primary deterministic archive and durable retry run even on quiet harvests.
python3 "$ROOT/bin/advisory_pipeline.py" --candidates "$ROOT/state/candidates.json" \
  --output-dir "$ROOT/state/advisory-outbox" || fail advisory_build $?
python3 "$ROOT/bin/advisory_pipeline.py" --output-dir "$ROOT/state/advisory-outbox" --flush \
  || echo "[run] advisory delivery pending; retained for retry next run"
# AI only records complementary checks; it cannot gate or rewrite the archive.
if [[ "${CTI_AI_FACTCHECK_ENABLED:-1}" == "1" ]]; then
  python3 "$ROOT/bin/factcheck_advisories.py" --output-dir "$ROOT/state/advisory-outbox" \
    || echo "[run] optional fact check unavailable"
fi

# Nothing to consider? stop before spending an analyst call.
CAND_COUNT="$(python3 -c 'import json,sys; print(json.load(open("state/candidates.json"))["counts"]["candidates"])' 2>/dev/null || echo 0)"
if [[ "$CAND_COUNT" == "0" ]]; then
  echo "[run] 0 candidates — nothing for the analyst. Done."
  echo '{"run_at":"'"$TS"'","decision":"skip","reason":"no candidates from ingest"}' > "$ROOT/state/last_run.json"
  exit 0
fi

# Daily post cap. Once hit, we still run the analyst — a story scoring at or
# above publish.severity_override_score overrides the cap (publish stage enforces
# this). Below that, publish just holds it for review.
USED="$(python3 "$ROOT/bin/postlog.py" 2>/dev/null || echo 0)"
MAXDAY="$(python3 -c 'import yaml; print(yaml.safe_load(open("config/settings.yaml")).get("publish",{}).get("max_per_day",5))' 2>/dev/null || echo 5)"
SEVOR="$(python3 -c 'import yaml; print(yaml.safe_load(open("config/settings.yaml")).get("publish",{}).get("severity_override_score",90))' 2>/dev/null || echo 90)"
if [[ "$USED" -ge "$MAXDAY" ]]; then
  echo "[run] daily post cap reached ($USED/$MAXDAY in 24h) — only a story scoring >= $SEVOR will post this run."
fi
echo "[run] $CAND_COUNT candidates queued for the analyst  ($USED/$MAXDAY posts used today)"

# The primary decision is deterministic. Legacy engines remain available for
# manual review, but cannot replace source facts or become an availability gate.
echo "[run] stage 2/4 : deterministic source packaging"
touch "$ROOT/state/analyst_started"
python3 "$ROOT/bin/analyst_fallback.py" || fail deterministic_packaging $?

python3 - <<'PY' || fail decision_freshness $?
from pathlib import Path
p = Path("state/decision.json")
c = Path("state/candidates.json")
marker = Path("state/analyst_started")
if not p.exists() or p.stat().st_mtime_ns < max(c.stat().st_mtime_ns, marker.stat().st_mtime_ns):
    raise SystemExit("analyst did not produce a fresh decision")
PY

# Honeynet add-on: the weekly first-party brief from honeyintel joins this run's
# decision here, so the leak guard, validation, subscriber fan-out and every
# posting rule below treat it exactly like CTI output. Never fatal.
python3 "$ROOT/bin/honeynet_bridge.py" --merge "$ROOT/state/decision.json" \
  || echo "[run] honeynet bridge returned $? (non-fatal)"

# Model output is untrusted: drop any public post whose URL was not in this
# run's candidate set (or is missing from its own body). NON-FATAL — subscribers
# still get served; only the offending social posts are removed.
python3 - <<'PY' || fail validation $?
import json, sys
from pathlib import Path
sys.path.insert(0, "bin")
try:
    from decision_validation import sanitize_decision
    decision = json.loads(Path("state/decision.json").read_text())
    candidates = json.loads(Path("state/candidates.json").read_text())
    if not isinstance(decision, dict) or not isinstance(candidates, dict):
        raise ValueError("invalid decision/candidate shape")
    for field in ("posts", "alerts"):
        if field in decision and (not isinstance(decision[field], list) or any(not isinstance(x, dict) for x in decision[field])):
            raise ValueError(f"invalid {field}")
except Exception as exc:  # noqa: BLE001
    print(f"[run] validation failed ({exc}) — delivery stopped")
    raise SystemExit(1)
from decision_validation import drop_leaking_items
# Every engine, not only backups: web-verified Codex and Claude read hostile text too.
# Only the items carrying a secret are dropped; the rest still reach subscribers.
if isinstance(decision, dict):
    env = {}
    secret_file = Path("config/env")
    for line in (secret_file.read_text().splitlines() if secret_file.exists() else []):
        k, sep, v = line.partition("=")
        if sep and not k.strip().startswith("#"):
            env[k.strip()] = v.strip().strip("'\"")
    decision, leak_drops, residual = drop_leaking_items(decision, env)
    who = decision.get("analyst") or "claude"
    import subprocess
    if residual:
        # Names only — never echo the value into the run log.
        print(f"[run] SECURITY: {who} output contains config/env value(s) {residual} "
              f"outside any post/alert — discarding the whole decision (possible prompt injection)")
        Path("state/decision.json").write_text(json.dumps({
            "decision": "skip", "posts": [], "alerts": [], "analyst": decision.get("analyst"),
            "reason": "analyst output contained a secret — discarded"}, indent=2))
        subprocess.run([sys.executable, "bin/notify.py",
                        f"CTI SECURITY: analyst output contained secret(s) {residual}; decision discarded. Check candidates for prompt injection.",
                        "high"], check=False)
        raise SystemExit(0)
    if leak_drops:
        for d in leak_drops:
            print(f"[run] SECURITY: {who} item dropped — {d}")
        Path("state/decision.json").write_text(json.dumps(decision, indent=2))
        subprocess.run([sys.executable, "bin/notify.py",
                        f"CTI SECURITY: dropped {len(leak_drops)} analyst item(s) containing a secret; the rest were delivered. Check candidates for prompt injection. {leak_drops}",
                        "high"], check=False)
clean, dropped = sanitize_decision(decision, candidates)
for d in dropped:
    print(f"[run] validation dropped a public post: {d}")
if dropped:
    Path("state/decision.json").write_text(json.dumps(clean, indent=2))
PY
set -e

# Publish the free full feed before social summaries.
echo "[run] stage 3/4 : free public feed"
python3 "$ROOT/bin/push_to_worker.py" "$ROOT/state/decision.json" || fail public_feed $?

echo "[run] stage 4/4 : publish"
set +e
python3 "$ROOT/bin/publish_linkedin.py" --decision "$ROOT/state/decision.json"
PUB_RC=$?
set -e
case "$PUB_RC" in
  0) echo "[run] publish OK" ;;
  3|4) echo "[run] publish skipped (rc $PUB_RC — no fresh decision)" ;;
  5) echo "[run] publish: delivery failed — approved post(s) queued for manual (state/manual_post.md), operator notified — non-fatal" ;;
  *) fail publish "$PUB_RC" ;;
esac

# success ping (optional)
if python3 -c 'import json,sys; d=json.load(open("state/last_run.json")); sys.exit(0 if d.get("decision")=="post" and not d.get("dry_run") else 1)' 2>/dev/null; then
  ON_SUCCESS="$(python3 -c 'import yaml; print(yaml.safe_load(open("config/settings.yaml")).get("notify",{}).get("on_success"))' 2>/dev/null || echo None)"
  if [[ "$ON_SUCCESS" == "True" ]]; then
    TITLE="$(python3 -c 'import json;print(json.load(open("state/last_run.json")).get("title",""))')"
    python3 "$ROOT/bin/notify.py" "CTI posted to LinkedIn: $TITLE" default || true
  fi
fi

echo "=== done $(date -u +%Y%m%dT%H%M%SZ) ==========================="

# Run logs retained during the Codex handover; no automatic deletion here.
