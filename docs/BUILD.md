# BUILD TRACKER — CTI subscription service

**Goal:** turn the CTI pipeline into a production, UK-law-compliant paid alert
service. Owner drives everything from the local dashboard; subscribers sign up
via a public site and receive alerts by email / Slack / Telegram / webhook / ntfy.

**Working rule:** update this file at the end of every work session — tick boxes,
note blockers, move the "RESUME HERE" pointer. Commit code + this file together.

Legend:  `[ ]` todo  `[~]` in progress  `[x]` done + tested  `[!]` blocked (see note)

---

## RESUME HERE
> Phase 0 ✅ (`bin/selftest_subs.py` 28/28) · Phase 1 ✅ (`bin/selftest_deliver.py` 15/15).
> Next: **Phase 2 — Stripe billing** (`lib/billing.py` + webhook handling; the
> webhook route itself lands in Phase 3's public app, so Phase 2 = the library
> + idempotency + recorded-payload tests).
> Commits: P0 `f8397f3` · P1 `<pending>`.

## ⚠ OPEN BLOCKER — Turso credentials
User wants PII in a **Turso** DB (EU region — good for UK GDPR). No `turso` CLI
or token on this laptop; group admin token lives on `godseye-vm`.
**Need from user:** create/rename an empty Turso DB and give me:
- `TURSO_DATABASE_URL`  (e.g. `libsql://cti-subs-hermess.turso.io`)
- `TURSO_AUTH_TOKEN`    (DB-scoped token, `turso db tokens create <db>`)
Put them in `config/env`. Until then everything is built + tested against a
local SQLite file (`state/subscribers.db`); switching to Turso is just those two
env vars — `lib/db.py` picks the backend automatically. Schema is identical
(libSQL is SQLite-compatible); access is the Turso **HTTP pipeline API** via
`requests`, no new pip deps.

---

## Architecture decisions (locked)

- **Two ASGI apps, separate processes:**
  - `web/app.py` — owner console, **localhost only**, unchanged binding. Adds a
    Subscribers admin + Subscriptions settings.
  - `web/public_app.py` — **public**, behind Caddy (auto-TLS). Signup, Stripe
    checkout return, preference centre, unsubscribe, double-opt-in, channel
    linking, webhooks. Minimal surface, no owner secrets beyond what it needs.
- **Data store:** `lib/db.py` abstraction, two interchangeable backends:
  - **Turso** (libSQL HTTP pipeline API via `requests`) when `TURSO_DATABASE_URL`
    + `TURSO_AUTH_TOKEN` are set — this is the production PII store (EU region).
  - **local SQLite** (`state/subscribers.db`, WAL, stdlib `sqlite3`) otherwise —
    for dev/test. Same SQL, same schema.
  Schema versioned in `lib/subscribers.py` (`_MIGRATIONS`), applied on first use.
- **No new pip deps.** Stripe / Telegram / Slack all via `requests` + stdlib
  `hmac` for signature checks. Signed tokens = stdlib HMAC (`lib/tokens.py`).
- **Money:** Stripe Checkout (hosted, PCI SAQ-A) + Stripe Customer Portal.
  We never see card data. Test mode until owner sets live keys AND flips
  `subscriptions.live: true`.
- **VAT:** below £90k threshold → not registered, no VAT line. Documented.
- **Lawful basis:** *contract* for the alerts they pay for; *consent* (double
  opt-in, logged with timestamp+IP+terms version) as belt-and-braces + for the
  free tier. One-click unsubscribe everywhere (PECR).
- **Channels:** email (SMTP/SES via existing `post_email`), Slack (incoming
  webhook URL per sub, or bot), Telegram (bot + chat_id via deep link), generic
  webhook (POST JSON), ntfy (topic URL). `lib/channels.py`.
- **Tiers:** `individual` (severe-only instant + daily digest), `team`
  (everything + JSON attachment + multi-recipient). Rules in settings, not code.
- **Delivery trigger:** `bin/deliver.py` runs after `bin/run.sh`'s publish stage
  (instant high-sev) + its own timer for the daily digest.

## Config shape (config/settings.yaml → new `subscriptions:` block)
```
subscriptions:
  enabled: false           # master off switch
  live: false              # false = Stripe test mode, no real charges
  brand_name: Blue Team Podcast
  site_url: https://cti.securitycyber.uk
  from_email: alerts@securitycyber.uk
  ico_registration: ""     # owner pastes their ICO number → shown in footer
  company_legal_name: ""   # owner's sole-trader / company name for T&Cs
  contact_email: privacy@securitycyber.uk
  retention_days_cancelled: 180
  retention_days_unconfirmed: 7
  tiers:
    individual:
      price_id: ""         # Stripe price id
      display_price: "£8 / month"
      instant_min_score: 90
      daily_digest: true
      channels: [email, telegram, ntfy]
    team:
      price_id: ""
      display_price: "£18 / month"
      instant_min_score: 75
      daily_digest: true
      attach_json: true
      channels: [email, slack, telegram, webhook, ntfy]
```

## Secrets (config/env → new keys)
```
STRIPE_SECRET_KEY=            # sk_test_… / sk_live_…
STRIPE_PUBLISHABLE_KEY=       # pk_…
STRIPE_WEBHOOK_SECRET=        # whsec_…
STRIPE_PORTAL_CONFIG_ID=      # optional
TELEGRAM_BOT_TOKEN=           # from @BotFather
TELEGRAM_BOT_USERNAME=        # for deep links
SUBS_TOKEN_SECRET=            # random 32+ bytes, for signed links (auto-gen if absent)
```

---

## PHASES

### Phase 0 — foundations ✅
- [x] `lib/db.py` — `execute`/`query`/`executescript`/`tx`/`migrate`; Turso HTTP pipeline + local sqlite; auto-select on `TURSO_DATABASE_URL`+`TURSO_AUTH_TOKEN`
- [x] `lib/subscribers.py` — subscribers/channels/prefs/consents/deliveries/events/stripe_events/kv; `MIGRATIONS` v1; CRUD; status lifecycle; DSAR export/erase; retention purge; stats
- [x] `lib/tokens.py` — HMAC-SHA256 signed url-safe tokens with exp + action check; secret auto-gen to config/env
- [x] `lib/subsconfig.py` — `subscriptions:` block loader, tier merge, `price_to_tier()`, `problems()` blocker list
- [x] `subscriptions:` block in `config/settings.yaml` (uncommitted — DEFAULTS in subsconfig.py are the reference)
- [x] `.gitignore` covers `state/subscribers.db*` + `config/subscribers.json`
- [x] `bin/selftest_subs.py` — 28 checks, all green on sqlite

### Phase 1 — delivery engine ✅
- [x] `lib/channels.py` — `send(kind, target, subject, body, json_payload=…)` for email / slack (incoming webhook) / telegram (bot) / webhook (signed) / ntfy; `verify_target()` for channel validation
- [x] `bin/deliver.py` — `--instant` (from decision.json) · `--digest` (last 24h from `state/posts_log.jsonl`) · `--test <id>` · `--list`
- [x] vendor-filter matching (subscriber `vendors[]` vs title+text+cve)
- [x] per-tier rules (instant_min_score, channels allow-list, attach_json)
- [x] dedupe via `deliveries` table (instant never re-sends; digest once per day)
- [x] hooked into `bin/run.sh` stage 4/4 (non-fatal) + `systemd/cti-digest.{service,timer}` (08:15 daily)
- [x] `CTI_DRY_RUN` / `--dry` honoured; `subscriptions.enabled` false → clean no-op
- [x] `bin/selftest_deliver.py` — 15 checks, mocked sends, all green

### Phase 2 — Stripe billing
- [ ] `lib/billing.py` — create_checkout_session(tier, email) · verify_webhook(payload, sig) · price_id→tier map · portal_session(customer)
- [ ] webhook events: checkout.session.completed → create/activate sub · customer.subscription.deleted → cancel · invoice.payment_failed → mark past_due + notify owner · customer.subscription.updated → sync
- [ ] idempotency (store processed event ids)
- [ ] test-mode path fully exercised with Stripe test fixtures / recorded payloads
- [ ] owner alert on payment failure / new sub (via existing notify.py)

### Phase 3 — public subscriber app
- [ ] `web/public_app.py` — FastAPI, own styling, no cookies, security headers, rate limiting (`lib/ratelimit.py`, sqlite-backed)
- [ ] `/` landing — pricing, tech merits (KEV xref, dedup, "do now"), links to /terms /privacy
- [ ] `/subscribe?tier=` → Stripe Checkout redirect
- [ ] `/welcome?session_id=` → confirm email sent, prompt to pick channel
- [ ] `/confirm?t=` double opt-in (logs consent + IP + terms version)
- [ ] `/manage?t=` preference centre — vendors, channels, pause/resume, "manage billing" (portal), "download my data", "delete my account"
- [ ] `/unsubscribe?t=` one-click (GET, no confirm needed per PECR; also POST form)
- [ ] `/channel/telegram` deep-link + `/webhooks/telegram` (bot /start → capture chat_id, link via token)
- [ ] `/channel/slack` — paste incoming-webhook URL, validated
- [ ] `/webhooks/stripe`
- [ ] `/terms` `/privacy` `/legal` — rendered from `docs/legal/*.md`
- [ ] `/healthz`
- [ ] `systemd/cti-public.service` + `Caddyfile` + `systemd/install-public.sh`

### Phase 4 — owner dashboard additions
- [ ] `/subscribers` — table (email, tier, status, channel, joined, last send), search, filters
- [ ] `/subscribers/{id}` — detail: consent log, delivery history, edit vendors/channel/tier, comp/manual add, **export JSON**, **erase (DSAR)** (also cancels Stripe)
- [ ] `/subscribers/add` — manual/comp subscriber (no payment)
- [ ] `/subscribers/export.csv`
- [ ] `/subscriptions` settings page — the `subscriptions:` block + Stripe key status + "test checkout" button + price-id validation
- [ ] dashboard tiles: active subs · MRR (from tier prices) · sends today · past-due count
- [ ] nav + links

### Phase 5 — compliance
- [ ] `docs/legal/privacy-policy.md` — UK GDPR / DPA 2018: controller, lawful basis, data collected, processors (Stripe, email provider, Slack, Telegram, hosting), retention, rights, complaints→ICO, contact
- [ ] `docs/legal/terms.md` — service description, "informational, no patch SLA", liability cap, cancellation, acceptable use, governing law = England & Wales
- [ ] `docs/legal/cookies.md` — statement that the site sets no non-essential cookies
- [ ] `docs/legal/compliance-runbook.md` — ICO registration steps + fee, DSAR process (30 days), breach process (72h to ICO), processor list upkeep, retention job schedule
- [ ] `bin/retention.py` — purge cancelled > N days, unconfirmed > 7 days; wired to a weekly timer
- [ ] consent record: every state change logged in `consents`/`events` with ts + ip + terms_version
- [ ] data-minimisation pass: store only what's needed; IP only where consent proof requires it
- [ ] security headers, HSTS via Caddy, webhook sig verification, token expiry, rate limits — all confirmed

### Phase 6 — deploy + hardening
- [ ] `systemd/install-public.sh` — installs cti-public.service, cti-digest.timer, cti-retention.timer; prints Caddy instructions
- [ ] `Caddyfile` template (auto-TLS, security headers, reverse_proxy to public app port)
- [ ] separate low-priv run user for the public app (document; don't require)
- [ ] `bin/selftest.py` — end-to-end dry check (db, config, stripe key format, channel creds, caddy present)
- [ ] backups: `bin/backup_db.py` (sqlite .backup) + weekly timer
- [ ] `requirements.txt` note (still zero new deps) + README

### Phase 7 — docs
- [ ] README — new "Paid alert service" section + architecture diagram update
- [ ] `docs/RND.md` — mark the built parts done
- [ ] operator runbook: first-run setup order (Stripe products → keys → settings → test checkout → go live)
- [ ] update memory file

---

## FILE MAP (planned)
```
lib/subscribers.py     sqlite store + migrations + CRUD
lib/tokens.py          signed url-safe tokens (HMAC)
lib/subsconfig.py      subscriptions config loader/validator
lib/channels.py        email / slack / telegram / webhook / ntfy senders
lib/billing.py         Stripe REST (checkout, webhook verify, portal)
lib/ratelimit.py       sqlite token-bucket per IP
bin/deliver.py         fan-out engine (instant + digest)
bin/retention.py       data retention purge
bin/backup_db.py       sqlite backup
bin/selftest.py        pre-flight
web/public_app.py      public subscriber site + webhooks
web/app.py             + /subscribers, /subscriptions  (owner)
systemd/cti-public.service  cti-digest.timer  cti-retention.timer  cti-backup.timer
Caddyfile
docs/legal/*.md        privacy, terms, cookies, compliance-runbook
state/subscribers.db   (gitignored)
```

## TEST COMMANDS
```
python3 -m py_compile lib/*.py bin/*.py web/*.py
python3 bin/selftest_subs.py          # phase 0
python3 bin/deliver.py --digest --dry # phase 1
python3 bin/selftest.py               # phase 6, full preflight
```

## BLOCKERS / NOTES
- (none yet)
