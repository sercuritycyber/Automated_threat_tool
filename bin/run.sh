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

echo "[run] stage 1/3 : ingest"
python3 "$ROOT/bin/ingest.py" || fail ingest $?

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

# --- Analyst engine — config/settings.yaml -> analyst.engine ------------
#   claude       claude -p (full verified analyst), then Antigravity (Gemini)
#                as the sole backup, then the deterministic fallback. This is
#                the default.
#   antigravity  Antigravity (Gemini) first, then deterministic.
ENGINE="$(python3 - <<'PYEOF'
import pathlib
try:
    import yaml
    s = yaml.safe_load(pathlib.Path("config/settings.yaml").read_text()) or {}
    a = s.get("analyst") or {}
    print((a.get("engine") or "claude").strip().lower())
except Exception:
    print("claude")
PYEOF
)"
echo "[run] stage 2/4 : analyst (engine=$ENGINE)"

run_claude_analyst() {
  claude -p "$(cat "$ROOT/prompts/analyst.md")" \
    --allowedTools "Read,Write,WebFetch" --permission-mode acceptEdits
}

set +e
ANALYST_RC=1
case "$ENGINE" in
  antigravity)
    python3 "$ROOT/bin/analyst_antigravity.py"; ANALYST_RC=$?
    ;;
  claude|*)
    run_claude_analyst; ANALYST_RC=$?
    if [[ "$ANALYST_RC" -ne 0 ]]; then
      echo "[run] claude analyst exited $ANALYST_RC — trying Antigravity (Gemini) backup"
      python3 "$ROOT/bin/analyst_antigravity.py"; ANALYST_RC=$?
    fi
    ;;
esac
set -e
if [[ "$ANALYST_RC" -ne 0 ]]; then
  echo "[run] all analyst engines failed ($ANALYST_RC) — deterministic fallback for subscribers"
  # Without this, a lapsed claude login stops public posting with no signal —
  # subscribers still get unverified alerts, so nothing else looks broken.
  python3 "$ROOT/bin/notify.py" "CTI analyst unavailable (exit $ANALYST_RC) — ran deterministic fallback: unverified subscriber alerts only, no public post. Check 'claude -p' auth on the VM. Log: $LOG" high || true
  python3 "$ROOT/bin/analyst_fallback.py" || echo "[run] analyst_fallback returned $? (non-fatal)"
fi

# Model output is untrusted: drop any public post whose URL was not in this
# run's candidate set (or is missing from its own body). NON-FATAL — subscribers
# still get served; only the offending social posts are removed.
set +e
python3 - <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "bin")
try:
    from decision_validation import sanitize_decision
    decision = json.loads(Path("state/decision.json").read_text())
    candidates = json.loads(Path("state/candidates.json").read_text())
except Exception as exc:  # noqa: BLE001
    print(f"[run] validation skipped ({exc}) — decision left as-is")
    raise SystemExit(0)
clean, dropped = sanitize_decision(decision, candidates)
for d in dropped:
    print(f"[run] validation dropped a public post: {d}")
if dropped:
    Path("state/decision.json").write_text(json.dumps(clean, indent=2))
PY
set -e

# Stage 3: paying subscribers FIRST. They are buying the lead time as much as
# the analysis, so the people who pay hear it before it is given away publicly.
# Never fatal: a fan-out problem must not stop the brand posting.
echo "[run] stage 3/4 : subscriber alerts"
python3 "$ROOT/bin/push_to_worker.py" "$ROOT/state/decision.json" || echo "[run] push_to_worker returned $? (non-fatal)"

# Optional head start before the same story goes out free on socials.
LEAD_MIN="${CTI_SUBSCRIBER_LEAD_MINUTES:-0}"
if [[ "$LEAD_MIN" =~ ^[0-9]+$ ]] && (( LEAD_MIN > 0 )); then
  echo "[run] holding the public post ${LEAD_MIN}m so subscribers stay ahead"
  sleep $((LEAD_MIN * 60))
fi

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

# Legacy local delivery — only if the Worker isn't configured.
if [[ -z "${CTI_WORKER_URL:-}" ]]; then
  python3 "$ROOT/bin/deliver.py" --instant --from "$ROOT/state/decision.json" || echo "[run] deliver returned $? (non-fatal)"
fi

# success ping (optional)
if python3 -c 'import json,sys; d=json.load(open("state/last_run.json")); sys.exit(0 if d.get("decision")=="post" and not d.get("dry_run") else 1)' 2>/dev/null; then
  ON_SUCCESS="$(python3 -c 'import yaml; print(yaml.safe_load(open("config/settings.yaml")).get("notify",{}).get("on_success"))' 2>/dev/null || echo None)"
  if [[ "$ON_SUCCESS" == "True" ]]; then
    TITLE="$(python3 -c 'import json;print(json.load(open("state/last_run.json")).get("title",""))')"
    python3 "$ROOT/bin/notify.py" "CTI posted to LinkedIn: $TITLE" default || true
  fi
fi

echo "=== done $(date -u +%Y%m%dT%H%M%SZ) ==========================="

# keep only the last 60 run logs
ls -1t "$ROOT"/logs/run-*.log 2>/dev/null | tail -n +61 | xargs -r rm -f
