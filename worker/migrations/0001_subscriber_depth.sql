-- Adds the paid write-up columns to an existing posts_log.
-- Run once against the live D1:
--   npx wrangler d1 execute cti-subs --remote --file migrations/0001_subscriber_depth.sql
-- Safe to skip on a fresh database created from schema.sql, which already has them.
ALTER TABLE posts_log ADD COLUMN subscriber_text TEXT;
ALTER TABLE posts_log ADD COLUMN action TEXT;
