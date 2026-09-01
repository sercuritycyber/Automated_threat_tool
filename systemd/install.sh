#!/usr/bin/env bash
# Install the CTI harvester timer + web panel as *user* systemd units (no root).
set -euo pipefail

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$UNIT_DIR"
cp "$SRC/cti-harvester.service" "$SRC/cti-harvester.timer" "$SRC/cti-panel.service" "$UNIT_DIR/"

systemctl --user daemon-reload
systemctl --user enable --now cti-harvester.timer
systemctl --user enable --now cti-panel.service

if ! loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
  echo
  echo "  To let the timer run while you're logged out:  sudo loginctl enable-linger $USER"
fi

echo
systemctl --user list-timers cti-harvester.timer --no-pager || true
echo
echo "  Control panel : http://127.0.0.1:8799"
echo "  Panel logs    : journalctl --user -u cti-panel.service -f"
echo "  Run manually  : systemctl --user start cti-harvester.service"
echo "  Harvest logs  : journalctl --user -u cti-harvester.service -f  (or ls ~/Threatintel/logs/)"
