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

# The analyst reads hostile feed text and fetches arbitrary article URLs, so a
# prompt injection is in scope. config/claude-untrusted.json hard-denies reading
# secrets (/proc included — config/env is exported into this env) and editing
# anything but state/. Deny rules beat --allowedTools and acceptEdits.
run_claude_analyst() {
  claude -p "$(cat "$ROOT/prompts/analyst.md")" \
    --allowedTools "Read,Write,WebFetch" --permission-mode acceptEdits \
    --settings "$ROOT/config/claude-untrusted.json" "$@"
}

# API bridge: when the Claude subscription is unavailable (lapsed, or a session
# limit), spend prepaid API credits on at most ONE analyst run per day — the
# first after 07:00, so it feeds the 08:15 digest. Every other run takes the
# free deterministic fallback. Only active while ANTHROPIC_API_KEY_BRIDGE is in
# config/env; it is deliberately not named ANTHROPIC_API_KEY, which would make
# every run bill the API even while the subscription works.
BRIDGE_MAX_USD="${BRIDGE_MAX_USD:-5}"
BRIDGE_STAMP="$ROOT/state/api_bridge_last_run"
BRIDGE_EXPECTED_FALLBACK=0
run_bridge_analyst() {
  [[ -n "${ANTHROPIC_API_KEY_BRIDGE:-}" ]] || return 1
  local today; today="$(date +%F)"
  if [[ "$(cat "$BRIDGE_STAMP" 2>/dev/null)" == "$today" || "$(date +%-H)" -lt 7 ]]; then
    echo "[run] API bridge: today's run already used (or before 07:00) — free fallback"
    BRIDGE_EXPECTED_FALLBACK=1
    return 1
  fi
  echo "$today" > "$BRIDGE_STAMP"   # stamp first: a failed attempt still counts
  echo "[run] subscription unavailable — API bridge run (1/day, cap \$$BRIDGE_MAX_USD)"
  ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY_BRIDGE" run_claude_analyst --max-budget-usd "$BRIDGE_MAX_USD"
}

set +e
ANALYST_RC=1
case "$ENGINE" in
  codex)
    python3 "$ROOT/bin/analyst_codex.py"; ANALYST_RC=$?
    if [[ "$ANALYST_RC" -ne 0 ]]; then
      echo "[run] Codex exited $ANALYST_RC — trying the Claude subscription"
      run_claude_analyst; ANALYST_RC=$?
    fi
    # No paid API bridge on the Codex route. Keep subscriber-only deterministic
    # fallback below if the subscription is unavailable.
    ;;
  antigravity)
    python3 "$ROOT/bin/analyst_antigravity.py"; ANALYST_RC=$?
    ;;
  claude|*)
    run_claude_analyst; ANALYST_RC=$?
    if [[ "$ANALYST_RC" -ne 0 && -n "${ANTHROPIC_API_KEY_BRIDGE:-}" ]]; then
      run_bridge_analyst; ANALYST_RC=$?
    fi
    if [[ "$ANALYST_RC" -ne 0 ]]; then
      echo "[run] claude analyst exited $ANALYST_RC — trying Codex (ChatGPT plan) backup"
      python3 "$ROOT/bin/analyst_codex.py"; ANALYST_RC=$?
    fi
    if [[ "$ANALYST_RC" -ne 0 ]]; then
      echo "[run] Codex unavailable — trying Antigravity (Gemini) backup"
      python3 "$ROOT/bin/analyst_antigravity.py"; ANALYST_RC=$?
    fi
    if [[ "$ANALYST_RC" -ne 0 ]]; then
      echo "[run] Gemini backup unavailable — trying OpenAI-compatible providers"
      python3 "$ROOT/bin/analyst_openai_compat.py"; ANALYST_RC=$?
    fi
    ;;
esac
set -e
if [[ "$ANALYST_RC" -ne 0 ]]; then
  echo "[run] all analyst engines failed ($ANALYST_RC) — deterministic fallback for subscribers"
  # Without this, a lapsed claude login stops public posting with no signal —
  # subscribers still get unverified alerts, so nothing else looks broken.
  # In bridge mode the fallback is expected on all but one run a day: stay quiet.
  if [[ "$BRIDGE_EXPECTED_FALLBACK" -eq 1 ]]; then
    echo "[run] bridge mode: expected fallback, no alert"
  else
    python3 "$ROOT/bin/notify.py" "CTI analyst unavailable (exit $ANALYST_RC) — ran deterministic fallback: unverified subscriber alerts only, no public post. Check $ENGINE authentication on the VM. Log: $LOG" high || true
  fi
  python3 "$ROOT/bin/analyst_fallback.py" || fail analyst_fallback $?
fi

# Honeynet add-on: the weekly first-party brief from honeyintel joins this run's
# decision here, so the leak guard, validation, subscriber fan-out and every
# posting rule below treat it exactly like CTI output. Never fatal.
python3 "$ROOT/bin/honeynet_bridge.py" --merge "$ROOT/state/decision.json" \
  || echo "[run] honeynet bridge returned $? (non-fatal)"

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
from decision_validation import drop_leaking_items
# Every engine, not only backups: web-verified Codex and Claude read hostile text too.
# Only the items carrying a secret are dropped; the rest still reach subscribers.
if isinstance(decision, dict):
    env = {}
    for line in Path("config/env").read_text().splitlines():
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

# Run logs retained during the Codex handover; no automatic deletion here.
