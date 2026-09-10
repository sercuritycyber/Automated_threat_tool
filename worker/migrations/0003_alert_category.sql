-- Adds curation columns to an existing posts_log. The daily digest email
-- only carries score >= CONFIG.digest_min_score OR an actionable category;
-- the private RSS feed still carries everything above the tier floor.
-- Run once against the live D1:
--   npx wrangler d1 execute cti-subs --remote --file migrations/0003_alert_category.sql
ALTER TABLE posts_log ADD COLUMN category TEXT;
ALTER TABLE posts_log ADD COLUMN exposure TEXT;
ALTER TABLE posts_log ADD COLUMN confidence TEXT;
