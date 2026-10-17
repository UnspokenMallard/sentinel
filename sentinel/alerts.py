"""The alert record: one row per key, forever, with a count that rises on every raise."""
from __future__ import annotations

import time

from . import store


def record(key: str, message: str, severity: str = "warn") -> int:
    """Upsert the key's row and return its new count. Raises on a database error: the caller decides."""
    now = time.time()
    with store.connect() as conn:
        row = conn.execute("SELECT count FROM alerts WHERE key = ?", (key,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO alerts(key, message, severity, first_seen, last_seen, count) VALUES(?, ?, ?, ?, ?, 1)",
                (key, message, severity, now, now),
            )
            return 1
        count = row[0] + 1
        conn.execute("UPDATE alerts SET message = ?, severity = ?, last_seen = ?, count = ? WHERE key = ?",
                     (message, severity, now, count, key))
        return count


def get(key: str) -> dict | None:
    with store.connect() as conn:
        row = conn.execute("SELECT message, severity, first_seen, last_seen, count FROM alerts WHERE key = ?",
                           (key,)).fetchone()
    return dict(zip(("message", "severity", "first_seen", "last_seen", "count"), row)) if row else None


def recent(limit: int = 15) -> list[tuple]:
    """(key, message, severity, last_seen, count), newest first."""
    with store.connect() as conn:
        return conn.execute(
            "SELECT key, message, severity, last_seen, count FROM alerts ORDER BY last_seen DESC LIMIT ?", (limit,)
        ).fetchall()


def count_since(since: float) -> int:
    """How many distinct keys fired at or after `since`."""
    with store.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM alerts WHERE last_seen > ?", (since,)).fetchone()[0]


def sum_since(since: float) -> int:
    """Total raises of keys last seen at or after `since`."""
    with store.connect() as conn:
        return conn.execute("SELECT COALESCE(SUM(count), 0) FROM alerts WHERE last_seen >= ?", (since,)).fetchone()[0]
