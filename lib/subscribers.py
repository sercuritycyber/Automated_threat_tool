#!/usr/bin/env python3
"""Subscriber store — schema, migrations and CRUD for the paid alert service.

Backed by `lib/db.py` (Turso in production, local SQLite for dev). All PII lives
here: email address, optional name, channel targets (Slack URL / Telegram chat
id), Stripe ids, and a consent/event audit trail.

Status lifecycle:
    pending_payment ─stripe✓─▶ unconfirmed ─email✓─▶ active ⇄ paused
    active/paused ─cancel─▶ cancelled        active ─payment fail─▶ past_due ─▶ active|cancelled
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from db import get_db  # type: ignore  (lib/ is on sys.path via callers)

STATUSES = ("pending_payment", "unconfirmed", "active", "paused", "past_due", "cancelled")
CHANNEL_KINDS = ("email", "discord", "slack", "telegram", "webhook", "ntfy")
CADENCES = ("monthly", "weekly")
TERMS_VERSION = "2026-09-02"

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE subscribers (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        email                 TEXT NOT NULL UNIQUE,
        name                  TEXT,
        tier                  TEXT NOT NULL DEFAULT 'individual',
        status                TEXT NOT NULL DEFAULT 'pending_payment',
        source                TEXT NOT NULL DEFAULT 'stripe',
        stripe_customer_id    TEXT,
        stripe_subscription_id TEXT,
        current_period_end    INTEGER,
        created_at            TEXT NOT NULL,
        confirmed_at          TEXT,
        cancelled_at          TEXT,
        notes                 TEXT
    );
    CREATE INDEX idx_sub_status ON subscribers(status);
    CREATE INDEX idx_sub_stripe_cus ON subscribers(stripe_customer_id);
    CREATE INDEX idx_sub_stripe_sub ON subscribers(stripe_subscription_id);

    CREATE TABLE channels (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
        kind          TEXT NOT NULL,
        target        TEXT NOT NULL,
        label         TEXT,
        verified      INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL,
        UNIQUE(subscriber_id, kind, target)
    );
    CREATE INDEX idx_chan_sub ON channels(subscriber_id);

    CREATE TABLE prefs (
        subscriber_id     INTEGER PRIMARY KEY REFERENCES subscribers(id) ON DELETE CASCADE,
        vendors           TEXT NOT NULL DEFAULT '[]',
        min_score_override INTEGER,
        instant           INTEGER NOT NULL DEFAULT 1,
        digest            INTEGER NOT NULL DEFAULT 1,
        updated_at        TEXT NOT NULL
    );

    CREATE TABLE consents (
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
    CREATE INDEX idx_consent_sub ON consents(subscriber_id);

    CREATE TABLE deliveries (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
        post_key      TEXT NOT NULL,
        channel_kind  TEXT NOT NULL,
        mode          TEXT NOT NULL,
        sent_at       TEXT NOT NULL,
        ok            INTEGER NOT NULL DEFAULT 1,
        error         TEXT
    );
    CREATE INDEX idx_deliv_sub ON deliveries(subscriber_id);
    CREATE INDEX idx_deliv_key ON deliveries(subscriber_id, post_key);

    CREATE TABLE events (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        subscriber_id INTEGER REFERENCES subscribers(id) ON DELETE SET NULL,
        ts            TEXT NOT NULL,
        kind          TEXT NOT NULL,
        detail        TEXT,
        ip            TEXT
    );
    CREATE INDEX idx_event_sub ON events(subscriber_id);
    CREATE INDEX idx_event_kind ON events(kind);

    CREATE TABLE stripe_events (
        id  TEXT PRIMARY KEY,
        ts  TEXT NOT NULL
    );

    CREATE TABLE kv (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """),
    (2, """
    ALTER TABLE subscribers ADD COLUMN cadence TEXT NOT NULL DEFAULT 'monthly';

    CREATE TABLE signup_intents (
        token       TEXT PRIMARY KEY,
        email       TEXT NOT NULL,
        tier        TEXT NOT NULL,
        cadence     TEXT NOT NULL,
        channel_kind   TEXT,
        channel_target TEXT,
        ip          TEXT,
        user_agent  TEXT,
        created_at  TEXT NOT NULL,
        consumed    INTEGER NOT NULL DEFAULT 0
    );
    """),
]

_ready = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db():
    global _ready
    d = get_db()
    if not _ready:
        d.migrate(MIGRATIONS)
        _ready = True
    return d


def ensure_schema() -> int:
    return get_db().migrate(MIGRATIONS)


# --------------------------------------------------------------------------- helpers
def _row(rows: list[dict]) -> dict | None:
    return rows[0] if rows else None


def _vendors_load(s: str | None) -> list[str]:
    try:
        v = json.loads(s or "[]")
        return [str(x).strip() for x in v if str(x).strip()]
    except (ValueError, TypeError):
        return []


def _hydrate(sub: dict | None) -> dict | None:
    if not sub:
        return None
    sub = dict(sub)
    sub["channels"] = channels_for(sub["id"])
    sub["prefs"] = get_prefs(sub["id"])
    return sub


# --------------------------------------------------------------------------- create / fetch
def create_pending(email: str, tier: str, source: str = "stripe",
                   stripe_customer_id: str | None = None, name: str | None = None,
                   cadence: str = "monthly") -> int:
    email = email.strip().lower()
    cadence = cadence if cadence in CADENCES else "monthly"
    d = db()
    existing = get_by_email(email)
    if existing:
        # returning subscriber — reuse the row, move it back to pending
        d.execute("UPDATE subscribers SET tier=?, cadence=?, source=?, "
                  "stripe_customer_id=COALESCE(?, stripe_customer_id), "
                  "status=CASE WHEN status IN ('cancelled') THEN 'pending_payment' ELSE status END WHERE id=?",
                  [tier, cadence, source, stripe_customer_id, existing["id"]])
        return existing["id"]
    res = d.execute(
        "INSERT INTO subscribers(email, name, tier, cadence, status, source, stripe_customer_id, created_at) "
        "VALUES (?, ?, ?, ?, 'pending_payment', ?, ?, ?)",
        [email, name, tier, cadence, source, stripe_customer_id, _now()])
    sid = res.lastrowid
    d.execute("INSERT INTO prefs(subscriber_id, updated_at) VALUES (?, ?)", [sid, _now()])
    d.execute("INSERT INTO channels(subscriber_id, kind, target, verified, created_at) "
              "VALUES (?, 'email', ?, 0, ?)", [sid, email, _now()])
    log_event("created", sid, {"tier": tier, "source": source})
    return sid


def get(subscriber_id: int, hydrate: bool = True) -> dict | None:
    r = _row(db().query("SELECT * FROM subscribers WHERE id = ?", [subscriber_id]))
    return _hydrate(r) if hydrate else r


def get_by_email(email: str, hydrate: bool = False) -> dict | None:
    r = _row(db().query("SELECT * FROM subscribers WHERE email = ?", [email.strip().lower()]))
    return _hydrate(r) if hydrate else r


def get_by_stripe_customer(cid: str) -> dict | None:
    return _row(db().query("SELECT * FROM subscribers WHERE stripe_customer_id = ?", [cid]))


def get_by_stripe_subscription(sid: str) -> dict | None:
    return _row(db().query("SELECT * FROM subscribers WHERE stripe_subscription_id = ?", [sid]))


def list_subscribers(status: str | None = None, tier: str | None = None, q: str | None = None,
                     limit: int = 200, offset: int = 0) -> list[dict]:
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if tier:
        where.append("tier = ?")
        args.append(tier)
    if q:
        where.append("(email LIKE ? OR name LIKE ? OR notes LIKE ?)")
        args += [f"%{q}%"] * 3
    sql = "SELECT * FROM subscribers"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return db().query(sql, args)


# --------------------------------------------------------------------------- status
def set_status(subscriber_id: int, status: str, reason: str = "", ip: str | None = None) -> None:
    assert status in STATUSES, status
    extra = ""
    if status == "active":
        extra = ", confirmed_at = COALESCE(confirmed_at, ?)"
    elif status == "cancelled":
        extra = ", cancelled_at = ?"
    args = [status]
    if extra:
        args.append(_now())
    args.append(subscriber_id)
    db().execute(f"UPDATE subscribers SET status = ?{extra} WHERE id = ?", args)
    log_event("status:" + status, subscriber_id, {"reason": reason}, ip)


def activate(subscriber_id: int, stripe_subscription_id: str | None = None,
             current_period_end: int | None = None) -> None:
    db().execute("UPDATE subscribers SET stripe_subscription_id = COALESCE(?, stripe_subscription_id), "
                 "current_period_end = COALESCE(?, current_period_end) WHERE id = ?",
                 [stripe_subscription_id, current_period_end, subscriber_id])
    set_status(subscriber_id, "active", "activated")


def set_cadence(subscriber_id: int, cadence: str) -> None:
    if cadence in CADENCES:
        db().execute("UPDATE subscribers SET cadence = ? WHERE id = ?", [cadence, subscriber_id])


# --------------------------------------------------------------------------- signup intents
def create_intent(token: str, email: str, tier: str, cadence: str,
                  channel_kind: str | None, channel_target: str | None,
                  ip: str | None = None, user_agent: str | None = None) -> None:
    db().execute(
        "INSERT INTO signup_intents(token, email, tier, cadence, channel_kind, channel_target, "
        "ip, user_agent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [token, email.strip().lower(), tier, cadence, channel_kind, channel_target,
         ip, user_agent, _now()])


def get_intent(token: str) -> dict | None:
    return _row(db().query("SELECT * FROM signup_intents WHERE token = ? AND consumed = 0", [token]))


def consume_intent(token: str) -> None:
    db().execute("UPDATE signup_intents SET consumed = 1 WHERE token = ?", [token])


def purge_intents(hours: int = 48) -> int:
    cut = datetime.fromtimestamp(time.time() - hours * 3600, timezone.utc).isoformat()
    r = db().execute("DELETE FROM signup_intents WHERE created_at < ? OR consumed = 1", [cut])
    return r.rowcount or 0


def set_stripe_ids(subscriber_id: int, customer_id: str | None = None,
                   subscription_id: str | None = None, period_end: int | None = None) -> None:
    db().execute("UPDATE subscribers SET "
                 "stripe_customer_id = COALESCE(?, stripe_customer_id), "
                 "stripe_subscription_id = COALESCE(?, stripe_subscription_id), "
                 "current_period_end = COALESCE(?, current_period_end) WHERE id = ?",
                 [customer_id, subscription_id, period_end, subscriber_id])


# --------------------------------------------------------------------------- channels
def channels_for(subscriber_id: int) -> list[dict]:
    return db().query("SELECT * FROM channels WHERE subscriber_id = ? ORDER BY id", [subscriber_id])


def add_channel(subscriber_id: int, kind: str, target: str, verified: bool = False,
                label: str | None = None) -> int:
    assert kind in CHANNEL_KINDS, kind
    d = db()
    d.execute("INSERT INTO channels(subscriber_id, kind, target, label, verified, created_at) "
              "VALUES (?, ?, ?, ?, ?, ?) "
              "ON CONFLICT(subscriber_id, kind, target) DO UPDATE SET "
              "verified = excluded.verified, label = excluded.label",
              [subscriber_id, kind, target.strip(), label, 1 if verified else 0, _now()])
    r = _row(d.query("SELECT id FROM channels WHERE subscriber_id=? AND kind=? AND target=?",
                     [subscriber_id, kind, target.strip()]))
    log_event("channel:add:" + kind, subscriber_id, {"target": _mask(target)})
    return r["id"] if r else 0


def verify_channel(channel_id: int) -> None:
    db().execute("UPDATE channels SET verified = 1 WHERE id = ?", [channel_id])


def remove_channel(channel_id: int) -> None:
    db().execute("DELETE FROM channels WHERE id = ?", [channel_id])


def _mask(s: str) -> str:
    s = s or ""
    if "@" in s:
        u, _, dom = s.partition("@")
        return (u[:2] + "***@" + dom)
    return s[:6] + "…" if len(s) > 9 else s


# --------------------------------------------------------------------------- prefs
def get_prefs(subscriber_id: int) -> dict:
    r = _row(db().query("SELECT * FROM prefs WHERE subscriber_id = ?", [subscriber_id]))
    if not r:
        db().execute("INSERT INTO prefs(subscriber_id, updated_at) VALUES (?, ?)",
                     [subscriber_id, _now()])
        r = {"subscriber_id": subscriber_id, "vendors": "[]", "min_score_override": None,
             "instant": 1, "digest": 1}
    return {"vendors": _vendors_load(r.get("vendors")),
            "min_score_override": r.get("min_score_override"),
            "instant": bool(r.get("instant", 1)),
            "digest": bool(r.get("digest", 1))}


def set_prefs(subscriber_id: int, *, vendors: list[str] | None = None,
              min_score_override: int | None = ..., instant: bool | None = None,
              digest: bool | None = None) -> None:
    cur = get_prefs(subscriber_id)
    v = json.dumps(vendors if vendors is not None else cur["vendors"])
    mso = cur["min_score_override"] if min_score_override is ... else min_score_override
    ins = cur["instant"] if instant is None else bool(instant)
    dig = cur["digest"] if digest is None else bool(digest)
    db().execute("UPDATE prefs SET vendors=?, min_score_override=?, instant=?, digest=?, updated_at=? "
                 "WHERE subscriber_id=?", [v, mso, 1 if ins else 0, 1 if dig else 0, _now(), subscriber_id])
    log_event("prefs:update", subscriber_id)


# --------------------------------------------------------------------------- consent / events
def log_consent(subscriber_id: int | None, kind: str, granted: bool, ip: str | None = None,
                user_agent: str | None = None, terms_version: str | None = None,
                detail: str | None = None) -> None:
    db().execute("INSERT INTO consents(subscriber_id, kind, granted, ts, ip, user_agent, terms_version, detail) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 [subscriber_id, kind, 1 if granted else 0, _now(), ip, user_agent,
                  terms_version or TERMS_VERSION, detail])


def consents_for(subscriber_id: int) -> list[dict]:
    return db().query("SELECT * FROM consents WHERE subscriber_id = ? ORDER BY ts", [subscriber_id])


def log_event(kind: str, subscriber_id: int | None = None, detail=None, ip: str | None = None) -> None:
    db().execute("INSERT INTO events(subscriber_id, ts, kind, detail, ip) VALUES (?, ?, ?, ?, ?)",
                 [subscriber_id, _now(), kind,
                  json.dumps(detail) if detail is not None else None, ip])


def events_for(subscriber_id: int, limit: int = 100) -> list[dict]:
    return db().query("SELECT * FROM events WHERE subscriber_id = ? ORDER BY ts DESC LIMIT ?",
                      [subscriber_id, limit])


# --------------------------------------------------------------------------- deliveries
def already_delivered(subscriber_id: int, post_key: str) -> bool:
    return bool(db().query(
        "SELECT 1 FROM deliveries WHERE subscriber_id = ? AND post_key = ? AND ok = 1 LIMIT 1",
        [subscriber_id, post_key]))


def record_delivery(subscriber_id: int, post_key: str, channel_kind: str, mode: str,
                    ok: bool = True, error: str | None = None) -> None:
    db().execute("INSERT INTO deliveries(subscriber_id, post_key, channel_kind, mode, sent_at, ok, error) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 [subscriber_id, post_key, channel_kind, mode, _now(), 1 if ok else 0, error])


def deliveries_for(subscriber_id: int, limit: int = 50) -> list[dict]:
    return db().query("SELECT * FROM deliveries WHERE subscriber_id = ? ORDER BY sent_at DESC LIMIT ?",
                      [subscriber_id, limit])


# --------------------------------------------------------------------------- stripe idempotency
def seen_stripe_event(event_id: str) -> bool:
    return bool(db().query("SELECT 1 FROM stripe_events WHERE id = ?", [event_id]))


def mark_stripe_event(event_id: str) -> None:
    db().execute("INSERT INTO stripe_events(id, ts) VALUES (?, ?) ON CONFLICT(id) DO NOTHING",
                 [event_id, _now()])


# --------------------------------------------------------------------------- kv
def kv_get(key: str, default=None):
    r = _row(db().query("SELECT value FROM kv WHERE key = ?", [key]))
    return r["value"] if r else default


def kv_set(key: str, value: str) -> None:
    db().execute("INSERT INTO kv(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", [key, str(value)])


# --------------------------------------------------------------------------- delivery engine feed
def active_for_delivery() -> list[dict]:
    """Hydrated active/paused subscribers with channels + prefs, ready to fan out."""
    subs = db().query("SELECT * FROM subscribers WHERE status IN ('active', 'paused')")
    return [_hydrate(s) for s in subs]


# --------------------------------------------------------------------------- DSAR
def export_subscriber(subscriber_id: int) -> dict:
    s = get(subscriber_id, hydrate=False)
    if not s:
        return {}
    return {
        "subscriber": s,
        "channels": channels_for(subscriber_id),
        "prefs": get_prefs(subscriber_id),
        "consents": consents_for(subscriber_id),
        "deliveries": deliveries_for(subscriber_id, limit=10000),
        "events": events_for(subscriber_id, limit=10000),
        "exported_at": _now(),
    }


def erase_subscriber(subscriber_id: int, reason: str = "DSAR erasure") -> None:
    """Hard-delete all PII. Leaves one anonymised audit event (no PII in it)."""
    d = db()
    s = get(subscriber_id, hydrate=False) or {}
    d.execute("DELETE FROM subscribers WHERE id = ?", [subscriber_id])  # cascades
    d.execute("DELETE FROM consents WHERE subscriber_id = ?", [subscriber_id])
    d.execute("DELETE FROM events WHERE subscriber_id = ?", [subscriber_id])
    d.execute("INSERT INTO events(subscriber_id, ts, kind, detail, ip) VALUES (NULL, ?, 'erased', ?, NULL)",
              [_now(), json.dumps({"reason": reason, "tier": s.get("tier"),
                                   "had_stripe": bool(s.get("stripe_customer_id"))})])


# --------------------------------------------------------------------------- retention
def purge_unconfirmed(days: int) -> int:
    cut = _iso_days_ago(days)
    ids = [r["id"] for r in db().query(
        "SELECT id FROM subscribers WHERE status IN ('pending_payment','unconfirmed') AND created_at < ?", [cut])]
    for i in ids:
        erase_subscriber(i, f"retention: unconfirmed > {days}d")
    return len(ids)


def purge_cancelled(days: int) -> int:
    cut = _iso_days_ago(days)
    ids = [r["id"] for r in db().query(
        "SELECT id FROM subscribers WHERE status = 'cancelled' AND "
        "COALESCE(cancelled_at, created_at) < ?", [cut])]
    for i in ids:
        erase_subscriber(i, f"retention: cancelled > {days}d")
    return len(ids)


def _iso_days_ago(days: int) -> str:
    return datetime.fromtimestamp(time.time() - days * 86400, timezone.utc).isoformat()


# --------------------------------------------------------------------------- stats
def stats() -> dict:
    rows = db().query("SELECT status, tier, COUNT(*) AS n FROM subscribers GROUP BY status, tier")
    by_status: dict[str, int] = {}
    by_tier_active: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + r["n"]
        if r["status"] in ("active", "paused", "past_due"):
            by_tier_active[r["tier"]] = by_tier_active.get(r["tier"], 0) + r["n"]
    sent_today = db().query(
        "SELECT COUNT(*) AS n FROM deliveries WHERE ok = 1 AND sent_at > ?",
        [_iso_days_ago(1)])[0]["n"]
    return {"by_status": by_status, "active_by_tier": by_tier_active,
            "total_active": sum(by_tier_active.values()), "sent_24h": sent_today}


if __name__ == "__main__":
    n = ensure_schema()
    d = get_db()
    print(f"backend={d.backend} schema_version={d.schema_version()} migrations_applied={n}")
