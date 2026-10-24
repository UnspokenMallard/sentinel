"""The alert record: one Mimir record per key (`heimdall.alert`), forever, with a count that rises on every raise."""
from __future__ import annotations

import time

from . import store
from ._mimir import records as mr

KIND = "heimdall.alert"


def record(key: str, message: str, severity: str = "warn") -> int:
    """Count one raise of the key and return its new count. The write waits in the outbox when Mimir is
    down and the count then reads 1 (the caller only uses it for a "(xN)" suffix)."""
    now = time.time()
    mr.bump(store.SOURCE, KIND, key, {"count": 1}, {"message": message, "severity": severity, "last_seen": now},
            {"first_seen": now}, at=now)
    try:
        return int((mr.get(KIND, key, source=store.SOURCE) or {}).get("count", 1))
    except mr.MimirDown:
        return 1


def get(key: str) -> dict | None:
    row = mr.get(KIND, key, source=store.SOURCE)
    return {c: row.get(c) for c in ("message", "severity", "first_seen", "last_seen", "count")} if row else None


def _rows(since: float | None = None, order: str = "asc", limit: int = 5000) -> list[dict]:
    return [{**r["data"], "key": r["key"]} for r in mr.query(kind=KIND, since=since, order=order, limit=limit, source=store.SOURCE)]


def recent(limit: int = 15) -> list[tuple]:
    """(key, message, severity, last_seen, count), newest first."""
    return [(r["key"], r["message"], r["severity"], r["last_seen"], r["count"]) for r in _rows(order="desc", limit=limit)]


def count_since(since: float) -> int:
    """How many distinct keys fired after `since`."""
    return int(mr.agg("count", kind=KIND, since=since, source=store.SOURCE) or 0)


def sum_since(since: float, key_prefix: str | None = None) -> int:
    """Total raises of keys last seen after `since`, optionally only keys starting with `key_prefix`."""
    if not key_prefix:
        return int(mr.agg("sum", kind=KIND, field="count", since=since, source=store.SOURCE) or 0)
    return sum(r["count"] for r in _rows(since) if r["key"].startswith(key_prefix))


def since(since_ts: float) -> list[tuple]:
    """(key, message, count, last_seen) for keys last seen at or after `since_ts`, oldest first."""
    return [(r["key"], r["message"], r["count"], r["last_seen"]) for r in _rows(since_ts)]
