#!/Users/anzes/.jarvis/.venv/bin/python
"""Sustained host CPU saturation check (macOS port of check-host-cpu-load.ps1).

Detection only: samples host-wide CPU three times over ~4s via `top` and flags
only sustained saturation, naming the current top process so a "tool is broken"
report gets checked against the host first. Never kills anything.
Usage: check-host-cpu-load.py [--max-avg-percent 85]
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
from datetime import datetime
from pathlib import Path

CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Sentinel owns a check's artifact and history (2026-10-17)
STATUS = CHECKS
ARTIFACT = STATUS / "host-cpu-load.json"


def main() -> int:
    limit = float(sys.argv[sys.argv.index("--max-avg-percent") + 1]) if "--max-avg-percent" in sys.argv else 85.0
    # top -l 4: the first sample is since boot, so average samples 2-4 (1s apart after -s 1... default 1s)
    out = subprocess.run(["top", "-l", "4", "-s", "1", "-n", "1", "-o", "cpu", "-stats", "pid,command,cpu"],
                         capture_output=True, text=True).stdout
    idle = [float(m) for m in re.findall(r"CPU usage: .*?([\d.]+)% idle", out)][1:]
    avg = round(100 - sum(idle) / len(idle), 1) if idle else None
    procs = re.findall(r"^\s*(\d+)\s+(.+?)\s+([\d.]+)\s*$", out.split("PID")[-1], re.M)
    top_pid, top_name, top_cpu = (procs[0] if procs else ("", "", "0"))
    STATUS.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps({"checked_at": datetime.now().astimezone().isoformat(), "avg_cpu_pct": avg,
                                    "top_process": top_name.strip(), "top_pid": top_pid, "top_cpu_pct": float(top_cpu)},
                                   indent=2))
    if avg is None:
        print("could not read CPU usage from top", file=sys.stderr)
        return 1
    if avg > limit:
        print(f"Host CPU averaged {avg:.0f}% over ~3s (limit {limit:.0f}%) - sustained saturation. Top CPU consumer "
              f"right now: {top_name.strip()} (PID {top_pid}, {top_cpu}%). Check this before assuming any other tool "
              "is broken.", file=sys.stderr)
        return 1
    print(f"host CPU avg {avg:.1f}% - within budget (top: {top_name.strip()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
