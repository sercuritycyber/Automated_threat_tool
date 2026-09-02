# BUILD TRACKER — CTI Alerts Cloudflare Worker

Port the paid subscription service to a **Cloudflare Worker** so it deploys the
same way securitycyber.uk does: code in GitHub → push → Cloudflare auto-builds.

**Why a Worker (not a box):** securitycyber.uk already runs this way (Worker +
D1 + Resend, `git push` = deploy). No server to run/expose. The Python pipeline
stays on Charlie's box; its only new job is to POST each run's posts to the
Worker.

**Working rule:** update this file every session. Commit code + tracker together
in the `Threatintel` repo (push access via `threatintel-deploy` key).

Legend: `[ ]` todo · `[~]` wip · `[x]` done+tested · `[!]` blocked

---

## RESUME HERE
> **W0–W7 all done + tested.** `node worker/test/run.mjs` (26/26) +
> `node worker/test/flow.mjs` vs `wrangler dev` (19/19). Panel ↔ Worker
> admin API verified. Committed + pushed.
> Owner to do (Cloudflare account side — see worker/README.md): `wrangler login`,
> `d1 create cti-subs` + paste id, `secret put` each, edit wrangler.toml vars
> (legal name / ICO / CONFIG price ids), add DNS `cti`, deploy, wire Stripe +
> Telegram webhooks, set CTI_* in config/env on the box.

## Decisions (locked)
- **Own Worker**, route `cti.securitycyber.uk/*` in the `securitycyber.uk` zone.
  Does NOT touch the marketing `worker.js` (repo SecurityCyber.uk — "don't touch").
- Lives in **this repo** at `worker/`. User connects it to Cloudflare Workers
  Builds (or `cd worker && npx wrangler deploy`).
- **D1** (own database `cti-subs`) replaces Turso/SQLite. Schema ports 1:1
  (both are SQLite).
- **Resend** (HTTP) replaces SMTP — same provider the marketing site uses.
- Plain **ES-module JavaScript**, split files, no build step / no TS toolchain
  (matches their worker.js). Wrangler bundles.
- **Cron Triggers** for the daily digest + weekly retention (no systemd timers).
- Web Crypto (`crypto.subtle`) for Stripe HMAC verify + signed subscriber tokens.
- Pipeline → Worker: `bin/push_to_worker.py` POSTs `state/decision.json` posts to
  `POST /internal/ingest` (shared secret `INGEST_SECRET`). Worker owns all
  subscriber state + fan-out.
- Owner admin: `web/app.py` `/subscribers` talks to the Worker's `/admin/*` API
  (bearer `ADMIN_TOKEN`). The local Python subscriber DB / `bin/deliver.py` are
  retired for production (kept for local-dev reference).
- The Python `web/public_app.py` stays as a reference impl + `wrangler`-free
  local dev; not deployed.

## Config / secrets
`worker/wrangler.toml [vars]`: BRAND, SITE_URL, FROM_EMAIL, CONTACT_EMAIL,
COMPANY_LEGAL_NAME, ICO_REG, plus `CONFIG` (JSON: tiers, prices, retention,
rate limit).
Secrets (`npx wrangler secret put`): STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
RESEND_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME, TELEGRAM_WEBHOOK_SECRET,
SUBS_TOKEN_SECRET, INGEST_SECRET, ADMIN_TOKEN.

---

## PHASES

### W0 — scaffold  ✅
- [x] `worker/wrangler.toml` (route, D1 binding, crons, vars)
- [x] `worker/package.json` (wrangler devDep, scripts)
- [x] `worker/schema.sql` (D1 migrations — port lib/subscribers.py)
- [x] `worker/.dev.vars.example`
- [x] `worker/src/config.js` (defaults + CONFIG var merge)
- [x] `worker/README.md` (create D1, put secrets, deploy, connect Stripe/Telegram)
- [x] `.gitignore`: `worker/node_modules`, `worker/.wrangler`, `worker/.dev.vars`

### W1 — core libs  ✅
- [x] `worker/src/util.js` — html(), esc(), json(), rateLimit(), clientIp(), redirect()
- [x] `worker/src/db.js` — d1 helpers: get/all/run, migrate(), the subscriber CRUD
      (subscribers, channels, prefs, consents, deliveries, events, stripe_events,
      signup_intents, kv), DSAR export/erase, retention purge, stats
- [x] `worker/src/tokens.js` — sign()/verify() HMAC-SHA256 via Web Crypto, newId()
- [x] node test harness: tokens round-trip, db against `wrangler dev --local`

### W2 — channels  ✅
- [x] `worker/src/channels.js` — send(kind,target,subject,body): email (Resend),
      discord, slack, telegram, webhook (signed), ntfy; verifyTarget()

### W3 — billing  ✅
- [x] `worker/src/billing.js` — createCheckoutSession(tier,cadence,email,ref),
      verifyWebhook(payload,sig,secret) [Web Crypto], handleEvent(event,env),
      portalSession(), cancelSubscription(), priceToTier()
- [x] idempotency via stripe_events

### W4 — pages + router  ✅
- [x] `worker/src/pages.js` — branded layout (SecurityCyber dark theme, Syne/DM
      Sans, logo mark), landing, /subscribe form, /manage, welcome, legal (inline
      HTML ported from docs/legal/*.md), 404
- [x] `worker/src/index.js` — fetch() router:
      GET / · GET|POST /subscribe · GET /welcome · POST /webhooks/stripe ·
      GET /confirm · GET /manage + POST /manage/* · GET /unsubscribe ·
      GET /channel/telegram · POST /webhooks/telegram · GET /privacy /terms
      /cookies · GET /logo.svg · GET /healthz · GET /robots.txt ·
      POST /internal/ingest · /admin/* (bearer)
- [x] security headers, no cookies, rate limits

### W5 — delivery + cron  ✅
- [x] instant fan-out on /internal/ingest (per subscriber tier/vendor/dedupe)
- [x] `scheduled()` handler: 08:15 digest, 03:30 Sun retention + intent purge
- [x] posts_log kept in D1 kv/table for the digest window

### W6 — Python side  ✅
- [x] `bin/push_to_worker.py` — POST decision.json posts to /internal/ingest
- [x] `bin/run.sh` stage 4 → call push_to_worker.py instead of deliver.py
      (deliver.py kept, no longer wired)
- [x] `web/app.py` /subscribers → Worker /admin API (list/get/edit/erase/export)
- [x] `bin/selftest.py` — add Worker reachability + config checks

### W7 — docs  ✅
- [x] `worker/README.md` full runbook
- [x] README.md — replace "run from a box" with the Worker deploy
- [x] docs/BUILD.md — note the Worker supersedes web/public_app.py for prod
- [x] docs/legal/compliance-runbook.md — D1/Resend/Cloudflare in the processor list
- [x] memory file

## TEST
```
cd worker && npx wrangler dev --local        # local Miniflare + D1
cd worker && npx wrangler d1 execute cti-subs --local --file schema.sql
node worker/test/run.mjs                      # crypto/token/billing unit checks
```

## BLOCKERS
- Cloudflare account: user runs `wrangler login` / connects the repo in the
  dashboard, creates the D1 (`wrangler d1 create cti-subs`), adds the DNS record
  `cti` → proxied, and `wrangler secret put` for each secret. I can't do the
  account side.
