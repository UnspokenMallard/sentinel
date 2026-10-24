#!/Users/anzes/.jarvis/.venv/bin/python
"""Stalled Claude sessions and orphaned session scratch processes (detection only).

A session that hangs (a background subagent waiting on a permission gate, a child
process that never exits) keeps its process alive with no CPU use and no output.
This job samples CPU time twice, SAMPLE_S apart, for every process that looks like
a Claude Code session, and reports those that are old and idle.

Two classes:
  - headless: `claude -p` jobs and processes under /private/tmp/claude-501/ (session
    scratch). These are ours. Idle past IDLE_FAIL_H is a failure (exit 1), so the
    job alerts through the normal handler-failed path.
  - interactive: a terminal session left open. Listed, never a failure: idle is normal.

The Claude Desktop app, its helpers and the Chrome native-messaging bridge are not
sessions and are excluded. Never signals or kills anything.
Usage: check-stalled.py
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

FINDING_EXIT = 87  # a check ran and found a problem; jarvis daemon/core.py FINDING_EXIT

SAMPLE_S = 20
IDLE_FAIL_H = 4.0
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Heimdall owns a check's artifact and history (2026-10-17)
ARTIFACT = CHECKS / "stalled.json"
SESSION_RE = re.compile(r"(/\.local/bin/claude\b|\bclaude -p\b|/private/tmp/claude-501/)")
EXCLUDE_RE = re.compile(r"Claude\.app|chrome-native-host|shell-snapshots|^tail -f /private/tmp/claude-501/")  # orphan tail of a task output, not a session (2026-10-20)


def parse_etime(text: str) -> float:
    """ps etime: [[dd-]hh:]mm:ss -> seconds."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    parts = [int(p) for p in text.split(":")]
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return days * 86400 + secs


def parse_cpu(text: str) -> float:
    """ps time (cumulative CPU): [[h:]m:]s.frac, minutes may exceed 59 -> seconds."""
    secs = 0.0
    for p in text.split(":"):
        secs = secs * 60 + float(p)
    return secs


def snapshot() -> dict[int, dict]:
    out = subprocess.run(["ps", "-axo", "pid=,etime=,time=,command="], capture_output=True, text=True).stdout
    procs: dict[int, dict] = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, etime, cpu, cmd = parts
        if not SESSION_RE.search(cmd) or EXCLUDE_RE.search(cmd) or int(pid) == os.getpid():
            continue
        procs[int(pid)] = {"pid": int(pid), "elapsed_h": parse_etime(etime) / 3600,
                           "cpu_s": parse_cpu(cpu), "command": cmd[:160],
                           "headless": bool(re.search(r"\bclaude -p\b|/private/tmp/claude-501/", cmd))}
    return procs


def main() -> int:
    first = snapshot()
    time.sleep(SAMPLE_S)
    second = snapshot()

    rows = []
    for pid, p in second.items():
        before = first.get(pid)
        if before is None:
            continue  # started during the sample: not idle evidence
        busy_s = p["cpu_s"] - before["cpu_s"]
        rows.append({**p, "idle": busy_s < 1.0, "idle_h": p["elapsed_h"] if busy_s < 1.0 else 0.0})

    stalled = [r for r in rows if r["idle"] and r["elapsed_h"] >= IDLE_FAIL_H]
    failing = [r for r in stalled if r["headless"]]
    interactive = [r for r in stalled if not r["headless"]]

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps({"checked_at": datetime.now().astimezone().isoformat(),
                                    "sessions": len(rows), "stalled": stalled}, indent=2))
    msg = f"{len(rows)} session process(es); {len(stalled)} idle >= {IDLE_FAIL_H:g}h"
    if failing:
        detail = "; ".join(f"pid {r['pid']} idle {r['elapsed_h']:.1f}h {r['command'][:60]}" for r in failing[:5])
        print(f"STALLED headless session(s): {detail}. {msg}", file=sys.stderr)
        return FINDING_EXIT
    if interactive:
        msg += f"; interactive left open: {len(interactive)} (not a failure)"
    print(f"sessions OK - {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
