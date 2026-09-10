-- CTI Alerts subscriber schema (Cloudflare D1 / SQLite).
-- Idempotent: safe to run repeatedly. Ported from lib/subscribers.py.

CREATE TABLE IF NOT EXISTS subscribers (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  email                  TEXT NOT NULL UNIQUE,
  name                   TEXT,
  tier                   TEXT NOT NULL DEFAULT 'individual',
  cadence                TEXT NOT NULL DEFAULT 'monthly',
  status                 TEXT NOT NULL DEFAULT 'pending_payment',
  source                 TEXT NOT NULL DEFAULT 'stripe',
  stripe_customer_id     TEXT,
  stripe_subscription_id TEXT,
  current_period_end     INTEGER,
  -- Worker-managed access window (ISO 8601). Stripe-billed subscribers renew
  -- via webhook and leave this NULL; a free-promo or timed-comp account sets
  -- it, and the daily lifecycle cron lapses the account to 'cancelled' once it
  -- passes. NULL = no Worker-managed term (Stripe-billed, or forever-free).
  term_ends_at           TEXT,
  created_at             TEXT NOT NULL,
  confirmed_at           TEXT,
  cancelled_at           TEXT,
  notes                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sub_status ON subscribers(status);
CREATE INDEX IF NOT EXISTS idx_sub_stripe_cus ON subscribers(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_sub_stripe_sub ON subscribers(stripe_subscription_id);

CREATE TABLE IF NOT EXISTS channels (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
  kind          TEXT NOT NULL,
  target        TEXT NOT NULL,
  label         TEXT,
  verified      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  UNIQUE(subscriber_id, kind, target)
);
CREATE INDEX IF NOT EXISTS idx_chan_sub ON channels(subscriber_id);

CREATE TABLE IF NOT EXISTS prefs (
  subscriber_id      INTEGER PRIMARY KEY REFERENCES subscribers(id) ON DELETE CASCADE,
  vendors            TEXT NOT NULL DEFAULT '[]',
  min_score_override INTEGER,
  instant            INTEGER NOT NULL DEFAULT 1,
  digest             INTEGER NOT NULL DEFAULT 1,
  updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consents (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  subscriber_id INTEGER REFERENCES subscribers(id) ON DELETE SET NULL,
  kind          TEXT NOT NULL,
  granted       INTEGER NOT NULL,
  ts            TEXT NOT NULL,
  ip            TEXT,
  user_agent    TEXT,
  terms_version TEXT,
  detail        TEXT
);
CREATE INDEX IF NOT EXISTS idx_consent_sub ON consents(subscriber_id);

CREATE TABLE IF NOT EXISTS deliveries (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
  post_key      TEXT NOT NULL,
  channel_kind  TEXT NOT NULL,
  mode          TEXT NOT NULL,
  sent_at       TEXT NOT NULL,
  ok            INTEGER NOT NULL DEFAULT 1,
  error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_deliv_key ON deliveries(subscriber_id, post_key);

CREATE TABLE IF NOT EXISTS events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  subscriber_id INTEGER REFERENCES subscribers(id) ON DELETE SET NULL,
  ts            TEXT NOT NULL,
  kind          TEXT NOT NULL,
  detail        TEXT,
  ip            TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_sub ON events(subscriber_id);

CREATE TABLE IF NOT EXISTS stripe_events (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signup_intents (
  token          TEXT PRIMARY KEY,
  email          TEXT NOT NULL,
  tier           TEXT NOT NULL,
  cadence        TEXT NOT NULL,
  channel_kind   TEXT,
  channel_target TEXT,
  ip             TEXT,
  user_agent     TEXT,
  created_at     TEXT NOT NULL,
  consumed       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS posts_log (
  post_key   TEXT PRIMARY KEY,
  title      TEXT,
  url        TEXT,
  source     TEXT,
  score      INTEGER,
  cve_ids    TEXT,
  post_text  TEXT,
  short_text TEXT,
  -- The paid write-up (versions, exposure check, action, detection). Kept
  -- separate from post_text so a subscriber alert is never just the social
  -- post reworded.
  subscriber_text TEXT,
  action     TEXT,
  -- how a defender acts on it: an actionable category + the exposure line
  -- gate whether it belongs in the digest EMAIL (the fatigue vector) vs
  -- only the private RSS feed.
  category   TEXT,
  exposure   TEXT,
  confidence TEXT,
  seen_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_seen ON posts_log(seen_at);

CREATE TABLE IF NOT EXISTS kv (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- One-off purchases (paid threat briefs) — Stripe Checkout in `payment` mode.
CREATE TABLE IF NOT EXISTS purchases (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  stripe_session_id TEXT NOT NULL UNIQUE,
  payment_intent    TEXT,
  email             TEXT,
  subscriber_id     INTEGER REFERENCES subscribers(id) ON DELETE SET NULL,
  item              TEXT NOT NULL DEFAULT 'brief',
  amount_total      INTEGER,
  currency          TEXT,
  status            TEXT NOT NULL DEFAULT 'pending',
  fulfilled_at      TEXT,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_purchase_email ON purchases(email);

CREATE TABLE IF NOT EXISTS ratelimit (
  bucket TEXT PRIMARY KEY,
  count  INTEGER NOT NULL,
  reset_at INTEGER NOT NULL
);
