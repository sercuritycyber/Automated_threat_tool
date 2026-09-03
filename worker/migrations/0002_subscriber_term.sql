-- Adds the Worker-managed access window to an existing subscribers table.
-- Stripe-billed subscribers keep this NULL (they renew via webhook); a
-- free-promo or timed-comp account sets it, and the daily lifecycle cron
-- lapses the account to 'cancelled' when it passes.
-- Run once against the live D1:
--   npx wrangler d1 execute cti-subs --remote --file migrations/0002_subscriber_term.sql
-- Safe to skip on a fresh database created from schema.sql, which already has it.
ALTER TABLE subscribers ADD COLUMN term_ends_at TEXT;
