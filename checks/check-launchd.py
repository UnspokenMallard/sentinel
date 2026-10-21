#!/Users/anzes/.jarvis/.venv/bin/python
"""Launchd agents of Jarvis and its components (audit A03, 2026-10-21; SHADOW, detection only).

An agent that is not running and whose last exit was not 0 failed silently: launchd keeps the status and nobody
reads it. This check lists those. In shadow it writes `failing` to the artifact and delivers no alerts; after a
clean week, move the rows to `alerts` (key `launchd-exit:<label>`).
Usage: check-launchd.py
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ARTIFACT = Path.home() / ".sentinel" / "state" / "checks" / "launchd.json"
LABEL_RE = re.compile(r"^com\.(jarvis|mind|budget|sentinel|herald|forge|gateway|analyst)\.")


def parse(listing: str) -> list[dict]:
    """Rows of `launchctl list` for our agents. A running agent's status is its previous exit, so only idle ones fail."""
    rows = []
    for line in listing.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) != 3 or not LABEL_RE.match(parts[2]):
            continue
        pid, status, label = parts
        running = pid != "-"
        rows.append({"label": label, "running": running, "last_exit": int(status),
                     "failing": not running and int(status) != 0})
    return rows


# shortcut: last exit only; log age against each agent's StartInterval is not checked yet, add it when shadow is clean.
def main(listing: str | None = None, artifact: Path = ARTIFACT) -> int:
    if listing is None:
        listing = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=30, check=True).stdout
    rows = parse(listing)
    failing = [f"{r['label']} last exited {r['last_exit']}" for r in rows if r["failing"]]
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(), "shadow": True,
                                    "rows": rows, "failing": failing, "alerts": []}, indent=2))
    print("launchd (shadow): " + ("; ".join(failing) if failing else f"OK - {len(rows)} agents"))
    return 0  # shadow: a finding is recorded, never raised


if __name__ == "__main__":
    sys.exit(main())
