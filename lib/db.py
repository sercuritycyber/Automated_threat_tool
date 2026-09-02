#!/usr/bin/env python3
"""Backend-agnostic tiny DB layer for the subscription service.

Two interchangeable backends, same API:

  * Turso / libSQL  — used when TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set.
    Talks the HTTP "pipeline" API with `requests`; no native libsql needed.
  * local SQLite    — `state/subscribers.db` (WAL), stdlib `sqlite3`. Dev/test.

SQL is written once and runs on both (libSQL is SQLite-compatible). Use `?`
placeholders and positional params.

    db = get_db()
    db.migrate(MIGRATIONS)
    db.execute("INSERT INTO t(a) VALUES (?)", ["x"])
    rows = db.query("SELECT * FROM t WHERE a = ?", ["x"])   # -> list[dict]
    db.tx([("INSERT INTO t(a) VALUES (?)", ["y"]),
           ("UPDATE t SET a=? WHERE a=?", ["z", "y"])])      # atomic
"""
from __future__ import annotations

import base64
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = ROOT / "state" / "subscribers.db"

_INSTANCE: "DB | None" = None
_LOCK = threading.RLock()


def get_db() -> "DB":
    global _INSTANCE
    with _LOCK:
        if _INSTANCE is None:
            url = os.getenv("TURSO_DATABASE_URL", "").strip()
            token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
            if url and token:
                _INSTANCE = TursoDB(url, token)
            else:
                _INSTANCE = SqliteDB(os.getenv("SUBS_DB_PATH") or str(DEFAULT_SQLITE_PATH))
        return _INSTANCE


def reset_db_singleton() -> None:
    """tests only."""
    global _INSTANCE
    with _LOCK:
        if _INSTANCE is not None:
            _INSTANCE.close()
        _INSTANCE = None


# --------------------------------------------------------------------------- base
class DB:
    backend = "?"

    def execute(self, sql: str, params: Sequence[Any] = ()) -> "Result":
        raise NotImplementedError

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        raise NotImplementedError

    def tx(self, statements: Iterable[tuple[str, Sequence[Any]]]) -> list["Result"]:
        raise NotImplementedError

    def executescript(self, script: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    # -- shared migration runner -------------------------------------------------
    def migrate(self, migrations: Sequence[tuple[int, str]]) -> int:
        self.execute("CREATE TABLE IF NOT EXISTS _meta "
                     "(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        cur = self.query("SELECT value FROM _meta WHERE key = 'schema_version'")
        have = int(cur[0]["value"]) if cur else 0
        applied = 0
        for version, script in sorted(migrations):
            if version <= have:
                continue
            for stmt in _split_sql(script):
                self.execute(stmt)
            self.execute(
                "INSERT INTO _meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", [str(version)])
            have = version
            applied += 1
        return applied

    def schema_version(self) -> int:
        try:
            r = self.query("SELECT value FROM _meta WHERE key = 'schema_version'")
            return int(r[0]["value"]) if r else 0
        except Exception:  # noqa
            return 0


class Result:
    def __init__(self, rowcount: int = 0, lastrowid: int | None = None):
        self.rowcount = rowcount
        self.lastrowid = lastrowid


def _split_sql(script: str) -> list[str]:
    """Naive `;` splitter — fine for our migration scripts (no ; inside strings
    or triggers). Keeps things dependency-free."""
    out, buf = [], []
    for line in script.splitlines():
        s = line.strip()
        if not s or s.startswith("--"):
            continue
        buf.append(line)
        if s.endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                out.append(stmt)
            buf = []
    tail = "\n".join(buf).strip().rstrip(";").strip()
    if tail:
        out.append(tail)
    return out


# --------------------------------------------------------------------------- sqlite
class SqliteDB(DB):
    backend = "sqlite"

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=8000")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Result:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return Result(cur.rowcount, cur.lastrowid)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def tx(self, statements: Iterable[tuple[str, Sequence[Any]]]) -> list[Result]:
        with self._lock:
            out = []
            try:
                for sql, params in statements:
                    cur = self._conn.execute(sql, tuple(params))
                    out.append(Result(cur.rowcount, cur.lastrowid))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return out

    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)
            self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa
            pass


# --------------------------------------------------------------------------- turso
def _encode_arg(v: Any) -> dict:
    if v is None:
        return {"type": "null", "value": None}
    if isinstance(v, bool):
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode()}
    return {"type": "text", "value": str(v)}


def _decode_cell(c: dict) -> Any:
    t = c.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(c["value"])
    if t == "float":
        return float(c["value"])
    if t == "blob":
        return base64.b64decode(c.get("base64", ""))
    return c.get("value")


class TursoDB(DB):
    backend = "turso"

    def __init__(self, url: str, token: str):
        u = url.strip()
        if u.startswith("libsql://"):
            u = "https://" + u[len("libsql://"):]
        self.endpoint = u.rstrip("/") + "/v2/pipeline"
        self.token = token
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}",
                                      "Content-Type": "application/json"})

    def _pipeline(self, stmts: list[tuple[str, Sequence[Any]]]) -> list[dict]:
        reqs = [{"type": "execute",
                 "stmt": {"sql": sql, "args": [_encode_arg(a) for a in params]}}
                for sql, params in stmts]
        reqs.append({"type": "close"})
        last_err = None
        for attempt in range(3):
            try:
                r = self._session.post(self.endpoint, json={"requests": reqs}, timeout=30)
                if r.status_code == 401:
                    raise RuntimeError("Turso auth rejected (check TURSO_AUTH_TOKEN)")
                if r.status_code >= 500:
                    last_err = RuntimeError(f"Turso HTTP {r.status_code}")
                    time.sleep(0.4 * (attempt + 1))
                    continue
                r.raise_for_status()
                break
            except requests.RequestException as e:
                last_err = e
                time.sleep(0.4 * (attempt + 1))
        else:
            raise RuntimeError(f"Turso request failed: {last_err}")
        body = r.json()
        results = body.get("results", [])
        out = []
        for res in results:
            if res.get("type") == "error":
                raise RuntimeError(f"Turso SQL error: {res.get('error', {}).get('message', res)}")
            resp = res.get("response") or {}
            if resp.get("type") == "execute":
                out.append(resp.get("result", {}))
        return out

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Result:
        res = self._pipeline([(sql, params)])
        r = res[0] if res else {}
        lid = r.get("last_insert_rowid")
        return Result(int(r.get("affected_row_count", 0) or 0),
                      int(lid) if lid not in (None, "") else None)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        res = self._pipeline([(sql, params)])
        r = res[0] if res else {}
        cols = [c["name"] for c in r.get("cols", [])]
        return [{cols[i]: _decode_cell(cell) for i, cell in enumerate(row)}
                for row in r.get("rows", [])]

    def tx(self, statements: Iterable[tuple[str, Sequence[Any]]]) -> list[Result]:
        stmts = list(statements)
        wrapped = [("BEGIN", [])] + stmts + [("COMMIT", [])]
        res = self._pipeline(wrapped)
        # res excludes BEGIN/COMMIT only if they returned execute results; they do,
        # so drop first and last
        inner = res[1:-1] if len(res) >= 2 else res
        out = []
        for r in inner:
            lid = r.get("last_insert_rowid")
            out.append(Result(int(r.get("affected_row_count", 0) or 0),
                              int(lid) if lid not in (None, "") else None))
        return out

    def executescript(self, script: str) -> None:
        self._pipeline([(s, []) for s in _split_sql(script)])

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:  # noqa
            pass


if __name__ == "__main__":
    d = get_db()
    print(f"backend: {d.backend}  schema_version: {d.schema_version()}")
