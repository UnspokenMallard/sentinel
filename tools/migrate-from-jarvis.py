#!/Users/anzes/.jarvis/.venv/bin/python
"""One-off cutover (2026-10-17): copy Jarvis's `alerts` and `incidents` rows into Sentinel's store.

Copies, never drops: the old tables stay in jarvis.db until Anze confirms. Backs up jarvis.db first and
refuses to overwrite an existing backup. Incident ids are kept. Delivery state (alerts.last_written)
goes to jarvis.db `alert_delivery`. Idempotent: a row already in Sentinel with a newer last_seen wins.
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".sentinel"))
from sentinel import store  # noqa: E402

JARVIS_DB = Path.home() / ".jarvis" / "jarvis.db"
BACKUP = Path.home() / ".jarvis" / "jarvis.db.bak-sentinel"

if not BACKUP.exists():
    subprocess.run(["sqlite3", str(JARVIS_DB), f".backup {BACKUP}"], check=True)
    print("backup:", BACKUP)
else:
    print("backup already exists, kept:", BACKUP)

src = sqlite3.connect(f"file:{JARVIS_DB}?mode=ro", uri=True)
alerts = src.execute("SELECT key, message, severity, first_seen, last_seen, count, last_written FROM alerts").fetchall()
incs = src.execute("SELECT id, kind, summary, severity, opened_at, last_seen_at, hits, members, member_count, "
                   "resolved_at, resolved_by, resolution FROM incidents ORDER BY id").fetchall()
src.close()

with store.connect() as conn:
    for key, msg, sev, first, last, count, _lw in alerts:
        conn.execute(
            "INSERT INTO alerts(key, message, severity, first_seen, last_seen, count) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET message=excluded.message, severity=excluded.severity, "
            "first_seen=MIN(first_seen, excluded.first_seen), last_seen=excluded.last_seen, count=excluded.count "
            "WHERE excluded.last_seen > alerts.last_seen", (key, msg, sev, first, last, count))
    for row in incs:
        conn.execute("INSERT OR IGNORE INTO incidents(id, kind, summary, severity, opened_at, last_seen_at, hits, "
                     "members, member_count, resolved_at, resolved_by, resolution) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", row)
    top = conn.execute("SELECT MAX(id) FROM incidents").fetchone()[0] or 0
    conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'incidents'", (top,))
    n_alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    n_incs = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]

dst = sqlite3.connect(JARVIS_DB, timeout=30)
dst.execute("CREATE TABLE IF NOT EXISTS alert_delivery (key TEXT PRIMARY KEY, last_written REAL NOT NULL DEFAULT 0)")
dst.executemany("INSERT OR IGNORE INTO alert_delivery(key, last_written) VALUES(?, ?)",
                [(a[0], a[6]) for a in alerts])
dst.commit()
n_del = dst.execute("SELECT COUNT(*) FROM alert_delivery").fetchone()[0]
dst.close()
print(f"jarvis.db had {len(alerts)} alerts, {len(incs)} incidents; sentinel.db now {n_alerts} alerts, {n_incs} incidents; "
      f"alert_delivery {n_del}")
