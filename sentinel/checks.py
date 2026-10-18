"""Checks: scripts under ~/.sentinel/checks that look at the machines and leave one artifact each.

`sentinel check <name>` runs `checks/check-<name>.py` (or `<name>.py`) with Sentinel's interpreter, passes
its exit code through, and stamps its artifact `state/checks/<name>.json` with the envelope every reader can
rely on whatever the check puts in the rest: `ran_at`, `exit`, and `ok` (exit 0 and nothing in `failing` or
`problems`). A run with `--check` in its args is read-only and stamps nothing. A check never alerts: what
Jarvis should say goes in the artifact's `alerts` list (`{key, message, severity, cooldown_s}`), and the
Jarvis job that triggered the check delivers it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime

from . import store

# The check scripts keep their exit contract (0 ran, non-zero could not run); the artifact says whether
# the machines are fine.


def checks_dir():
    return store.ROOT / "checks"


def artifact_path(name: str):
    return store.STATE / "checks" / f"{name}.json"


def names() -> list[str]:
    out = []
    for p in sorted(checks_dir().glob("*.py")):
        out.append(p.stem[len("check-"):] if p.stem.startswith("check-") else p.stem)
    return out


def script_for(name: str):
    for stem in (f"check-{name}", name):
        p = checks_dir() / f"{stem}.py"
        if p.is_file():
            return p
    return None


def stamp(name: str, code: int) -> None:
    """Add the envelope to the artifact the check just wrote. A non-dict or unreadable artifact is left alone."""
    path = artifact_path(name)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(doc, dict):
        return
    doc["ran_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    doc["exit"] = code
    doc["ok"] = code == 0 and not doc.get("failing") and not doc.get("problems")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    tmp.replace(path)


def run(name: str, args: list[str]) -> int:
    script = script_for(name)
    if script is None:
        print(f"sentinel: no check named {name!r} (have: {', '.join(names())})", file=sys.stderr)
        return 2
    code = subprocess.run([sys.executable, str(script), *args]).returncode
    # `--check` only reads an earlier run's artifact: restamping it would hide a run that never happened.
    if code == 0 and "--check" not in args:
        stamp(name, code)
    return code
