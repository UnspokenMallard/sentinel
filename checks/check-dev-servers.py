#!/Users/anzes/.jarvis/.venv/bin/python
"""Which local dev servers are running, from where, and whether they can be trusted (detection only).

Answers the questions that cost the most debugging time in past sessions:
  - which repo / worktree / branch is the server on this port running from?
    (2026-09-19: a worktree's dev server served the wrong build and API)
  - is it older than the code it serves? (2026-09-05: a five-day-old ts-node server
    held pre-change source; the frontend was debugged for three symptoms it did not cause)
  - is the port held twice? (2026-10-08: an SSH tunnel on [::1] and a test server on
    127.0.0.1 shared a port, so "localhost" reached the remote service)

For every listening TCP socket it records process, port, address, working directory,
start time, and for a process inside a git repository under ~/code: the repo, branch
and whether source changed after the process started (watch-mode servers reload and
are not judged). Fails (exit 1) only on a port bound by two different processes.
Stale servers are listed as warnings. Never stops or signals anything.
Run it by hand before trusting test results: ~/.jarvis/scripts/check-dev-servers.py
Output: reports/dev-servers/latest.md, state/dev-servers.json.
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
from datetime import datetime
from pathlib import Path

FINDING_EXIT = 87  # a check ran and found a problem; jarvis daemon/core.py FINDING_EXIT

HOME = Path.home()
JARVIS = HOME / ".jarvis"
CODE = HOME / "code"
REPORT = JARVIS / "reports" / "dev-servers" / "latest.md"
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Sentinel owns a check's artifact and history (2026-10-17)
ARTIFACT = CHECKS / "dev-servers.json"
DEV_CMDS = re.compile(r"^(node|python[\d.]*|ruby|java|deno|bun|php|ssh|ng|vite|tsx|ts-node)$", re.I)
WATCH_RE = re.compile(r"\bwatch\b|--watch|nodemon|ng serve|\bvite\b|ts-node-dev|tsx watch|webpack serve|"
                      r"--reload|runserver", re.I)
MAX_CHANGED_LISTED = 5
# Agent tooling that runs with a repo as cwd but serves no app (Serena's MCP server and
# dashboard listen on 2428x per project): listed under "other", never judged stale.
TOOL_RE = re.compile(r"serena|mcp-server|/\.cache/uv/|/\.claude-(personal|work)/|claude-mods", re.I)


def sh(*args: str, cwd: str | None = None, timeout: int = 30) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout if r.returncode == 0 else ""


def listeners() -> list[dict]:
    """(pid, command, address, port) for every listening TCP socket, via lsof -F."""
    out = sh("lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "pcn")
    rows, pid, cmd = [], None, ""
    for line in out.splitlines():
        tag, val = line[:1], line[1:]
        if tag == "p":
            pid = int(val)
        elif tag == "c":
            cmd = val
        elif tag == "n" and pid is not None:
            addr, _, port = val.rpartition(":")
            if port.isdigit():
                rows.append({"pid": pid, "command": cmd, "address": addr, "port": int(port)})
    return rows


def conflicts(rows: list[dict]) -> list[str]:
    """Ports bound by more than one process. Pure, for the selftest."""
    by_port: dict[int, set[tuple[int, str]]] = {}
    for r in rows:
        by_port.setdefault(r["port"], set()).add((r["pid"], r["command"]))
    return [f"port {port}: " + ", ".join(f"{c} (pid {p})" for p, c in sorted(procs))
            for port, procs in sorted(by_port.items()) if len(procs) > 1]


def proc_info(pid: int) -> dict:
    cwd = ""
    for line in sh("lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn").splitlines():
        if line.startswith("n"):
            cwd = line[1:]
    started = sh("ps", "-o", "lstart=", "-p", str(pid)).strip()
    args = sh("ps", "-o", "command=", "-p", str(pid)).strip()
    start_ts = None
    if started:
        try:
            start_ts = datetime.strptime(started, "%a %b %d %H:%M:%S %Y").timestamp()
        except ValueError:
            pass
    return {"cwd": cwd, "start_ts": start_ts, "args": args[:200]}


def repo_state(cwd: str, start_ts: float | None) -> dict:
    """Repo, branch and source changed since the process started. Empty outside ~/code repos."""
    if not cwd or not cwd.startswith(str(CODE)):
        return {}
    top = sh("git", "-C", cwd, "rev-parse", "--show-toplevel").strip()
    if not top:
        return {}
    branch = sh("git", "-C", top, "rev-parse", "--abbrev-ref", "HEAD").strip()
    changed: list[str] = []
    if start_ts:
        last_commit = sh("git", "-C", top, "log", "-1", "--format=%ct").strip()
        if last_commit.isdigit() and int(last_commit) > start_ts:
            changed.append(f"commit {sh('git', '-C', top, 'log', '-1', '--format=%h %s').strip()[:60]}")
        for line in sh("git", "-C", top, "status", "--porcelain").splitlines():
            path = Path(top) / line[3:].strip().strip('"')
            try:
                if path.is_file() and path.stat().st_mtime > start_ts:
                    changed.append(line[3:].strip())
            except OSError:
                continue
    return {"repo": Path(top).name, "toplevel": top, "branch": branch, "changed_since_start": changed}


def main() -> int:
    rows = listeners()
    clash = conflicts(rows)
    servers: list[dict] = []
    for pid in sorted({r["pid"] for r in rows if DEV_CMDS.match(r["command"])}):
        info = proc_info(pid)
        ports = sorted({f"{r['address']}:{r['port']}" for r in rows if r["pid"] == pid})
        cmd = next(r["command"] for r in rows if r["pid"] == pid)
        state = {} if TOOL_RE.search(info["args"]) else repo_state(info["cwd"], info["start_ts"])
        watch = bool(WATCH_RE.search(info["args"]))
        stale = bool(state.get("changed_since_start")) and not watch
        servers.append({"pid": pid, "command": cmd, "ports": ports, **info, **state, "watch_mode": watch,
                        "stale": stale})

    now = datetime.now()
    def age(ts: float | None) -> str:
        if not ts:
            return "?"
        h = (now.timestamp() - ts) / 3600
        return f"{h:.0f}h" if h < 48 else f"{h / 24:.0f}d"

    lines = [f"# Dev servers - {now:%Y-%m-%d %H:%M}", "",
             "Run `~/.jarvis/scripts/check-dev-servers.py` before trusting test results against a local server.", ""]
    if clash:
        lines += ["## PORT CONFLICTS (two processes on one port: 'localhost' may reach either)", ""]
        lines += [f"- {c}" for c in clash] + [""]
    in_repo = [s for s in servers if s.get("repo")]
    other = [s for s in servers if not s.get("repo")]
    lines += ["## In a repository under ~/code", ""]
    for s in in_repo:
        flag = " **STALE: source changed after start**" if s["stale"] else (" (watch mode)" if s["watch_mode"] else "")
        lines.append(f"- {', '.join(s['ports'])} - {s['repo']} @ {s['branch']} - {s['command']} pid {s['pid']}, "
                     f"up {age(s['start_ts'])}{flag}")
        lines.append(f"  - cwd {s['cwd']}")
        if s["stale"]:
            lines += [f"  - changed: {c}" for c in s["changed_since_start"][:MAX_CHANGED_LISTED]]
    if not in_repo:
        lines.append("None.")
    lines += ["", "## Other listeners (tunnels, tools, Jarvis)", ""]
    lines += [f"- {', '.join(s['ports'])} - {s['command']} pid {s['pid']}, up {age(s['start_ts'])}, "
              f"cwd {s['cwd'] or '?'}" for s in other] or ["None."]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps({"checked_at": now.astimezone().isoformat(), "conflicts": clash,
                                    "servers": servers}, indent=2))
    stale = [s for s in servers if s["stale"]]
    summary = f"{len(in_repo)} repo server(s), {len(stale)} stale, {len(other)} other listener(s)"
    if "--print" in sys.argv or os.isatty(1):
        print("\n".join(lines))
    if clash:
        print(f"dev servers: PORT CONFLICT {'; '.join(clash)}. {summary}", file=sys.stderr)
        return FINDING_EXIT
    print(f"dev servers OK - {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
