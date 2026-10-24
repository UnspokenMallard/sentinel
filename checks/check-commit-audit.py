#!/Users/anzes/.jarvis/.venv/bin/python
"""Nightly audit of the last day's commits in the local repositories (report only).

Catches the commit accidents recorded in Session Lessons:
  - line-ending blowup: a file whose diff is far larger than the same diff with
    --ignore-cr-at-eol (2026-10-07: a 12-line change committed as 2,644 lines)
  - unmentioned deletions: a commit deletes files and its message says nothing about
    removing anything (2026-10-01, 2026-08-24: staged `git rm`s swept into other commits)
  - attribution trailers: Co-authored-by / Claude-Session / "Generated with" in the
    message (2026-09-09: er-web history had to be rewritten to strip them)
  - agent author: a commit authored by an agent identity (AGENT_EMAILS) instead of the
    repo's user.email. Coworkers' authorship on commits applied here is kept, not flagged.
Scope: commits on any local branch with committer date in the last WINDOW_H hours,
whose author or committer is this machine's identity (the repo's user.email) or an
agent identity (AGENT_EMAILS). Coworkers' fetched commits are out of scope.
Read-only: runs git queries, never amends, rewrites or pushes.
Output: ~/.jarvis/reports/commit-audit/latest.md, state/checks/commit-audit.json (Heimdall). A finding is the check
working, so it exits 0 and puts `alerts` (commit-audit:flagged, warn) in the artifact for the Jarvis job to deliver;
exit 1 would make the daemon file it as handler-failed. Moved from Jarvis 2026-10-18.
Usage: check-commit-audit.py [--hours N]
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
import tomllib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".jarvis" / "scripts"))  # notmine.py: the "not mine" list is Jarvis's
import notmine  # noqa: E402

HOME = Path.home()
JARVIS = HOME / ".jarvis"
ROOTS = [HOME / "code" / "eventregistry", HOME / "code" / "personal"]
EXTRA = [JARVIS]
REPORT = JARVIS / "reports" / "commit-audit" / "latest.md"
ARTIFACT = Path.home() / ".sentinel" / "state" / "checks" / "commit-audit.json"
CONFIG = Path.home() / ".sentinel" / "config.toml"
ACKS = Path.home() / ".sentinel" / "state" / "commit-audit-acks.json"  # written by the HUD's Acknowledge button (audit A27)
WINDOW_H = 26
AGENT_EMAILS = {"noreply@anthropic.com"}
CRLF_RATIO = 5          # diff this many times larger than with --ignore-cr-at-eol ...
CRLF_MIN_LINES = 100    # ... and at least this many lines larger
TRAILER_RE = re.compile(r"^(Co-authored-by|Claude-Session|Generated with)\b", re.I | re.M)
REMOVAL_WORDS = re.compile(r"delet|remov|drop|retir|clean|prune|rm\b|move|renam|replac|split|merg|squash|uninstall"
                           r"|instead of|migrat|consolidat|ignor|untrack|stop tracking",
                           re.I)
# A commit that says it is about line endings is the fix for churn, not churn.
EOL_WORDS = re.compile(r"line[- ]endings?|\bCRLF\b|\bEOL\b", re.I)


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
    return r.stdout if r.returncode == 0 else ""


def repos() -> list[Path]:
    found: list[Path] = []
    for root in ROOTS:
        if root.is_dir():
            found += [p for p in sorted(root.iterdir()) if (p / ".git").exists()]
    return [p for p in found + [p for p in EXTRA if (p / ".git").exists()] if notmine.is_mine(p)]


def acknowledged() -> dict[str, str]:
    """[commit_audit.acknowledged] in ~/.sentinel/config.toml plus ACKS (the HUD's Acknowledge button):
    sha prefix -> why it needs no action (fixed by a later commit, or already pushed and not worth a force-push)."""
    try:
        cfg = (tomllib.loads(CONFIG.read_text(encoding="utf-8")).get("commit_audit") or {}).get("acknowledged") or {}
    except (OSError, tomllib.TOMLDecodeError):
        cfg = {}
    try:
        hud = {k: v.get("reason", "") if isinstance(v, dict) else str(v) for k, v in json.loads(ACKS.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError, AttributeError):
        hud = {}
    return {k.lower(): v for k, v in {**hud, **cfg}.items()}


def is_acknowledged(sha: str, acks: dict[str, str]) -> bool:
    """Pure, for the selftest. A prefix of at least 7 characters matches."""
    return any(len(k) >= 7 and sha.lower().startswith(k) for k in acks)


def numstat(repo: Path, sha: str, *extra: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in git(repo, "show", "--numstat", "--format=", *extra, sha).splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            out[parts[2]] = int(parts[0]) + int(parts[1])
    return out


def crlf_blowups(full: dict[str, int], ignoring_cr: dict[str, int]) -> list[str]:
    """Files whose diff is mostly line-ending churn. Pure, for the selftest."""
    hits = []
    for path, n in full.items():
        real = ignoring_cr.get(path, 0)
        if n - real >= CRLF_MIN_LINES and n >= CRLF_RATIO * max(real, 1):
            hits.append(f"{path}: {n} lines, {real} without CR changes")
    return hits


def unmentioned_deletions(message: str, deleted: list[str]) -> list[str]:
    """Deleted files the message gives no hint of. Pure, for the selftest."""
    if not deleted or REMOVAL_WORDS.search(message):
        return []
    return [d for d in deleted if Path(d).stem.lower() not in message.lower()]


def audit_commit(repo: Path, sha: str, author: str, me: str, message: str) -> list[str]:
    flags = []
    blow = [] if EOL_WORDS.search(message) else crlf_blowups(numstat(repo, sha),
                                                             numstat(repo, sha, "--ignore-cr-at-eol"))
    if blow:
        flags.append("line-ending churn: " + "; ".join(blow[:3]))
    deleted = [p for p in git(repo, "show", "--diff-filter=D", "--name-only", "--format=", sha).splitlines() if p]
    silent = unmentioned_deletions(message, deleted)
    if silent:
        flags.append(f"deletes {len(silent)} file(s) the message does not mention: " + ", ".join(silent[:5]))
    if TRAILER_RE.search(message):
        flags.append("attribution trailer in message: " + TRAILER_RE.search(message).group(0))
    if author.lower() in AGENT_EMAILS:
        flags.append(f"authored by an agent identity ({author}), not by {me or 'the repo user'}")
    return flags


def main() -> int:
    hours = int(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else WINDOW_H
    findings: list[dict] = []
    checked = skipped = 0
    acks = acknowledged()
    seen: set[str] = set()  # across repos: a worktree (er-web-bg) shares its commits with er-web
    for repo in repos():
        me = git(repo, "config", "user.email").strip()
        log = git(repo, "log", "--all", "--no-merges", f"--since={hours} hours ago",
                  "--format=%H%x1f%ae%x1f%ce%x1f%s%x1f%B%x1e")
        for record in log.split("\x1e"):
            fields = record.strip("\n").split("\x1f")
            if len(fields) < 5 or fields[0] in seen:
                continue
            sha, author, committer, subject, body = fields[0], fields[1], fields[2], fields[3], fields[4]
            seen.add(sha)
            ours = {author.lower(), committer.lower()} & ({me.lower()} | AGENT_EMAILS)
            if not ours:
                continue
            if is_acknowledged(sha, acks):
                skipped += 1
                continue
            checked += 1
            flags = audit_commit(repo, sha, author, me, body)
            if flags:
                findings.append({"repo": repo.name, "sha": sha[:9], "subject": subject[:90], "flags": flags})

    now = datetime.now()
    lines = [f"# Commit audit - {now:%Y-%m-%d %H:%M}", "",
             f"{checked} commit(s) from the last {hours}h checked across {len(repos())} repos; "
             f"{len(findings)} flagged, {skipped} acknowledged in config.toml. Read-only: nothing was changed.", ""]
    for f in findings:
        lines.append(f"## {f['repo']} {f['sha']} - {f['subject']}")
        lines += [f"- {flag}" for flag in f["flags"]] + [""]
    if not findings:
        lines.append("Nothing flagged.")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    doc = {"checked_at": now.astimezone().isoformat(), "commits": checked, "flagged": findings,
           "failing": [f"{f['repo']} {f['sha']}" for f in findings], "alerts": []}
    if findings:
        summary = (f"commit audit: {len(findings)} flagged commit(s): "
                   + "; ".join(f"{f['repo']} {f['sha']}: {f['flags'][0][:80]}" for f in findings[:4]))
        print(summary)
        # A finding is the check working, not failing: exit 0 so the daemon does not file it as
        # handler-failed (incident #36, 2026-10-14). The job delivers `alerts` (deliver_alerts).
        doc["alerts"] = [{"key": "commit-audit:flagged", "severity": "warn", "cooldown_s": 20 * 3600,
                          "message": summary + " - fix, or acknowledge in ~/.sentinel/config.toml [commit_audit.acknowledged]"}]
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(doc, indent=2))
    if findings:
        return 0
    print(f"commit audit OK - {checked} commit(s) checked, nothing flagged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
