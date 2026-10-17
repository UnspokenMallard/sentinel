#!/Users/anzes/.jarvis/.venv/bin/python
"""Disk space and cache growth watch (detection only).

Records the free space on / and the size of the directories that grow without
bound on this Mac (~/Library/Caches, ~/.jarvis, the mac-backup log dir), appends
one line per run to state/disk-history.jsonl, and reports the growth rate over
the last week. Fails when free space is below both FREE_FAIL_GB and FREE_FAIL_PCT,
or when the cache directory exceeds CACHE_FAIL_GB. Never deletes anything: cache
cleanup is a decision for Anze.
Usage: check-disk.py
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Sentinel owns a check's artifact and history (2026-10-17)
HISTORY = CHECKS / "disk-history.jsonl"
ARTIFACT = CHECKS / "disk.json"
GROWN = {
    "caches": HOME / "Library" / "Caches",
    "jarvis": HOME / ".jarvis",
    "mac-backup-logs": HOME / "Library" / "Logs" / "mac-backup",
}
FREE_FAIL_GB = 15.0
FREE_FAIL_PCT = 8.0
CACHE_FAIL_GB = 40.0
GROWTH_WARN_GB_PER_WEEK = 10.0


def free_space() -> tuple[float, float]:
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return free / 1e9, 100.0 * free / total


def dir_gb(path: Path) -> float | None:
    if not path.exists():
        return None
    r = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, timeout=240)
    if r.returncode not in (0, 1) or not r.stdout.strip():  # du exits 1 on unreadable subdirs; size still printed
        return None
    return int(r.stdout.split()[0]) / 1e6


def read_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    rows = []
    for line in HISTORY.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def main() -> int:
    now = datetime.now().astimezone()
    free_gb, free_pct = free_space()
    sizes = {name: dir_gb(path) for name, path in GROWN.items()}
    row = {"at": now.isoformat(), "free_gb": round(free_gb, 2), "free_pct": round(free_pct, 1),
           **{k: (round(v, 3) if v is not None else None) for k, v in sizes.items()}}
    history = read_history()
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a") as fh:
        fh.write(json.dumps(row) + "\n")

    # growth over the last 7 days of recorded runs, for the directories that have a size
    growth = {}
    week_ago = now - timedelta(days=7)
    past = [h for h in history if datetime.fromisoformat(h["at"]) <= week_ago]
    if past:
        base = past[-1]
        for name in GROWN:
            if base.get(name) is not None and sizes.get(name) is not None:
                growth[name] = round(sizes[name] - base[name], 3)

    problems = []
    if free_gb < FREE_FAIL_GB and free_pct < FREE_FAIL_PCT:
        problems.append(f"free space {free_gb:.1f} GB ({free_pct:.1f}%) on /")
    if sizes.get("caches") is not None and sizes["caches"] > CACHE_FAIL_GB:
        problems.append(f"~/Library/Caches is {sizes['caches']:.1f} GB (limit {CACHE_FAIL_GB:g})")
    for name, delta in growth.items():
        if delta > GROWTH_WARN_GB_PER_WEEK:
            problems.append(f"{name} grew {delta:.1f} GB in a week")

    ARTIFACT.write_text(json.dumps({"checked_at": now.isoformat(), "row": row, "growth_7d_gb": growth,
                                    "problems": problems}, indent=2))
    summary = f"free {free_gb:.0f} GB ({free_pct:.0f}%); caches {sizes['caches'] or 0:.1f} GB"
    if problems:
        print("disk: " + "; ".join(problems) + f". ({summary})", file=sys.stderr)
        return 1
    print(f"disk OK - {summary}; {len(history) + 1} sample(s) recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
