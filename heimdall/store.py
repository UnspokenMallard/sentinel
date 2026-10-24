"""Heimdall's store: state/sentinel.db (WAL), state files, config.toml.

Paths are module attributes read at call time, so a test points them at a scratch directory
(`store.ROOT`, `store.STATE`, `store.DB_PATH`, `store.CONFIG`, `store.ALERTS_NOW`) the way
Fafnir's tests do. Stdlib only: imported by Jarvis's venv and Heimdall's own.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import tomllib
from pathlib import Path

ROOT = Path(os.environ.get("HEIMDALL_HOME", Path.home() / ".sentinel"))
STATE = ROOT / "state"
DB_PATH = STATE / "sentinel.db"
CONFIG = ROOT / "config.toml"
log = logging.getLogger("heimdall")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- One row per alert key, forever; `count` keeps rising on every raise. Delivery state (when a line was
-- last written or pushed) is Jarvis's, not here.
CREATE TABLE IF NOT EXISTS alerts (
  key        TEXT PRIMARY KEY,
  message    TEXT NOT NULL,
  severity   TEXT NOT NULL DEFAULT 'warn',
  first_seen REAL NOT NULL,
  last_seen  REAL NOT NULL,
  count      INTEGER NOT NULL DEFAULT 1
);

-- Alerts with a lifecycle: one open incident per kind (the part of the key before the first ':'),
-- held by the partial unique index. Resolved rows are unconstrained, so a recurring problem keeps its
-- history.
CREATE TABLE IF NOT EXISTS incidents (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  kind         TEXT NOT NULL,
  summary      TEXT NOT NULL,
  severity     TEXT NOT NULL DEFAULT 'warn',
  opened_at    REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  hits         INTEGER NOT NULL DEFAULT 1,
  members      TEXT NOT NULL DEFAULT '[]',
  member_count INTEGER NOT NULL DEFAULT 1,
  resolved_at  REAL,
  resolved_by  TEXT,
  resolution   TEXT,
  evidence     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_incidents_open ON incidents(kind) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_incidents_opened ON incidents(opened_at);
CREATE INDEX IF NOT EXISTS ix_incidents_resolved ON incidents(resolved_at);
"""


@contextlib.contextmanager
def connect():
    """`with connect() as conn:` commits or rolls back, and always closes (one handle per call)."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        with conn:
            conn.executescript(_SCHEMA)
            yield conn
    finally:
        conn.close()


def load_config() -> dict:
    """~/.sentinel/config.toml; missing means every module's own defaults."""
    try:
        with open(CONFIG, "rb") as f:
            return tomllib.load(f)
    except OSError:
        return {}


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))


def get_meta(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


# Where `alerts-now` writes. config.toml `alerts_now` points it at the file Jarvis's readers already
# know (~/.jarvis/ALERTS-NOW.md); without a config it is Heimdall's own state (tests).
ALERTS_NOW = Path(str(load_config().get("alerts_now") or STATE / "ALERTS-NOW.md")).expanduser()
