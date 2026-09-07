#!/usr/bin/env bash
# SecurityCyber marketing autoposter — one promo post to the social channels.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ -f config/env ]]; then set -a; . config/env; set +a; fi
mkdir -p logs
TS="$(date -u +%Y%m%dT%H%M%SZ)"
exec python3 bin/marketing_post.py 2>&1 | tee -a "logs/marketing-$TS.log"
