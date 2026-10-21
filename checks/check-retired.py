#!/Users/anzes/.jarvis/.venv/bin/python
"""Retired tools that are still installed or running (detection only).

A tool removed from one place keeps running from another: agentmemory was
"uninstalled" 2026-10-01 and kept running from a Cursor plugin (Session Lesson
2026-10-03), and a fix that lived only in a note never reached its script
(2026-08-12). Each [[retired]] entry in config.toml names a tool and where it
must no longer appear:
  process  - regex over running process command lines
  paths    - files or directories that must not exist (globs allowed, ~ expanded)
  configs  - {file = regex} pairs: the regex must not match the file's text
  launchd  - regex over ~/Library/LaunchAgents plist file text
Exit 1 with every trace found. Never stops, uninstalls or deletes anything: removal
is Anze's decision, and each trace names the place to remove it from.
Output: reports/retired-tools/latest.md, state/retired-tools.json.
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import glob
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

FINDING_EXIT = 87  # a check ran and found a problem; jarvis daemon/core.py FINDING_EXIT

HOME = Path.home()
JARVIS = HOME / ".jarvis"
REPORT = JARVIS / "reports" / "retired-tools" / "latest.md"
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Sentinel owns a check's artifact and history (2026-10-17)
ARTIFACT = CHECKS / "retired.json"
LAUNCH_AGENTS = HOME / "Library" / "LaunchAgents"


def processes() -> list[tuple[int, str]]:
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    me = os.getpid()
    rows = []
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != me and "check-retired" not in cmd:
            rows.append((int(pid), cmd.strip()))
    return rows


def traces(entry: dict, procs: list[tuple[int, str]]) -> list[str]:
    """Every place a retired tool still shows up. Takes the process list so the selftest can drive it."""
    found: list[str] = []
    if entry.get("process"):
        rx = re.compile(entry["process"], re.I)
        found += [f"running: pid {pid} {cmd[:100]}" for pid, cmd in procs if rx.search(cmd)]
    for pattern in entry.get("paths", []):
        found += [f"still on disk: {p}" for p in glob.glob(os.path.expanduser(pattern))]
    for path, regex in (entry.get("configs") or {}).items():
        file = Path(os.path.expanduser(path))
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(regex, text, re.I)
        if m:
            found.append(f"still in config: {file} ({m.group(0)[:60]!r})")
    if entry.get("launchd"):
        rx = re.compile(entry["launchd"], re.I)
        for plist in sorted(LAUNCH_AGENTS.glob("*.plist")):
            try:
                if rx.search(plist.read_text(encoding="utf-8", errors="replace")):
                    found.append(f"launchd agent: {plist.name}")
            except OSError:
                continue
    return found


def main() -> int:
    cfg = tomllib.loads((JARVIS / "config.toml").read_text(encoding="utf-8"))
    entries = cfg.get("retired") or []
    procs = processes()
    results = []
    for entry in entries:
        hits = traces(entry, procs)
        results.append({"name": entry.get("name"), "retired": entry.get("retired"), "why": entry.get("why", ""),
                        "traces": hits})
    flagged = [r for r in results if r["traces"]]
    now = datetime.now()
    lines = [f"# Retired tools - {now:%Y-%m-%d %H:%M}", "",
             f"{len(entries)} retired tool(s) checked; {len(flagged)} still present somewhere. "
             "Nothing was stopped or removed.", ""]
    for r in flagged:
        lines.append(f"## {r['name']} (retired {r['retired']})")
        if r["why"]:
            lines.append(f"_{r['why']}_")
        lines += [f"- {t}" for t in r["traces"]] + [""]
    if not flagged:
        lines.append("None found.")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    ARTIFACT.write_text(json.dumps({"checked_at": now.astimezone().isoformat(), "results": results}, indent=2))
    if flagged:
        print("retired tools still present: " + "; ".join(f"{r['name']}: {r['traces'][0][:90]}" for r in flagged),
              file=sys.stderr)
        return FINDING_EXIT
    print(f"retired tools OK - {len(entries)} checked, none present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
