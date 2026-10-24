#!/Users/anzes/.jarvis/.venv/bin/python
"""Installed library versions per repository, one line each, for the session-start digest.

Agents write code from training-era docs; when the repo runs a newer major, a config
key that no longer exists is set and silently ignored (Session Lesson 2026-10-03:
adapter-node 6). This job reads, for every repo under ~/code with a package.json, the
version actually INSTALLED (node_modules/<pkg>/package.json, else the lockfile) of each
direct dependency plus the toolchain devDependencies in TOOLCHAIN, and writes one line to
state/versions/<repo>.txt. ~/.claude-personal/hooks/session-memory-digest.py appends that
line to the digest of every session started in the repo.
Read-only for the repos. Output: state/versions/*.txt, state/repo-versions.json.
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import json
import sys
from datetime import date
from pathlib import Path

# scripts/notmine.py: repositories Anze marked not his (the selftest loads this file by path)
sys.path.insert(0, str(Path.home() / ".jarvis" / "scripts"))  # notmine.py: the "not mine" list is Jarvis's
import notmine  # noqa: E402

HOME = Path.home()
JARVIS = HOME / ".jarvis"
ROOTS = [HOME / "code" / "eventregistry", HOME / "code" / "personal"]
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Heimdall owns a check's artifact and history (2026-10-17)
OUT_DIR = JARVIS / "state" / "versions"  # read by session-memory-digest.py: a published output, not check state
ARTIFACT = CHECKS / "repo-versions.json"
MAX_ENTRIES = 30
TOOLCHAIN = {"typescript", "jest", "vitest", "vite", "webpack", "eslint", "cypress", "@playwright/test",
             "ts-node", "tsx", "@angular/cli", "@angular-devkit/build-angular", "@sveltejs/kit",
             "@sveltejs/adapter-node", "svelte", "esbuild", "nodemon", "prettier"}


def installed(repo: Path, name: str, lock: dict) -> str | None:
    try:
        return json.loads((repo / "node_modules" / name / "package.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        pass
    return ((lock.get("packages") or {}).get(f"node_modules/{name}") or {}).get("version")


def group_scopes(found: dict[str, str]) -> list[tuple[str, str]]:
    """Collapse 3+ packages of one @scope at the same version into "@scope/* (n)". Pure, for the selftest."""
    groups: dict[tuple[str, str], list[str]] = {}
    for name, v in found.items():
        if name.startswith("@") and "/" in name:
            groups.setdefault((name.split("/")[0], v), []).append(name)
    out, done = [], set()
    for name, v in found.items():
        key = (name.split("/")[0], v) if name.startswith("@") and "/" in name else None
        if key and len(groups[key]) >= 3:
            if key not in done:
                out.append((f"{key[0]}/* ({len(groups[key])})", v))
                done.add(key)
        else:
            out.append((name, v))
    return out


def versions_line(repo: Path) -> tuple[str, dict]:
    pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    try:
        lock = json.loads((repo / "package-lock.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lock = {}
    deps = {d for d in (pkg.get("dependencies") or {}) if not d.startswith("@types/")}
    tools = {d for d in (pkg.get("devDependencies") or {}) if d in TOOLCHAIN} | (deps & TOOLCHAIN)
    # toolchain first: if the line is capped, the cut falls on the long tail, not on typescript
    names = sorted(tools) + sorted(deps - tools)
    found: dict[str, str] = {}
    for name in names:
        v = installed(repo, name, lock)
        found[name] = v or f"not installed ({(pkg.get('dependencies') or {}).get(name) or pkg['devDependencies'][name]})"
    shown = group_scopes(found)[:MAX_ENTRIES]
    more = len(group_scopes(found)) - len(shown)
    engines = (pkg.get("engines") or {}).get("node")
    head = f"Installed versions in {repo.name} ({date.today()}, from node_modules/lockfile; trust these over docs):"
    body = ", ".join(f"{n} {v}" for n, v in shown) + (f", +{more} more in package.json" if more else "")
    return f"{head} {('node engine ' + engines + ', ') if engines else ''}{body}", found


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = {}
    for root in ROOTS:
        if not root.is_dir():
            continue
        for repo in sorted(p for p in root.iterdir() if (p / "package.json").is_file() and notmine.is_mine(p)):
            try:
                line, found = versions_line(repo)
            except (OSError, ValueError, KeyError) as exc:
                summary[repo.name] = f"unreadable: {exc}"
                continue
            (OUT_DIR / f"{repo.name}.txt").write_text(line + "\n", encoding="utf-8")
            summary[repo.name] = len(found)
    ARTIFACT.write_text(json.dumps({"date": str(date.today()), "repos": summary}, indent=2))
    print(f"versions written for {len(summary)} repo(s): " + ", ".join(f"{k} ({v})" for k, v in summary.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
