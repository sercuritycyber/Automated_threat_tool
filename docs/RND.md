# R&D — productisation notes

> Captured 2026-09-02. Ideas / roadmap only — **not** built, not committed to.
> The pipeline itself stays free for Blue Team Podcast's own channels.

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
