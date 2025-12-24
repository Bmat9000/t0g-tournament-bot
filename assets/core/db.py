"""Shared SQLite helpers.

Cleanup Roadmap v1 - Step 5: DB helpers cleanup (no behavior changes).

This file centralizes the connection settings + retry wrapper used across the bot.
"""

from __future__ import annotations

import random
import sqlite3
import time
from typing import Callable, Optional, TypeVar

from .config import DB_PATH

T = TypeVar("T")


def run_db(
    fn: Callable[[], T],
    *,
    retries: int = 5,
    base_delay: float = 0.12,
    jitter: float = 0.08,
) -> T:
    """Retry wrapper for SQLite writes in WAL mode.

    Retries only `sqlite3.OperationalError` containing "locked" or "busy".
    """
    last_err: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if ("locked" not in msg) and ("busy" not in msg):
                raise

            last_err = e
            if attempt == retries:
                break

            time.sleep(base_delay * attempt + random.uniform(0, jitter))

    raise last_err  # type: ignore[misc]


def get_db_connection() -> sqlite3.Connection:
    """Create a configured SQLite connection for bot concurrency."""
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = -20000;")
    conn.execute("PRAGMA wal_autocheckpoint = 1000;")

    return conn


def with_conn(fn):
    """Decorator: open/close a DB connection automatically."""

    def wrapper(*args, **kwargs):
        conn = get_db_connection()
        try:
            return fn(conn, *args, **kwargs)
        finally:
            conn.close()

    return wrapper
