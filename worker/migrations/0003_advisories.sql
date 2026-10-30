-- Additive archive: no subscriber tables or historical posts are changed.
CREATE TABLE IF NOT EXISTS advisories (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  source_text TEXT NOT NULL,
  published TEXT NOT NULL,
  document TEXT NOT NULL,
  added_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS advisories_published ON advisories(published);
