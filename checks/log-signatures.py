#!/Users/anzes/.jarvis/.venv/bin/python
"""Group error and warning lines in the Jarvis logs by signature (report only).

Reads every log under ~/.jarvis/logs and ~/.analyst/state/logs modified in the last WINDOW_DAYS, keeps the
lines that say ERROR, WARN, FAILED, Traceback or a nonzero exit, and collapses
each to a signature: timestamps, numbers, hex ids, quoted strings and paths are
replaced with placeholders, so one failure that repeats with different values
counts once. Writes reports/log-signatures/latest.md (top TOP_N, most frequent
first) and state/log-signatures.json. Read-only.
Usage: log-signatures.py
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

JARVIS = Path.home() / ".jarvis"
LOGS = JARVIS / "logs"
ODIN_LOGS = Path.home() / ".analyst" / "state" / "logs"  # the report jobs Jarvis triggers in Odin (2026-10-18)
REPORT = JARVIS / "reports" / "log-signatures" / "latest.md"
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Heimdall owns a check's artifact and history (2026-10-17)
ARTIFACT = CHECKS / "log-signatures.json"
WINDOW_DAYS = 7
TOP_N = 15
# Build output, not Jarvis failures: the dependency rehearsal logs every webpack/Angular
# warning of each trial build. Counted in its own short section so it cannot crowd the top.
BUILD_LOGS = {"dep-rehearsal.log"}
BUILD_TOP_N = 3
INTEREST = re.compile(r"ERROR|WARN|FAILED|Traceback|exit=[1-9]")
NORMALIZE = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?"), "<ts>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"'[^']{1,200}'|\"[^\"]{1,200}\""), "<str>"),
    (re.compile(r"/[\w.\-/~]+"), "<path>"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def signature(line: str) -> str:
    s = line.strip()
    for pattern, repl in NORMALIZE:
        s = pattern.sub(repl, s)
    return s[:200]


# Within the first 30 characters, so "=== <ts> ..." banner lines are dated too.
LEADING_TS = re.compile(r"^.{0,30}?(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")
# "0 FAIL, 0 WARN" is a clean summary, not a warning: zero counts are removed before matching.
ZERO_COUNT = re.compile(r"\b0 (ERROR|WARN|FAILED|FAIL)S?\b")


def in_window(line: str, cutoff: float) -> bool:
    """Lines that carry their own timestamp are judged by it: alerts.log is appended to, so a
    recently modified file still holds months-old lines. Lines without one take the file's mtime."""
    m = LEADING_TS.match(line)
    if not m:
        return True
    try:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}").timestamp() >= cutoff
    except ValueError:
        return True


def main() -> int:
    cutoff = time.time() - WINDOW_DAYS * 86400
    counts: Counter[str] = Counter()
    build: Counter[str] = Counter()
    examples: dict[str, str] = {}
    sources: dict[str, set[str]] = defaultdict(set)
    files = [p for d in (LOGS, ODIN_LOGS) for p in d.glob("*.log*") if p.is_file() and p.stat().st_mtime >= cutoff]
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if not INTEREST.search(ZERO_COUNT.sub("", line)) or not in_window(line, cutoff):
                continue
            sig = signature(line)
            log_name = path.name.split(".log")[0] + ".log"
            if log_name in BUILD_LOGS:
                build[sig] += 1
                examples.setdefault(sig, line.strip()[:240])
                continue
            counts[sig] += 1
            examples.setdefault(sig, line.strip()[:240])
            sources[sig].add(path.name.split(".log")[0] + ".log")

    top = counts.most_common(TOP_N)
    lines = [f"# Log signatures - {datetime.now():%Y-%m-%d %H:%M}", "",
             f"{sum(counts.values())} error/warning line(s) in {len(files)} log file(s) modified in the last "
             f"{WINDOW_DAYS} days; {len(counts)} distinct signature(s). Top {len(top)} by count:", ""]
    for sig, n in top:
        lines.append(f"- **{n}x** {', '.join(sorted(sources[sig]))}: `{examples[sig][:160]}`")
    if not top:
        lines.append("No error or warning lines in the window.")
    if build:
        lines += ["", f"## Build output ({', '.join(sorted(BUILD_LOGS))})", "",
                  f"{sum(build.values())} warning line(s), {len(build)} signature(s); not Jarvis failures. Top {BUILD_TOP_N}:", ""]
        lines += [f"- {n}x `{examples[sig][:160]}`" for sig, n in build.most_common(BUILD_TOP_N)]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    ARTIFACT.write_text(json.dumps({"checked_at": datetime.now().astimezone().isoformat(),
                                    "distinct": len(counts), "total": sum(counts.values()),
                                    "build_lines": sum(build.values()),
                                    "top": [{"count": n, "signature": s} for s, n in top]}, indent=2))
    print(f"{sum(counts.values())} error/warning line(s), {len(counts)} signature(s); report {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
