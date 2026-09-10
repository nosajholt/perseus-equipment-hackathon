"""Read-only SQLite access layer with a small TTL cache.

The source database is treated as an immutable artifact: every connection is
opened with `mode=ro` so no code path in this app can write to it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

DB_FILENAME = "perseus_equipment_database.db"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_db_path() -> Path:
    """Locate the database file: `PERSEUS_DB` if set, else the repo-root copy.

    The database is too large to commit, so a clone starts without it and the
    owner drops their own copy into the repo root. That default is resolved
    against this file rather than the process working directory, so the app
    behaves the same however it was launched.
    """
    override = os.environ.get("PERSEUS_DB")
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / DB_FILENAME


DB_PATH = _resolve_db_path()
DB_URI = f"file:{DB_PATH.as_posix()}?mode=ro"

# Aggregations over the full 351k-row invoice detail table take ~1.3s, so cached
# results are held long enough that repeated dashboard loads feel instant.
CACHE_TTL_SECONDS = 900

_local = threading.local()
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_inflight: dict[str, threading.Lock] = {}


def get_connection() -> sqlite3.Connection:
    """Return this thread's read-only connection, creating it on first use.

    FastAPI dispatches sync endpoints across a thread pool, and SQLite
    connections cannot be shared between threads.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        # `mode=ro` reports a missing file as a bare "unable to open database
        # file", which gives no hint about where it was looked for.
        if not DB_PATH.is_file():
            raise FileNotFoundError(
                f"Perseus database not found at {DB_PATH}. Copy {DB_FILENAME} "
                f"into the repository root ({REPO_ROOT}), or set the PERSEUS_DB "
                "environment variable to the full path of your copy."
            )
        conn = sqlite3.connect(DB_URI, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA cache_size = -64000")
        conn.execute("PRAGMA temp_store = MEMORY")
        _local.conn = conn
    return conn


def query(sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[dict[str, Any]]:
    """Run a SELECT and return rows as plain dicts."""
    cur = get_connection().execute(sql, params)
    try:
        return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()


def query_one(sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
    """Return the first column of the first row, or None."""
    cur = get_connection().execute(sql, params)
    try:
        row = cur.fetchone()
    finally:
        cur.close()
    return row[0] if row else None


def _cache_key(name: str, payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str)
    return f"{name}:{hashlib.sha1(blob.encode()).hexdigest()}"


def cached(name: str, payload: Any, producer: Callable[[], Any]) -> Any:
    """Memoize `producer()` under a TTL, keyed by name plus its arguments."""
    key = _cache_key(name, payload)

    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        gate = _inflight.setdefault(key, threading.Lock())

    # A tab loads its panels in parallel and they share the same underlying
    # aggregation, so concurrent misses wait for the first producer rather than
    # each running the same multi-second query.
    with gate:
        now = time.time()
        with _cache_lock:
            hit = _cache.get(key)
            if hit and hit[0] > now:
                return hit[1]

        value = producer()

        with _cache_lock:
            _cache[key] = (now + CACHE_TTL_SECONDS, value)
            _inflight.pop(key, None)
        return value


def cache_stats() -> dict[str, Any]:
    now = time.time()
    with _cache_lock:
        live = sum(1 for expiry, _ in _cache.values() if expiry > now)
        return {"entries": len(_cache), "live": live, "ttl_seconds": CACHE_TTL_SECONDS}


def table_names() -> Iterable[str]:
    return [
        r["name"]
        for r in query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]
