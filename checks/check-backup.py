#!/Users/anzes/.jarvis/.venv/bin/python
"""Freshness and health check for the Mac backup (detection only).

Reads the status file that ~/.local/bin/mac-backup writes after every run, plus
the restore-drill line in its log. The backup script itself already runs the
drills and checks; this job makes sure a failure cannot pass silently, because
nothing else looks at the status file. Fails when:
  - the status file is missing or unreadable
  - the last run is older than MAX_RUN_AGE_H (daily job, so 26h = one missed run)
  - the NUC repository did not produce a good snapshot
  - the offsite (Cloudflare R2) copy is not configured or failed
  - the last successful restore drill is older than MAX_DRILL_AGE_D (monthly drill)
Never writes to a repository and never runs restic itself.
Usage: check-backup.py
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import json
import re
import sys
from datetime import datetime
from pathlib import Path

FINDING_EXIT = 87  # a check ran and found a problem; jarvis daemon/core.py FINDING_EXIT

LOG_DIR = Path.home() / "Library" / "Logs" / "mac-backup"
STATUS_FILE = LOG_DIR / "status.json"
BACKUP_LOG = LOG_DIR / "backup.log"
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Sentinel owns a check's artifact and history (2026-10-17)
ARTIFACT = CHECKS / "backup.json"
MAX_RUN_AGE_H = 26
MAX_DRILL_AGE_D = 40
# Anze 2026-10-09: offsite (Cloudflare R2) copy postponed indefinitely. While True, a
# "not-configured" offsite state is reported as postponed, not as a problem. A
# configured offsite copy that fails is still a problem. Set False to make it required again.
OFFSITE_POSTPONED = True
DRILL_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) nuc: restore drill passed", re.M)


MAC_BACKUP = Path.home() / ".local" / "bin" / "mac-backup"
EXCLUDES = Path.home() / ".config" / "mac-backup" / "excludes.txt"
IMMICH_STATUS = Path.home() / ".jarvis" / "state" / "immich-backup.json"
MAX_IMMICH_AGE_H = 26
H = str(Path.home())
# Every data store known to matter (BACKUP-DRILL-DESIGN.md part C), and what covers it:
# "mac-backup" (path under SOURCES, not excluded), "db-copy" (a consistent SQLite copy in
# mac-backup's DRILL_DBS), "immich-backup" (its own job), or ("accepted", "<who, when, why>").
STORES: list[tuple[str, str, object]] = [
    ("Jarvis repo and state", f"{H}/.jarvis", "mac-backup"),
    ("Jarvis secrets", f"{H}/.jarvis/secrets", "mac-backup"),
    ("jarvis.db", "jarvis.db", "db-copy"),
    ("memory store (memory.db)", "memory.db", "db-copy"),
    ("yggdrasil (ygg.db)", "ygg.db", "db-copy"),
    *[(f"{n} home", f"{H}/.{n.lower()}", "mac-backup")
      for n in ("Mind", "Budget", "Sentinel", "Herald", "Forge", "Gateway", "Analyst")],
    *[(f"{n}.db", f"{n}.db", "db-copy") for n in ("budget", "sentinel", "herald", "forge", "gateway")],
    ("Claude configs", f"{H}/.claude-personal", "mac-backup"),
    ("Claude work config", f"{H}/.claude-work", "mac-backup"),
    ("repositories (unpushed work)", f"{H}/code", "mac-backup"),
    ("Notes vault", f"{H}/Documents/Notes", "mac-backup"),
    ("Archive", f"{H}/Archive", "mac-backup"),
    ("SSH keys", f"{H}/.ssh", "mac-backup"),
    ("Caddy config", f"{H}/.config/caddy", "mac-backup"),
    ("mac-backup config and excludes", f"{H}/.config/mac-backup", "mac-backup"),
    ("ntfy state", "/opt/homebrew/var/lib/ntfy", "mac-backup"),
    ("Immich photos (NUC)", "nuc:~/immich/library", "immich-backup"),
    ("local scripts (mac-backup itself, mem)", f"{H}/.local/bin", "mac-backup"),
]


def mac_backup_sources(script: str) -> tuple[list[str], list[str]]:
    """SOURCES and DRILL_DBS as mac-backup declares them ($HOME expanded, comments dropped)."""
    # Comments go first: one holds "(imported ... 2026-10-13)", whose ")" would end the array.
    bare = "\n".join(re.sub(r"(^|\s)#.*", "", line) for line in script.splitlines())

    def array(name: str) -> list[str]:
        m = re.search(rf"^{name}=\((.*?)\)", bare, re.M | re.S)
        if not m:
            return []
        return [w.strip('"').replace("$HOME", H) for w in re.findall(r'"[^"]*"|\S+', m.group(1))]
    return array("SOURCES"), array("DRILL_DBS")


def is_excluded(path: str, excludes: list[str]) -> bool:
    parts = path.split("/")
    for p in excludes:
        if p.startswith("/") and (path == p or path.startswith(p.rstrip("/") + "/")):
            return True
        if "/" not in p and p in parts:
            return True
    return False


def coverage(script: str, excludes: list[str], immich: dict | None, now: datetime) -> list[str]:
    sources, dbs = mac_backup_sources(script)
    problems = []
    for name, path, how in STORES:
        if isinstance(how, tuple):
            continue
        if how == "mac-backup":
            ok = any(path == s or path.startswith(s.rstrip("/") + "/") for s in sources) and not is_excluded(path, excludes)
        elif how == "db-copy":
            ok = path in dbs
        else:
            at = (immich or {}).get("checked_at")
            ok = bool(immich and immich.get("ok") and at
                      and (now - datetime.fromisoformat(at)).total_seconds() / 3600 <= MAX_IMMICH_AGE_H)
        if not ok:
            problems.append(f"not backed up: {name} ({path}, expected {how})")
    return problems


def last_drill(log_text: str) -> datetime | None:
    hits = DRILL_RE.findall(log_text)
    return datetime.fromisoformat(hits[-1]) if hits else None


def main() -> int:
    now = datetime.now().astimezone()
    problems: list[str] = []
    try:
        status = json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError) as exc:
        problems.append(f"status file unreadable ({STATUS_FILE.name}): {exc}")
        status = {}

    run_at = None
    if status.get("at"):
        run_at = datetime.fromisoformat(status["at"])
        age_h = (now - run_at).total_seconds() / 3600
        if age_h > MAX_RUN_AGE_H:
            problems.append(f"last backup run {age_h:.0f}h ago (limit {MAX_RUN_AGE_H}h)")
    elif status:
        problems.append("status file has no run time")

    nuc = status.get("nuc") or {}
    if status and not nuc.get("ok"):
        problems.append(f"NUC repository backup failed: {nuc.get('error') or 'no error recorded'}")

    offsite = status.get("offsite") or {}
    offsite_note = ""
    if offsite.get("state") == "not-configured" and OFFSITE_POSTPONED:
        offsite_note = "; offsite postponed (not configured)"
    elif offsite.get("state") != "ok":
        problems.append(
            f"offsite copy {offsite.get('state', 'missing')}"
            + (f": {offsite['error']}" if offsite.get("error") else "")
            + " - no second repository exists"
        )

    try:
        drill = last_drill(BACKUP_LOG.read_text(errors="replace"))
    except OSError as exc:
        drill = None
        problems.append(f"backup log unreadable: {exc}")
    drill_age_d = None
    if drill is None:
        problems.append("no successful restore drill in backup.log")
    else:
        drill_age_d = (now - drill.astimezone()).days
        if drill_age_d > MAX_DRILL_AGE_D:
            problems.append(f"last restore drill {drill_age_d}d ago (limit {MAX_DRILL_AGE_D}d)")

    try:
        excludes = [l.strip() for l in EXCLUDES.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
        try:
            immich = json.loads(IMMICH_STATUS.read_text())
        except (OSError, ValueError):
            immich = None
        problems += coverage(MAC_BACKUP.read_text(), excludes, immich, now)
    except OSError as exc:
        problems.append(f"backup coverage unreadable: {exc}")

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps({
        "checked_at": now.isoformat(),
        "last_run": run_at.isoformat() if run_at else None,
        "last_drill": drill.isoformat() if drill else None,
        "problems": problems,
    }, indent=2))

    summary = (f"last run {run_at:%F %H:%M}" if run_at else "no run recorded") + offsite_note
    summary += f"; last drill {drill_age_d}d ago" if drill_age_d is not None else ""
    if problems:
        print("backup: " + "; ".join(problems) + f". ({summary})", file=sys.stderr)
        return FINDING_EXIT
    print(f"backup OK - {summary}; nuc good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
