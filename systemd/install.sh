#!/usr/bin/env bash
# Install the CTI harvester as a *user* systemd timer (no root needed).
set -euo pipefail

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$UNIT_DIR"
cp "$SRC/cti-harvester.service" "$UNIT_DIR/"
cp "$SRC/cti-harvester.timer"   "$UNIT_DIR/"

systemctl --user daemon-reload
systemctl --user enable --now cti-harvester.timer

# let the timer fire even when charlie is not logged in
if ! loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes'; then
  echo "Enabling linger so the timer runs while you're logged out:"
  echo "  sudo loginctl enable-linger $USER"
fi

echo
systemctl --user list-timers cti-harvester.timer --no-pager || true
echo
echo "Manual run : systemctl --user start cti-harvester.service"
echo "Live logs  : journalctl --user -u cti-harvester.service -f"
echo "Run logs   : ls -t ~/Threatintel/logs/"
