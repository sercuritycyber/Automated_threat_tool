#!/usr/bin/env python3
"""SQLite storage for the onion-address aggregator — stdlib only.

The product is a maintained LIST of .onion addresses, not forum content. Three
tables:

* ``onions``    — one row per unique v3 address: when first/last seen, how many
  times, and the latest liveness result (status/title/checked_at).
* ``sightings`` — where each address was found (which source page, with the
  label/context around it), so a human can judge trust and spot phishing clones.
* ``tags``      — target forum names attached to an address (breachforums/xss/…).

No forum page bodies are stored — only addresses, the context they appeared in
on the *index* sources, and a liveness title.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS onions (
    address       TEXT PRIMARY KEY,
    first_seen    REAL NOT NULL,
    last_seen     REAL NOT NULL,
    times_seen    INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'unknown',   -- unknown | up | down
    http_status   INTEGER,
    title         TEXT,
    last_checked  REAL,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS sightings (
    id            INTEGER PRIMARY KEY,
    address       TEXT NOT NULL REFERENCES onions(address),
    source        TEXT NOT NULL,
    label         TEXT,
    context       TEXT,
    seen_at       REAL NOT NULL,
    UNIQUE(address, source, label)
);

CREATE TABLE IF NOT EXISTS tags (
    address       TEXT NOT NULL REFERENCES onions(address),
    tag           TEXT NOT NULL,
    UNIQUE(address, tag)
);

CREATE INDEX IF NOT EXISTS ix_sight_addr ON sightings(address);
CREATE INDEX IF NOT EXISTS ix_tags_addr ON tags(address);
CREATE INDEX IF NOT EXISTS ix_onion_status ON onions(status);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- discovery ---------------------------------------------------------- #
    def add_sighting(self, address: str, source: str, label: str, context: str,
                     tags: list[str] | None = None, now: float | None = None) -> bool:
        """Upsert an address and record this sighting. Returns True if the
        address was newly discovered."""
        now = time.time() if now is None else now
        address = address.lower().strip()
        cur = self.db.cursor()
        row = cur.execute("SELECT address FROM onions WHERE address=?", (address,)).fetchone()
        is_new = row is None
        if is_new:
            cur.execute(
                "INSERT INTO onions(address,first_seen,last_seen,times_seen) VALUES(?,?,?,1)",
                (address, now, now),
            )
        else:
            cur.execute(
                "UPDATE onions SET last_seen=?, times_seen=times_seen+1 WHERE address=?",
                (now, address),
            )
        try:
            cur.execute(
                "INSERT INTO sightings(address,source,label,context,seen_at) VALUES(?,?,?,?,?)",
                (address, source, (label or "")[:200], (context or "")[:500], now),
            )
        except sqlite3.IntegrityError:
            pass  # same address/source/label already logged
        for tag in tags or []:
            try:
                cur.execute("INSERT INTO tags(address,tag) VALUES(?,?)", (address, tag))
            except sqlite3.IntegrityError:
                pass
        self.db.commit()
        return is_new

    def set_liveness(self, address: str, *, status: str, http_status: int | None,
                     title: str | None, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.db.execute(
            "UPDATE onions SET status=?, http_status=?, title=COALESCE(?,title), last_checked=? "
            "WHERE address=?",
            (status, http_status, title, now, address.lower().strip()),
        )
        self.db.commit()

    def set_note(self, address: str, note: str) -> None:
        self.db.execute("UPDATE onions SET note=? WHERE address=?",
                        (note, address.lower().strip()))
        self.db.commit()

    def mark_blocked(self, address: str, note: str = "csam-filtered",
                     now: float | None = None) -> None:
        """Record an address as CSAM-blocked, storing NO context or title, and
        purge anything already stored for it. Blocked rows are never fetched,
        listed, or shown — kept only so we skip the address on future runs."""
        now = time.time() if now is None else now
        address = address.lower().strip()
        cur = self.db.cursor()
        cur.execute("DELETE FROM sightings WHERE address=?", (address,))
        cur.execute("DELETE FROM tags WHERE address=?", (address,))
        row = cur.execute("SELECT address FROM onions WHERE address=?", (address,)).fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO onions(address,first_seen,last_seen,status,title,note,last_checked)"
                " VALUES(?,?,?,'blocked',NULL,?,?)",
                (address, now, now, note, now),
            )
        else:
            cur.execute(
                "UPDATE onions SET status='blocked', title=NULL, note=?, last_checked=? "
                "WHERE address=?",
                (note, now, address),
            )
        self.db.commit()

    def is_blocked(self, address: str) -> bool:
        row = self.db.execute("SELECT status FROM onions WHERE address=?",
                              (address.lower().strip(),)).fetchone()
        return bool(row) and row["status"] == "blocked"

    def addresses_to_check(self, *, stale_seconds: float | None = None,
                           limit: int | None = None) -> list[str]:
        q = "SELECT address FROM onions WHERE status != 'blocked'"
        args: list = []
        if stale_seconds is not None:
            q += " AND (last_checked IS NULL OR last_checked < ?)"
            args.append(time.time() - stale_seconds)
        q += " ORDER BY last_checked IS NOT NULL, last_seen DESC"
        if limit:
            q += " LIMIT ?"
            args.append(limit)
        return [r["address"] for r in self.db.execute(q, args)]

    def listing(self, *, tag: str | None = None, status: str | None = None):
        q = ("SELECT o.*, "
             "(SELECT GROUP_CONCAT(t.tag) FROM tags t WHERE t.address=o.address) AS tags, "
             "(SELECT GROUP_CONCAT(DISTINCT s.source) FROM sightings s WHERE s.address=o.address) AS sources, "
             "(SELECT COUNT(*) FROM sightings s WHERE s.address=o.address) AS n_sightings "
             "FROM onions o WHERE o.status != 'blocked'")
        args: list = []
        if tag:
            q += " AND o.address IN (SELECT address FROM tags WHERE tag=?)"
            args.append(tag)
        if status:
            q += " AND o.status=?"
            args.append(status)
        q += " ORDER BY (o.status='up') DESC, o.last_seen DESC"
        return self.db.execute(q, args).fetchall()

    def stats(self) -> dict:
        c = self.db.execute
        return {
            "onions": c("SELECT COUNT(*) FROM onions WHERE status!='blocked'").fetchone()[0],
            "up": c("SELECT COUNT(*) FROM onions WHERE status='up'").fetchone()[0],
            "down": c("SELECT COUNT(*) FROM onions WHERE status='down'").fetchone()[0],
            "unchecked": c("SELECT COUNT(*) FROM onions WHERE status='unknown'").fetchone()[0],
            "tagged": c("SELECT COUNT(DISTINCT address) FROM tags").fetchone()[0],
            "sources": c("SELECT COUNT(DISTINCT source) FROM sightings").fetchone()[0],
            "blocked_csam": c("SELECT COUNT(*) FROM onions WHERE status='blocked'").fetchone()[0],
        }
