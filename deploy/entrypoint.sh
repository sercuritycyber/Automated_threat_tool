#!/usr/bin/env bash
# Container entrypoint for the Threatintel image.
#
# Its only job is to make `claude -p` usable headless: Claude Code blocks on a
# one-time folder-trust prompt the first time it runs in a directory, and there
# is no TTY here to answer it. We pre-accept trust for the working directory in
# the (bind-mounted) ~/.claude.json before handing off to the real command.
set -euo pipefail

CLAUDE_JSON="${HOME}/.claude.json"

if [[ -w "$CLAUDE_JSON" || ( ! -e "$CLAUDE_JSON" && -w "$(dirname "$CLAUDE_JSON")" ) ]]; then
  python3 - "$CLAUDE_JSON" "$PWD" <<'PY' || echo "[entrypoint] could not seed claude trust (non-fatal)"
import json, os, sys

path, project = sys.argv[1], sys.argv[2]
try:
    with open(path) as fh:
        data = json.load(fh)
except (OSError, ValueError):
    data = {}

proj = data.setdefault("projects", {}).setdefault(project, {})
if not proj.get("hasTrustDialogAccepted"):
    proj["hasTrustDialogAccepted"] = True
    proj.setdefault("hasCompletedProjectOnboarding", True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)
    print(f"[entrypoint] marked {project} trusted for claude")
PY
else
  echo "[entrypoint] ${CLAUDE_JSON} not writable — skipping claude trust seed"
fi

exec "$@"
