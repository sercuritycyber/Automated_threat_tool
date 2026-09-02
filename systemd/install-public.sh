#!/usr/bin/env bash
# Install the PUBLIC subscriber service: the site, the daily digest, and the
# weekly data-retention purge. Run systemd/install.sh first for the core.
#
#   systemd/install-public.sh            install / update
#   systemd/install-public.sh status
#   systemd/install-public.sh uninstall
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SRC/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(cti-public.service cti-digest.service cti-digest.timer cti-retention.service
       cti-retention.timer cti-backup.service cti-backup.timer)

c(){ printf '\033[%sm%s\033[0m' "$1" "$2"; }
ok(){ echo "  $(c '1;32' '✓') $*"; }
no(){ echo "  $(c '1;31' '✗') $*"; }
hd(){ echo; echo "$(c '1;36' "$*")"; }

if [[ "${1:-}" == "status" ]]; then
  systemctl --user --no-pager status cti-public.service 2>/dev/null || true
  echo; systemctl --user list-timers cti-digest.timer cti-retention.timer --no-pager || true
  echo; python3 "$ROOT/bin/selftest.py" 2>/dev/null || python3 "$ROOT/lib/subsconfig.py" | sed -n '/blockers/,$p'
  exit 0
fi

if [[ "${1:-}" == "uninstall" ]]; then
  systemctl --user disable --now "${UNITS[@]}" 2>/dev/null || true
  for u in "${UNITS[@]}"; do rm -f "$UNIT_DIR/$u"; done
  systemctl --user daemon-reload
  ok "public units removed (subscriber data in state/ is untouched)"
  exit 0
fi

hd "Preflight"
python3 - <<'PY' || { no "python deps"; exit 1; }
import importlib.util as u
missing = [m for m in ("fastapi","uvicorn","requests","yaml") if u.find_spec(m) is None]
raise SystemExit(1 if missing else 0)
PY
ok "python deps present"

python3 "$ROOT/bin/selftest_subs.py"  >/dev/null && ok "subscriber store self-test passes" || no "selftest_subs FAILED"
python3 "$ROOT/lib/subsconfig.py" | sed -n '/^blockers:/,$p' | sed 's/^/  /'

hd "Installing → $UNIT_DIR"
mkdir -p "$UNIT_DIR"
for u in "${UNITS[@]}"; do cp "$SRC/$u" "$UNIT_DIR/$u"; ok "$u"; done
systemctl --user daemon-reload
systemctl --user enable --now cti-public.service
systemctl --user enable --now cti-digest.timer cti-retention.timer cti-backup.timer
ok "services enabled"
sleep 1
if [[ "$(systemctl --user is-active cti-public.service)" == active ]]; then
  ok "public site is up on 127.0.0.1:8080"
  curl -fsS -m 4 http://127.0.0.1:8080/healthz >/dev/null && ok "/healthz OK" || no "/healthz not responding yet"
else
  no "cti-public.service is not active — journalctl --user -u cti-public.service -n 40"
fi

hd "Still to do (once, before going live)"
cat <<EOF
  1. DNS: point cti.securitycyber.uk at this host.
  2. TLS: put systemd/Caddyfile in /etc/caddy/, set the domain + your email,
     then  sudo systemctl reload caddy   (Caddy fetches the cert automatically).
  3. Stripe: create one Price per tier, set subscriptions.tiers.<t>.price_id,
     and put STRIPE_SECRET_KEY / STRIPE_PUBLISHABLE_KEY in config/env.
  4. Stripe webhook: add https://cti.securitycyber.uk/webhooks/stripe in the
     Stripe dashboard; copy the signing secret to STRIPE_WEBHOOK_SECRET.
  5. Email: set EMAIL_SMTP_* + subscriptions.from_email (use SES/Resend/Postmark).
  6. Telegram (optional): create a bot with @BotFather, set TELEGRAM_BOT_TOKEN
     + TELEGRAM_BOT_USERNAME, then
       curl "https://api.telegram.org/bot\$TELEGRAM_BOT_TOKEN/setWebhook?url=https://cti.securitycyber.uk/webhooks/telegram&secret_token=\$TELEGRAM_WEBHOOK_SECRET"
  7. ICO: register (ico.org.uk), put the number in subscriptions.ico_registration.
  8. Fill subscriptions.company_legal_name / contact_email, then read /privacy
     and /terms and adjust.
  9. Flip subscriptions.enabled: true. Keep subscriptions.live: false and use
     sk_test_ keys until a full test checkout works, then switch to sk_live_.

  Check readiness any time:  python3 lib/subsconfig.py
EOF
