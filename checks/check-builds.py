#!/Users/anzes/.jarvis/.venv/bin/python
"""GitHub Actions builds (added 2026-10-14): raises incident `build-failed` for a broken branch.

Anze: "If you detect that ... a build failed ... make a notification and bug me."

Repositories: every git repo under ~/code/*/ and ~/.jarvis that has .github/workflows and
is not marked "not mine" (notmine.is_mine). For each GitHub remote, the branches watched
are the remote's default branch (refs/remotes/<remote>/HEAD, read locally) and the branch
the checkout tracks on that remote. Runs on other people's feature branches are not judged.

A branch is broken when the newest completed run of a workflow on it ended in failure,
timed_out or startup_failure, within MAX_AGE_DAYS. The alert key is
`build-failed:<repo>/<workflow>@<remote>/<branch>`, and its message carries
"run <id> on <owner/repo>", which daemon/playbook.py reads for the gh commands it suggests.

A broken build exits 0: the check worked, and the alert goes through the artifact's `alerts` (Jarvis delivers: an
incident, the phone ping). Exit 1 only when no remote could be asked (gh missing,
offline): that is "cannot tell", and the artifact is not refreshed.
Writes state/checks/builds.json (Sentinel): {checked_at, failing: [alert keys], rows, errors}.
Usage: check-builds.py [--dry-run]   (--dry-run raises no alert)
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9; the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JARVIS = Path.home() / ".jarvis"
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Sentinel owns a check's artifact and history (2026-10-17)
ARTIFACT = CHECKS / "builds.json"
ROOTS = [Path.home() / "code"]
BAD = {"failure", "timed_out", "startup_failure"}
MAX_AGE_DAYS = 30
RUNS = 40
# Build times (2026-10-15, Anze): every successful run's duration per workflow and branch, kept
# 30 days. The newest run SLOW_FACTOR x slower than the 7-day median of the ones before it
# (at least SLOW_MIN_RUNS of them, and SLOW_MIN_S longer) raises `build-slow:...` (warn).
TIMES = CHECKS / "build-times.json"
SLOW_FACTOR = 1.5
SLOW_MIN_RUNS = 3
SLOW_MIN_S = 60

sys.path.insert(0, str(Path.home() / ".jarvis" / "scripts"))  # notmine.py: the "not mine" list is Jarvis's
import notmine  # noqa: E402


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)
    return r.stdout.strip() if r.returncode == 0 else ""


def repos() -> list[Path]:
    found = [JARVIS] if (JARVIS / ".github" / "workflows").is_dir() else []
    for root in ROOTS:
        for d in sorted(root.glob("*/*")):
            if (d / ".git").exists() and (d / ".github" / "workflows").is_dir() and notmine.is_mine(d):
                found.append(d)
    return found


def slug_of(url: str) -> str | None:
    m = re.match(r"^(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def watched(repo: Path) -> list[tuple[str, str, set[str]]]:
    """(remote, owner/repo, branches) for each GitHub remote of the checkout."""
    upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    out = []
    for remote in git(repo, "remote").split():
        slug = slug_of(git(repo, "remote", "get-url", remote))
        if not slug:
            continue
        branches = set()
        head = git(repo, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD")
        if head.startswith(f"{remote}/"):
            branches.add(head[len(remote) + 1:])
        if upstream.startswith(f"{remote}/"):
            branches.add(upstream[len(remote) + 1:])
        if branches:
            out.append((remote, slug, branches))
    return out


def broken(runs: list[dict], branches: set[str], now: datetime) -> list[dict]:
    """The newest completed run per (workflow, branch), when it failed. Pure, for the selftest."""
    newest: dict[tuple[str, str], dict] = {}
    for run in runs:
        if run.get("status") != "completed" or run.get("headBranch") not in branches:
            continue
        key = (run.get("workflowName") or "?", run["headBranch"])
        if key not in newest or run.get("createdAt", "") > newest[key].get("createdAt", ""):
            newest[key] = run
    cutoff = (now - timedelta(days=MAX_AGE_DAYS)).isoformat().replace("+00:00", "Z")
    return [r for r in newest.values() if r.get("conclusion") in BAD and r.get("createdAt", "") >= cutoff]


def runs_of(slug: str) -> list[dict]:
    r = subprocess.run(["gh", "run", "list", "-R", slug, "--limit", str(RUNS), "--json",
                        "databaseId,headBranch,status,conclusion,workflowName,createdAt,startedAt,updatedAt,url,event"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200] or f"gh exit {r.returncode}")
    return json.loads(r.stdout or "[]")


def _ts(value: str | None) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def record_times(times: dict, slug: str, runs: list[dict], branches: set[str], now: datetime) -> list[tuple[str, str]]:
    """Add successful runs' durations to `times`; return (key, message) for a slow newest run.

    Keyed by GitHub repo (owner/name), not checkout: er-web, er-web-bg and er-web-main share
    one repo, and a run is one sample whichever checkout watches its branch."""
    slow = []
    groups: dict[str, list[dict]] = {}
    for run in runs:
        if run.get("conclusion") != "success" or run.get("headBranch") not in branches:
            continue
        start, end = _ts(run.get("startedAt") or run.get("createdAt")), _ts(run.get("updatedAt"))
        if start is None or end is None or end <= start:
            continue
        workflow = re.sub(r"[^\w. -]", "", run.get("workflowName") or "?").strip() or "?"
        gkey = f"{slug}/{workflow}@{run['headBranch']}"
        entry = times.setdefault(gkey, {})
        entry[str(run["databaseId"])] = {"at": start, "s": round(end - start)}
        groups.setdefault(gkey, [])
    cutoff = now.timestamp() - 30 * 86400
    for gkey in list(times):
        for rid in [r for r, v in times[gkey].items() if v["at"] < cutoff]:
            del times[gkey][rid]
        if not times[gkey]:
            del times[gkey]
    for gkey in groups:
        if gkey not in times:  # every run older than 30 days
            continue
        ordered = sorted(times[gkey].values(), key=lambda v: v["at"])
        if len(ordered) < SLOW_MIN_RUNS + 1:
            continue
        newest = ordered[-1]
        week = [v["s"] for v in ordered[:-1] if v["at"] >= newest["at"] - 7 * 86400]
        if len(week) < SLOW_MIN_RUNS:
            continue
        median = sorted(week)[len(week) // 2]
        if newest["s"] >= median * SLOW_FACTOR and newest["s"] - median >= SLOW_MIN_S:
            slow.append((f"build-slow:{gkey}",
                         f"{gkey}: last successful build took {newest['s'] // 60}m{newest['s'] % 60:02d}s, "
                         f"{newest['s'] / median:.1f}x the 7-day median of {median // 60}m{median % 60:02d}s"))
    return slow


def key_of(repo: Path, remote: str, run: dict) -> str:
    workflow = re.sub(r"[^\w. -]", "", run.get("workflowName") or "?").strip() or "?"
    return f"build-failed:{repo.name}/{workflow}@{remote}/{run['headBranch']}"


def main() -> int:
    dry = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc)
    rows, errors, failures = [], [], []
    asked = 0
    # Two checkouts of one GitHub repo (er-web, er-web-bg) ask it once and report a
    # broken branch once, under the first checkout's name.
    cache: dict[str, list[dict]] = {}
    reported: set[int] = set()
    try:
        times = json.loads(TIMES.read_text())
    except (OSError, ValueError):
        times = {}
    slow: dict[str, str] = {}
    for repo in repos():
        for remote, slug, branches in watched(repo):
            if slug not in cache:
                try:
                    cache[slug] = runs_of(slug)
                    asked += 1
                except (RuntimeError, ValueError, subprocess.TimeoutExpired, OSError) as exc:
                    errors.append(f"{slug}: {exc}")
                    cache[slug] = []
                    continue
            slow.update(record_times(times, slug, cache[slug], set(branches), now))
            bad = [r for r in broken(cache[slug], branches, now) if r["databaseId"] not in reported]
            reported.update(r["databaseId"] for r in bad)
            rows.append({"repo": repo.name, "remote": remote, "slug": slug, "branches": sorted(branches),
                         "failed": [r["databaseId"] for r in bad]})
            for run in bad:
                failures.append((key_of(repo, remote, run),
                                 f"{repo.name}: {run.get('workflowName')} failed on {remote}/{run['headBranch']} "
                                 f"({run.get('event')}, {run.get('createdAt')}) - run {run['databaseId']} on {slug} "
                                 f"{run.get('url', '')}".strip()))
    if not asked and errors:
        print("builds: no remote could be asked: " + "; ".join(errors), file=sys.stderr)
        return 1
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    # "alerts": what Jarvis should say. Sentinel records; the job handler delivers (deliver_alerts).
    alerts = [] if dry else (
        [{"key": k, "message": m, "severity": "error", "cooldown_s": 3600} for k, m in failures]
        + [{"key": k, "message": m, "severity": "warn", "cooldown_s": 86400} for k, m in slow.items()])
    ARTIFACT.write_text(json.dumps({"checked_at": now.isoformat(), "failing": [k for k, _ in failures],
                                    "slow": sorted(slow), "rows": rows, "errors": errors, "alerts": alerts}, indent=2))
    if not dry:
        TIMES.write_text(json.dumps(times, indent=1))
    for _, message in failures:
        print(f"builds: FAILED - {message}")
    for message in slow.values():
        print(f"builds: SLOW - {message}")
    print(f"builds: {asked} remote(s) asked, {len(failures)} broken"
          + (f", {len(errors)} unreachable: {'; '.join(errors)}" if errors else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
