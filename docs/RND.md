# R&D — productisation notes

> Captured 2026-09-02. **Update: the subscription service described below was
> then built** — see `docs/BUILD.md` for the phase log and `README.md` →
> "Paid alert service". This file stays as the background/rationale.
> It ships **disabled** (`subscriptions.enabled: false`); the brand's own free
> channels are unchanged.

## Value proposition & tiers

| Tier | Pricing | Delivery | Features |
|---|---|---|---|
| **Individual Defender** | £2 / week (£8/mo) | Email / ntfy | Instant high-priority alerts (pre-score / severity ≥ 90) + a daily digest |
| **Team / MSP** | £15–£20 / month | Email / webhook / Slack | Multi-recipient delivery, custom CVE + vendor filtering, exportable candidate JSON |

## Productisation roadmap

### Monetisation & management
- Stripe Payment Links or Gumroad for weekly / monthly recurring subscriptions.
- A lightweight webhook that adds / removes client addresses from the `EMAIL_TO`
  list (or a subscriber DB) on subscribe / cancel.

### Delivery enhancements
- **Email (SMTP):** the existing `bin/publishers.py` `post_email` works cleanly.
  For inbox deliverability at volume, send through a transactional provider —
  SendGrid, Resend, or AWS SES — rather than a personal SMTP box.
- **Custom filtering:** let a subscriber pin key vendors (Fortinet, Cisco,
  Palo Alto, …) and only get paged for high-impact CVEs touching their stack.
  Ingest already extracts CVE IDs and vendor/product context — the filter is a
  per-subscriber allow-list applied at delivery time.

### Legal & technical safeguards
- **Disclaimer:** a standard CTI disclaimer — alerts are aggregated from public
  advisories for informational purposes and are **not** an SLA for patch
  management.
- **Source attribution:** keep the clear links back to vendor advisories and
  researchers that the pipeline already enforces (`prompts/analyst.md` — "facts
  only from the source", every post ends with the source URL).

## How today's build maps to this

| Roadmap need | Already in the repo |
|---|---|
| Email delivery | `bin/publishers.py` `post_email` (SMTP, STARTTLS/SSL) + Secrets page fields |
| Multi-recipient | `EMAIL_TO` is a comma list |
| Severity ≥ 90 alerting | `publish.severity_override_score`; ingest `pre_score`; analyst `score` |
| Multi-channel | `publish.targets` = linkedin / mastodon / bluesky / x / email |
| Exportable candidate JSON | `state/candidates.json` is already the artefact |
| Per-run digest | not built — would be a new publisher that batches the run's `posts[]` |

## Open questions

- Digest format (one email vs. per-CVE) and cadence.
- Where the subscriber list lives (flat file vs. SQLite vs. the existing Turso).
- Whether filtering happens in ingest (cheaper) or at delivery (per-subscriber).
- Rate-of-change: a paid alert that's 30 min slower than free feeds isn't worth £8/mo — the value is the triage + "do now", not the speed.

---

## Running it as a UK micro-SaaS

> From `Turning your automated-system_into_a_sub_service.txt` (see `docs/` for the raw note).
> Verdict there: viable as a low-overhead side-hustle; validate before scaling.

### Financial viability & tax
- **£1,000 trading allowance** — HMRC gives a UK sole trader £1,000/yr tax-free
  trading income. First cohort (≈10 users × £8/mo ≈ £960/yr) fits under it: no tax
  due, no Self Assessment registration needed *if* it stays the only side income.
- **Monzo Business** — built-in Making Tax Digital tool (Sage-powered) categorises
  transactions and tracks income/expenses in real time; no manual spreadsheet.
- **Payments** — Stripe or Lemon Squeezy for recurring weekly/monthly billing,
  paid out to the Monzo account.

### Legal & compliance
- **ICO registration** — running a mailing list of customer emails = processing
  personal data, so the ICO data-protection fee is **mandatory**. Tier 1
  micro-business: **£40–£52/year**. Bonus for a security brand — it puts you on
  the public ICO register (visible accountability).
- **Terms & conditions** — state clearly the service aggregates *public
  advisories for information only*, no patch-management SLA. Protects you if a
  severe CVE slips past the RSS/KEV heuristics.

### Brand & delivery
- **securitycyber.uk** — deploy a lightweight landing page: the technical merits
  (CISA KEV cross-reference, near-duplicate clustering, `claude -p` triage, the
  "do now" line), the two tiers, a Stripe checkout link.
- **Transactional email** — Resend / Postmark / Amazon SES from the Python
  script for deliverability. `bin/publishers.py` `post_email` already speaks SMTP;
  these providers all offer an SMTP endpoint, so it's a credentials swap, not a
  rewrite. (SES also has a REST API if raw SMTP throughput becomes a limit.)

### Recurring overhead (steady state)
- ICO fee £40–£52/yr + `securitycyber.uk` renewal. Email provider free tier
  covers early volume (Resend 3k/mo, SES ~62k/mo from EC2). A few paying infosec
  subscribers make it self-sustaining.

### Subscriber → delivery plumbing — ✅ BUILT
`lib/billing.py` verifies the Stripe webhook; `web/public_app.py` handles
`/webhooks/stripe`; `lib/subscribers.py` is the store (Turso or SQLite);
`bin/deliver.py` fans out per subscriber. Original sketch, for reference:

Stripe fires webhooks on `customer.subscription.created` / `.deleted`:

1. Small webhook endpoint (FastAPI route in `web/app.py`, or a separate tiny
   service) verifies the Stripe signature.
2. On create: append `{email, tier, vendors:[], stripe_id}` to a subscribers
   store — start with `config/subscribers.json` (flat file, git-ignored, chmod
   600), move to SQLite/Turso only if it grows.
3. On delete/cancel: mark inactive (keep the row for audit; don't hard-delete).
4. Delivery: a new `email_digest` publisher (or a flag on `post_email`) that,
   instead of a single `EMAIL_TO`, iterates active subscribers, applies each
   subscriber's vendor allow-list to the run's `posts[]`, and sends per-tier
   (Individual = severe-only + daily digest; Team = everything + JSON attach).
5. Never put subscriber addresses in `config/env` / `EMAIL_TO` — that's for the
   brand's own channels; the subscriber list is separate and access-controlled.

Keep the free Blue Team Podcast channels (LinkedIn etc.) exactly as they are —
the paid tier is an *additional* delivery path off the same `posts[]`.
