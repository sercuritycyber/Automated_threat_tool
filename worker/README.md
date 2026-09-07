# CTI Alerts — Cloudflare Worker

The public subscription service for the SecurityCyber CTI alert feed. Same
deploy model as securitycyber.uk: code in GitHub → push → Cloudflare builds.

- **Worker name:** `cti-alerts` (set in `wrangler.toml`)
- **Deploy:** `npx wrangler deploy` **from this `worker/` directory**, or use the
  Cloudflare build settings with root directory `worker/` and deploy command
  `npx wrangler deploy`. The Version command should be blank; if Cloudflare
  requires one, use `npx wrangler versions upload` from the `worker/` directory.
- **Custom domain:** don't hand-add a DNS record. Cloudflare dashboard →
  Workers & Pages → `cti-alerts` → Settings → **Domains & Routes → Add → Custom
  Domain** → `cti.securitycyber.uk`. That creates and proxies the DNS record for you.
- **Route:** `cti.securitycyber.uk/*`
- **Storage:** D1 (`cti-subs`) — all subscriber PII
- **Email:** Resend
- **Payments:** Stripe hosted Checkout + Customer Portal (no card data here)
- **Cron:** daily digest + weekly retention (see `wrangler.toml`)

The Python pipeline stays on Charlie's box; after each harvest it POSTs the
posts to `POST /internal/ingest` (bin/push_to_worker.py). The owner panel
(`web/app.py` → Subscribers) reads/writes via `/admin/*`.

The Worker has an intentionally empty `public/` assets directory for Cloudflare
build compatibility. Application routes remain handled by `src/index.js`.

```
src/index.js     router + fetch()/scheduled() + delivery fan-out + /admin API
src/config.js    wrangler.toml vars + CONFIG json  (tiers, prices, limits)
src/db.js        D1 data layer  (schema in schema.sql)
src/channels.js  email/discord/slack/telegram/webhook/ntfy senders
src/billing.js   Stripe REST + webhook verify (Web Crypto)
src/tokens.js    signed subscriber links (HMAC-SHA256)
src/pages.js     branded HTML  (SecurityCyber dark theme)
src/legal.js     privacy / terms / cookies  (mirror of docs/legal/*.md)
src/util.js      helpers
test/run.mjs     pure-logic unit checks   (node test/run.mjs)
test/flow.mjs    end-to-end vs `wrangler dev`   (node test/flow.mjs)
```

## First-time setup

```bash
cd worker
npm install                                  # wrangler

npx wrangler login                            # once, in a browser

# 1. database
npx wrangler d1 create cti-subs               # copy the id into wrangler.toml
npx wrangler d1 execute cti-subs --remote --file schema.sql

# 2. secrets
npx wrangler secret put STRIPE_SECRET_KEY       # sk_test_… (then sk_live_… at go-live)
npx wrangler secret put STRIPE_WEBHOOK_SECRET   # from the Stripe dashboard webhook
npx wrangler secret put RESEND_API_KEY          # re_…
npx wrangler secret put SUBS_TOKEN_SECRET       # 32+ random bytes
npx wrangler secret put INGEST_SECRET           # 32+ random bytes — also CTI_INGEST_SECRET in config/env
npx wrangler secret put ADMIN_TOKEN             # 32+ random bytes — also CTI_ADMIN_TOKEN in config/env
# optional
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_BOT_USERNAME
npx wrangler secret put TELEGRAM_WEBHOOK_SECRET

# 3. vars — edit wrangler.toml [vars]:
#    COMPANY_LEGAL_NAME, ICO_REG, and the CONFIG JSON (paste your Stripe price ids
#    into tiers.<t>.monthly_price_id / weekly_price_id)

# 4. DNS: the custom_domain route in wrangler.toml creates the proxied
#    cti.securitycyber.uk record during deployment.

# 5. deploy   (ALWAYS run from this worker/ directory)
npx wrangler deploy
#    Cloudflare Workers Builds can use root directory worker/ and the deploy command above.
#    The Version command should be blank unless configured to run from worker/.

# 6. Stripe webhook endpoint:
#    https://cti.securitycyber.uk/webhooks/stripe   → copy signing secret to STRIPE_WEBHOOK_SECRET
# 7. Telegram webhook (if used):
#    curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://cti.securitycyber.uk/webhooks/telegram&secret_token=<TELEGRAM_WEBHOOK_SECRET>"

# 8. On Charlie's box, config/env:
#    CTI_WORKER_URL=https://cti.securitycyber.uk
#    CTI_INGEST_SECRET=<same as the Worker's INGEST_SECRET>
#    CTI_ADMIN_TOKEN=<same as the Worker's ADMIN_TOKEN>
```

Readiness at any time: the owner panel `/subscriptions` page shows the Worker's
`problems` list. Keep Stripe in test mode (`CONFIG.live=false` + `sk_test_`)
until a full test checkout works, then set `live=true` and the `sk_live_` key.

Compliance checklist: `../docs/legal/compliance-runbook.md`.

## Local dev

```bash
cp .dev.vars.example .dev.vars     # fill in test values
npx wrangler d1 execute cti-subs --local --file schema.sql
npx wrangler dev --local           # http://localhost:8787
node test/run.mjs                  # unit checks
node test/flow.mjs                 # full lifecycle vs the running dev server
```

## One-off threat brief

`/brief` sells a single analyst-written report as a **one-time payment**
(Stripe Checkout `mode: payment`), separate from the subscription. Price:
`CONFIG.brief` (£49 default) or `BRIEF_PRICE_ID`. On
`checkout.session.completed` (payment mode) the Worker records the purchase in
`purchases`, emails a receipt, and pings the owner. Deliver + mark done in the
panel → **Purchases**.

## Routes

Public: `/` `/subscribe` `/welcome` `/brief` `/brief/thanks` `/confirm`
`/manage` (+ `/manage/*`) `/unsubscribe` `/channel/telegram`
`/privacy` `/terms` `/cookies` `/logo.svg` `/healthz` `/robots.txt`
POST: `/subscribe` `/brief/buy` `/account`
Webhooks: `/webhooks/stripe` `/webhooks/telegram`
Internal (bearer `INGEST_SECRET`): `POST /internal/ingest`
Admin (bearer `ADMIN_TOKEN`): `/admin/stats`, `/admin/subscribers[?status=&q=]`,
`/admin/subscribers/{id}`, `/admin/subscribers/{id}/{export|edit|erase|channel|test}`,
`/admin/add`, `/admin/purchases`, `/admin/purchases/{session_id}/fulfilled`
