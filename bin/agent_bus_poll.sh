#!/usr/bin/env bash
# agent_bus_poll.sh — wake an agent when the other one has left it work.
#
# Runs on a timer. Checks ~/agent-bus/to-<agent>/todo/ ; if there is a task,
# invokes the agent headless with a constrained prompt to work it. If the bus
# is remote (laptop), rsyncs it first and pushes results back after.
#
#   AGENT=claude|antigravity   which side this poller is
#   BUS=~/agent-bus            local bus dir
#   BUS_REMOTE=user@host:path  optional — canonical bus to sync with (laptop → VM)
#   AGENT_CMD="..."            override the invocation (needed for antigravity)
#
# Exit 0 always unless the agent invocation itself errors.
set -uo pipefail

AGENT="${AGENT:-claude}"
BUS="${BUS:-$HOME/agent-bus}"
BUS_REMOTE="${BUS_REMOTE:-}"
MAX_TASKS="${MAX_TASKS:-3}"
LOG="$BUS/log"; mkdir -p "$LOG"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
say() { echo "$(ts) [$AGENT] $*" | tee -a "$LOG/poll-$AGENT.log" >&2; }

exec 9>"$BUS/.poll-$AGENT.lock"
flock -n 9 || { say "another poll is running — skip"; exit 0; }

# 1. sync down (laptop pulls the canonical bus from the VM)
if [[ -n "$BUS_REMOTE" ]]; then
  rsync -az --update -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=no" \
    "$BUS_REMOTE/" "$BUS/" 2>>"$LOG/poll-$AGENT.log" || say "rsync down failed (continuing with local copy)"
fi

# 2. any work?
mkdir -p "$BUS/to-$AGENT/todo" "$BUS/to-$AGENT/doing" "$BUS/to-$AGENT/done"
mapfile -t TASKS < <(find "$BUS/to-$AGENT/todo" -maxdepth 1 -name '*.md' -type f | sort | head -n "$MAX_TASKS")
if [[ ${#TASKS[@]} -eq 0 ]]; then
  exit 0   # nothing to do — quiet
fi
say "found ${#TASKS[@]} task(s): $(basename -a "${TASKS[@]}" | paste -sd, -)"

# 3. invoke the agent
PROMPT_FILE="$BUS/PROMPT-$AGENT.md"
[[ -f "$PROMPT_FILE" ]] || { say "no $PROMPT_FILE — cannot invoke"; exit 0; }

RC=0
if [[ -n "${AGENT_CMD:-}" ]]; then
  # antigravity or a custom runner; it must read PROMPT_FILE itself
  ( cd "$HOME/Threatintel" && AGENT_BUS="$BUS" bash -c "$AGENT_CMD" ) < /dev/null \
    >>"$LOG/poll-$AGENT.log" 2>&1 || RC=$?
elif [[ "$AGENT" == "claude" ]] && command -v claude >/dev/null; then
  ( cd "$HOME/Threatintel" && AGENT_BUS="$BUS" \
      claude -p "$(cat "$PROMPT_FILE")" \
        --allowedTools "Read,Write,Edit,Bash,Glob,Grep" \
        --permission-mode acceptEdits ) < /dev/null \
    >>"$LOG/poll-$AGENT.log" 2>&1 || RC=$?
else
  say "no runner for agent '$AGENT' (set AGENT_CMD)"; exit 0
fi
say "agent run finished rc=$RC"

# 4. sync back
if [[ -n "$BUS_REMOTE" ]]; then
  rsync -az --update -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=no" \
    "$BUS/" "$BUS_REMOTE/" 2>>"$LOG/poll-$AGENT.log" || say "rsync up failed"
fi
exit 0
