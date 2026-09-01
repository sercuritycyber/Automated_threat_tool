#!/usr/bin/env bash
# Install / update the Blue Team CTI stack as *user* systemd units (no root).
#
#   systemd/install.sh              install or update everything
#   systemd/install.sh status       show what's running
#   systemd/install.sh uninstall    stop and remove the units (keeps your data)
#
# Safe to re-run: it re-copies the units, reloads systemd, and restarts the
# panel so code changes take effect.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SRC/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(cti-harvester.service cti-harvester.timer cti-panel.service)

c()  { printf '\033[%sm%s\033[0m' "$1" "$2"; }
ok() { echo "  $(c '1;32' '✓') $*"; }
no() { echo "  $(c '1;31' '✗') $*"; }
hd() { echo; echo "$(c '1;36' "$*")"; }

# --------------------------------------------------------------- status / uninstall
if [[ "${1:-}" == "status" ]]; then
  systemctl --user --no-pager status "${UNITS[@]}" || true
  echo
  systemctl --user list-timers cti-harvester.timer --no-pager || true
  exit 0
fi

if [[ "${1:-}" == "uninstall" ]]; then
  systemctl --user disable --now cti-harvester.timer cti-panel.service 2>/dev/null || true
  systemctl --user stop cti-harvester.service 2>/dev/null || true
  for u in "${UNITS[@]}"; do rm -f "$UNIT_DIR/$u"; done
  systemctl --user daemon-reload
  ok "units removed (config/, state/, logs/ left untouched)"
  exit 0
fi

# --------------------------------------------------------------- preflight
hd "Preflight"

if command -v claude >/dev/null 2>&1; then
  ok "claude CLI: $(command -v claude)"
else
  no "claude CLI not on PATH — the analyst stage will fail. Install it first."
  exit 1
fi

MISSING="$(python3 - <<'PY'
import importlib.util
mods = {"fastapi": "fastapi", "uvicorn": "uvicorn", "multipart": "python-multipart",
        "requests": "requests", "yaml": "PyYAML"}
print(" ".join(pkg for imp, pkg in mods.items() if importlib.util.find_spec(imp) is None))
PY
)"
if [[ -n "$MISSING" ]]; then
  echo "  installing missing Python deps: $MISSING"
  python3 -m pip install --user -q $MISSING \
    || python3 -m pip install --break-system-packages -q $MISSING \
    || { no "pip install failed — run:  python3 -m pip install --user -r $ROOT/requirements.txt"; exit 1; }
fi
ok "python deps present (fastapi, uvicorn, python-multipart, requests, pyyaml)"

# validate the timer schedule before we install a broken one
ONCAL="$(grep -oP 'OnCalendar=\K.+' "$SRC/cti-harvester.timer" || true)"
if [[ -n "$ONCAL" ]] && command -v systemd-analyze >/dev/null 2>&1; then
  if systemd-analyze calendar "$ONCAL" >/dev/null 2>&1; then
    ok "timer schedule valid: $ONCAL"
  else
    no "cti-harvester.timer has an invalid OnCalendar ($ONCAL) — fix it before installing."
    exit 1
  fi
fi

# secrets file — run.sh sources it; make sure it exists and is locked down
if [[ ! -f "$ROOT/config/env" ]]; then
  printf '# LINKEDIN_ACCESS_TOKEN=...   (add via the panel -> Secrets)\n' > "$ROOT/config/env"
  chmod 600 "$ROOT/config/env"
  echo "  $(c '1;33' '!') created an empty config/env — add your LinkedIn token in the panel"
else
  chmod 600 "$ROOT/config/env"
  if grep -q '^LINKEDIN_ACCESS_TOKEN=..' "$ROOT/config/env"; then
    ok "LinkedIn token present in config/env"
  else
    echo "  $(c '1;33' '!') no LINKEDIN_ACCESS_TOKEN in config/env — add it in the panel -> Secrets"
  fi
fi

mkdir -p "$ROOT/state" "$ROOT/logs" "$ROOT/drafts"

# --------------------------------------------------------------- install
hd "Installing units -> $UNIT_DIR"
mkdir -p "$UNIT_DIR"
for u in "${UNITS[@]}"; do
  cp "$SRC/$u" "$UNIT_DIR/$u"
  ok "$u"
done

systemctl --user daemon-reload
systemctl --user enable --now cti-harvester.timer >/dev/null
ok "cti-harvester.timer enabled"
systemctl --user enable cti-panel.service >/dev/null
systemctl --user restart cti-panel.service
ok "cti-panel.service (re)started"

# linger — needed for the timer to fire while logged out
if loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
  ok "linger enabled — timer runs even when you're logged out"
else
  echo "  $(c '1;33' '!') linger is OFF — the timer only runs while you're logged in."
  echo "     enable it with:  sudo loginctl enable-linger $USER"
fi

# --------------------------------------------------------------- summary
hd "Done"
sleep 1
PANEL_STATE="$(systemctl --user is-active cti-panel.service || true)"
if [[ "$PANEL_STATE" == "active" ]]; then
  ok "control panel  -> http://127.0.0.1:8799"
else
  no "panel is '$PANEL_STATE' — check:  journalctl --user -u cti-panel.service -n 40 --no-pager"
fi
echo
systemctl --user list-timers cti-harvester.timer --no-pager || true
echo
echo "  Run a harvest now : systemctl --user start cti-harvester.service"
echo "  Harvest logs      : journalctl --user -u cti-harvester.service -f   (or ~/Threatintel/logs/)"
echo "  Panel logs        : journalctl --user -u cti-panel.service -f"
echo "  Status any time   : systemd/install.sh status"
