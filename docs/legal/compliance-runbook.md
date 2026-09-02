# Compliance runbook (owner)

Not customer-facing. What you must do to run the paid service lawfully in the UK,
and how the code helps.

## Before you take a single payment

1. **Register with the ICO.** You process personal data (a customer mailing
   list) so the data-protection fee is mandatory.
   - Self-assessment + payment: <https://ico.org.uk/registration/>
   - Tier 1 (micro): **£40–£52 / year**.
   - Put the registration number in `subscriptions.ico_registration` — it then
     shows in the website footer and privacy policy automatically.
2. **Set your legal identity.** `subscriptions.company_legal_name` = your
   sole-trader name ("Charlie Collins" or a registered business name).
   `subscriptions.contact_email` = a monitored address for privacy requests.
3. **Publish the policies.** They're at `/privacy`, `/terms`, `/cookies` and are
   generated from `docs/legal/*.md` with your details filled in. Read them and
   adjust anything that doesn't match how you actually operate.
4. **Email:** the Worker sends via **Resend** (`RESEND_API_KEY` secret). Verify
   the `securitycyber.uk` domain in Resend and use
   `securitycyber_cti@securitycyber.uk` as the from/reply address.
5. **Create the Stripe products** — a monthly and/or weekly Price per tier —
   and paste the price ids into `worker/wrangler.toml` `CONFIG` under
   `tiers.<name>.monthly_price_id` / `weekly_price_id`. Set `STRIPE_SECRET_KEY`
   + `STRIPE_WEBHOOK_SECRET` as Worker secrets. Add the webhook endpoint
   `https://cti.securitycyber.uk/webhooks/stripe` in the Stripe dashboard.
6. Keep `CONFIG.live: false` and `sk_test_` keys until a full test checkout
   works end to end; then set `live: true` and the `sk_live_` key.

**Deployment now runs as the Cloudflare Worker in `worker/`** — see
`worker/README.md`. The old Python `web/public_app.py` stays as a local-dev
reference only.

## Records of processing (ROPA)

Keep a short document (a page is fine) listing: what data you hold, why, lawful
basis, who you share it with, retention, and security measures. The privacy
policy already contains most of it — copy it into an internal note and keep it
updated. Required under Article 30 even for small businesses that process data
regularly.

## Data subject requests (DSARs)

- Someone asks for their data → **Subscribers → (their row) → Export (DSAR)**
  in the owner panel. Subscribers can also self-serve from their preference
  centre ("download my data").
- Someone asks to be deleted → **Erase (DSAR)** in the same place (or the
  subscriber's own "delete my account" button). This erases the account and, if
  they have a Stripe subscription, cancels it. An anonymous audit line is kept.
- Respond within **one calendar month**. Free unless excessive.

## Personal data breach

If personal data is lost, stolen or exposed in a way that risks harm to
individuals, notify the **ICO within 72 hours** of becoming aware
(<https://ico.org.uk/for-organisations/report-a-breach/>). Notify affected
subscribers if the risk is high. Keep a log of all breaches even if not
reportable.

## PECR / marketing

- Alert emails are **service messages** the customer paid for — allowed.
- The **soft opt-in** lets you email *existing customers* about similar services
  of your own, provided every message has an easy opt-out. Do not email
  non-customers or share the list.
- Every message includes a one-click unsubscribe (`List-Unsubscribe` header +
  link). Do not remove it.

## Retention

The Worker's **weekly Cron Trigger** (Sun 03:30 UTC) deletes:
- cancelled accounts after `CONFIG.retention_days_cancelled` days
- unconfirmed sign-ups after `CONFIG.retention_days_unconfirmed` days
- stale signup intents after 48h

Check it ran: the owner panel `/subscribers` page shows "last retention".
(Legacy local Python: `bin/retention.py` + `cti-retention.timer`.)

## Keeping this current

- Review the processor table in `docs/legal/privacy-policy.md` whenever you
  add/replace a provider (email host, hosting, a new channel).
- Re-read the policies once a year and bump `TERMS_VERSION` in
  `lib/subscribers.py` if they change materially (that invalidates old consent
  records' version string, which is intended — you may want fresh consent).

## VAT

Below the £90,000 turnover threshold you are not VAT registered and do not add
VAT. If you approach it, register and revisit pricing. Stripe Tax can automate
this later.
