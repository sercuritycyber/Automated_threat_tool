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
> **W0–W7 done + tested**, plus the one-off threat brief.
>
> **W9 — "why pay": make the paid feed FULL (2026-09-03).** The live feed
> already carried depth (every item has `subscriber_text`), but too little
> volume — a run's `alerts[]` was ~15 items because `ingest.py`'s pre-score is
> CVE/KEV-centric and quietly floored real CTI with no CVE number (breaches,
> published exploits, malware campaigns, botnet takedowns, ICS patches).
> Changes:
> - `bin/ingest.py` — pre-score base 8→11×weight; big expansion of positive
>   signals (breach/intrusion, malware family, named campaign/APT, published
>   exploit/PoC/Metasploit, package ecosystem, LE takedown, ICS/OT, more
>   vendors) and negatives (virtual events, podcasts, explainers, corporate
>   PR). `max_candidates` 40→80. Result on a normal run: ~30/50 candidates
>   now clear the subscriber floor, up from ~17.
> - `config/settings.yaml` — explicit `scoring.subscriber_min_score: 10`
>   (was an invisible default of 15).
> - `prompts/analyst.md` — `alerts[]` mandate rewritten: "this is where the
>   subscription earns its money… turn MOST of the candidate list into alerts
>   (15-30 normal, 30-40 busy)". Cap 30→40. `analyst_fallback.py` cap 30→40.
> - Worker `rssFeed`: 50→120 items shown, `postsForFeed` 120→300,
>   `trimPostsLog` 500→2000 (deeper backlog in the reader).
> - Worker digest email: capped at `digest_max_items` (25) top-by-score with
>   "the other N are in your feed", so a 100-item day doesn't produce an
>   unreadable email. JSON attachment still carries the full set.
>
> **W8 — subscription-engine hardening (2026-09-03).** Closed gaps a paying
> subscriber would hit:
> - **EMAIL SUBSCRIBERS GOT NOTHING (critical).** The email channel row is
>   auto-created by `createPending` with `verified=0` and nothing ever set it
>   to 1 (the confirm loop explicitly `continue`s past email). `deliverInstant`
>   /`deliverDigest` filter `c.verified`, so every email subscriber — the
>   default, most common choice — received zero alerts, forever. Fix: the
>   email row is labelled `account` (identifier) vs `signup` (chosen delivery,
>   set by `billing.handleEvent`, incl. the no-intent default); `signup` rows
>   are verified on confirm; `deliverableChannels()` centralises the rule with
>   a legacy net for pre-label rows that have no other verified channel.
>   Regression tests 6n/6o in flow.mjs.
> - **Paid-but-unconfirmed is never deleted.** `db.purgeUnconfirmed` now
>   excludes any row with a Stripe customer/subscription id (matches the
>   privacy policy's "never confirmed / never paid" wording). New
>   `db.paidUnconfirmed()`; retention cron pages the owner about them; the
>   daily digest cron re-sends the confirm email up to 3× (`confirm_resend:<id>`
>   kv). Previously: Stripe kept billing while our record was erased after 7d.
> - **Opening any emailed link completes the opt-in.** `activateSubscriber()`
>   is shared by `/confirm` and `/manage`; a lost /confirm email no longer
>   strands a payer. `/confirm` is now idempotent (no double owner-pings /
>   feed-URL emails on a second click).
> - **Self-serve resume.** `POST /manage/resume` + button/banner on the prefs
>   page. "Pause all alerts" → `paused` was a dead end (still billed, no way
>   back without support).
> - **past_due gets the grace it was promised.** `DELIVER_STATUSES =
>   [active, past_due]` for instant + digest, matching the RSS feed. Was
>   `status === "active"` only, so a card blip cut email/digest instantly.
> - **RSS feed no longer gated by the digest toggle** — `wants(…, "feed")`:
>   floor + vendor filter only. A sub who wanted instant+RSS and unticked
>   "daily digest" was emptying their own feed.
> - **Existing active subscriber re-checkout** (upgrade / resubscribe / event
>   replay w/ new id) stays `active` → `{action:"synced"}`, not knocked back
>   to `unconfirmed`.
> - **Discord at signup** is deferred like Telegram (OAuth after payment) —
>   picking it used to hard-fail on the empty target.
> - CSP `form-action` allows `https://buy.stripe.com` (Payment Link redirect).
>
> Tests: `worker/test/run.mjs` (38) + new `worker/test/db.mjs` (11, node:sqlite
> D1 shim, no wrangler) + `worker/test/flow.mjs` (60, needs `wrangler dev`).
> `npm test` runs run+db; `npm run test:flow` the e2e.
>
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
