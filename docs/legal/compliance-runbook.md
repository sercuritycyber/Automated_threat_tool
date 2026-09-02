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
4. **Pick an email provider with good deliverability.** A personal SMTP box will
   land in spam. Amazon SES, Resend or Postmark; put the SMTP creds in
   `config/env` (`EMAIL_SMTP_*`, `EMAIL_FROM`).
5. **Create the Stripe products** (one price per tier), copy each price id into
   `subscriptions.tiers.<name>.price_id`, and the keys into `config/env`
   (`STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`). Add the webhook endpoint
   (`{{site}}/webhooks/stripe`) in the Stripe dashboard and copy its signing
   secret to `STRIPE_WEBHOOK_SECRET`.
6. Leave `subscriptions.live: false` and use `sk_test_` keys until you have run a
   full test checkout end to end.

## Records of processing (ROPA)

Keep a short document (a page is fine) listing: what data you hold, why, lawful
basis, who you share it with, retention, and security measures. The privacy
policy already contains most of it — copy it into an internal note and keep it
updated. Required under Article 30 even for small businesses that process data
regularly.

## Data subject requests (DSARs)

- Someone asks for their data → use **Subscribers → (their row) → Export JSON**
  in the owner panel, or `bin/deliver.py` is not it — it's in the admin UI.
- Someone asks to be deleted → **Delete** in the same place. This erases the
  account and, if they have a Stripe subscription, cancels it. An anonymous
  audit line is kept (no personal data in it).
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

`bin/retention.py` runs weekly (`cti-retention.timer`) and deletes:
- cancelled accounts after `subscriptions.retention_days_cancelled` days
- unconfirmed sign-ups after `subscriptions.retention_days_unconfirmed` days

Check the timer is enabled: `systemctl --user list-timers cti-retention.timer`.

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
