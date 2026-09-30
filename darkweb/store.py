#!/usr/bin/env python3
"""SQLite storage for the dark-web collector — stdlib only.

Three tables:

* ``pages``    — one row per (site, url) with the latest fetch metadata.
* ``snapshots`` — one row per distinct content version of a page (dedup by
  sha256 of the extracted text), so we keep history without storing the same
  content twice.
* ``hits``     — matcher output: a watchlist term / honeypot IOC found in a
  snapshot, with surrounding context for the analyst.

Everything is keyed so the collector is idempotent: re-fetching an unchanged
page inserts no new snapshot.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id            INTEGER PRIMARY KEY,
    site          TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT,
    first_seen    REAL NOT NULL,
    last_seen     REAL NOT NULL,
    last_status   INTEGER,
    last_sha256   TEXT,
    fetch_count   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(site, url)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY,
    page_id       INTEGER NOT NULL REFERENCES pages(id),
    fetched_at    REAL NOT NULL,
    sha256        TEXT NOT NULL,
    status        INTEGER,
    content_type  TEXT,
    bytes_len     INTEGER,
    text          TEXT NOT NULL,
    UNIQUE(page_id, sha256)
);

CREATE TABLE IF NOT EXISTS hits (
    id            INTEGER PRIMARY KEY,
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(id),
    page_id       INTEGER NOT NULL REFERENCES pages(id),
    found_at      REAL NOT NULL,
    kind          TEXT NOT NULL,     -- watchlist | honeypot-ip | honeypot-cred | ...
    indicator     TEXT NOT NULL,     -- the thing we matched
    context       TEXT,              -- surrounding text
    reviewed      INTEGER NOT NULL DEFAULT 0,
    UNIQUE(snapshot_id, kind, indicator, context)
);

CREATE INDEX IF NOT EXISTS ix_snap_page ON snapshots(page_id);
CREATE INDEX IF NOT EXISTS ix_hits_page ON hits(page_id);
CREATE INDEX IF NOT EXISTS ix_hits_kind ON hits(kind);
"""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


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

    # -- pages / snapshots -------------------------------------------------- #
    def record_fetch(
        self, site: str, url: str, *, title: str | None,
        status: int, content_type: str, bytes_len: int, text: str,
        now: float | None = None,
    ) -> tuple[int, int | None, bool]:
        """Upsert a page and insert a snapshot if the content is new.

        Returns ``(page_id, snapshot_id_or_None, is_new_content)``.
        """
        now = time.time() if now is None else now
        sha = sha256_text(text)
        cur = self.db.cursor()
        row = cur.execute(
            "SELECT id, first_seen FROM pages WHERE site=? AND url=?", (site, url)
        ).fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO pages(site,url,title,first_seen,last_seen,last_status,"
                "last_sha256,fetch_count) VALUES(?,?,?,?,?,?,?,1)",
                (site, url, title, now, now, status, sha),
            )
            page_id = cur.lastrowid
        else:
            page_id = row["id"]
            cur.execute(
                "UPDATE pages SET last_seen=?,last_status=?,last_sha256=?,title=COALESCE(?,title),"
                "fetch_count=fetch_count+1 WHERE id=?",
                (now, status, sha, title, page_id),
            )
        # New content version?
        existing = cur.execute(
            "SELECT id FROM snapshots WHERE page_id=? AND sha256=?", (page_id, sha)
        ).fetchone()
        if existing:
            self.db.commit()
            return page_id, existing["id"], False
        cur.execute(
            "INSERT INTO snapshots(page_id,fetched_at,sha256,status,content_type,"
            "bytes_len,text) VALUES(?,?,?,?,?,?,?)",
            (page_id, now, sha, status, content_type, bytes_len, text),
        )
        snap_id = cur.lastrowid
        self.db.commit()
        return page_id, snap_id, True

    def iter_snapshots(self, *, only_new_since: float | None = None):
        q = "SELECT s.*, p.site AS site, p.url AS url FROM snapshots s JOIN pages p ON p.id=s.page_id"
        args: tuple = ()
        if only_new_since is not None:
            q += " WHERE s.fetched_at >= ?"
            args = (only_new_since,)
        q += " ORDER BY s.fetched_at"
        yield from self.db.execute(q, args)

    def latest_snapshots(self):
        """The newest snapshot per page (what the matcher scans by default)."""
        q = (
            "SELECT s.*, p.site AS site, p.url AS url FROM snapshots s "
            "JOIN pages p ON p.id=s.page_id "
            "WHERE s.id = (SELECT id FROM snapshots s2 WHERE s2.page_id=s.page_id "
            "ORDER BY s2.fetched_at DESC LIMIT 1)"
        )
        yield from self.db.execute(q)

    # -- hits --------------------------------------------------------------- #
    def add_hit(self, snapshot_id: int, page_id: int, kind: str,
                indicator: str, context: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        try:
            self.db.execute(
                "INSERT INTO hits(snapshot_id,page_id,found_at,kind,indicator,context)"
                " VALUES(?,?,?,?,?,?)",
                (snapshot_id, page_id, now, kind, indicator, context[:1000]),
            )
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # already recorded

    def hits(self, *, unreviewed_only: bool = False, since: float | None = None):
        q = ("SELECT h.*, p.site AS site, p.url AS url FROM hits h "
             "JOIN pages p ON p.id=h.page_id WHERE 1=1")
        args: list = []
        if unreviewed_only:
            q += " AND h.reviewed=0"
        if since is not None:
            q += " AND h.found_at>=?"
            args.append(since)
        q += " ORDER BY h.found_at DESC"
        return self.db.execute(q, args).fetchall()

    def stats(self) -> dict:
        c = self.db.execute
        return {
            "pages": c("SELECT COUNT(*) FROM pages").fetchone()[0],
            "snapshots": c("SELECT COUNT(*) FROM snapshots").fetchone()[0],
            "hits": c("SELECT COUNT(*) FROM hits").fetchone()[0],
            "unreviewed_hits": c("SELECT COUNT(*) FROM hits WHERE reviewed=0").fetchone()[0],
            "sites": c("SELECT COUNT(DISTINCT site) FROM pages").fetchone()[0],
        }
