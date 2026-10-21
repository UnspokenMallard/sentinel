#!/Users/anzes/.jarvis/.venv/bin/python
"""Every place on this Mac that names a path Jarvis depends on, and whether that
path still exists (macOS port of check-paths.ps1). Detection only.

"Ours" means a path under ~/.jarvis or ~/.claude* - the trees a profile move
touches. Sources:
  - launchd agents in ~/Library/LaunchAgents named com.jarvis.* / io.ntfy.* /
    org.eventregistry.*, or whose ProgramArguments name a path of ours
  - config.toml: each enabled job's input paths (script, out_dir) and every
    path-shaped [[actions]] target
  - running python/zsh/node processes: script paths of ours
  - the Caddyfile named in [paths] caddyfile: every `root` and `output file` path,
    i.e. what Caddy serves and logs to (added 2026-10-09 after the Mac move left
    l1/l2/er-web-test pointing at /Users/anzes/Projects/..., unnoticed)
Paths listed in [paths] ignore_missing are parked on purpose (Anze's decision, with a
reason in config.toml) and never fail the check; they are still counted.
Exit 1 with the list when anything is missing; 0 with a summary otherwise.
--extra PATH adds a reference by hand: the positive control.
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import json
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

FINDING_EXIT = 87  # a check ran and found a problem; jarvis daemon/core.py FINDING_EXIT

HOME = Path.home()
J = HOME / ".jarvis"
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Sentinel owns a check's artifact and history (2026-10-17)
ARTIFACT = CHECKS / "paths.json"
# `root * "/x"`, `root "/x"`, `output file "/x"` - quoted or bare
CADDY_PATH_RE = re.compile(r'^\s*(root(?:\s+\*)?|output file)\s+"?(/[^"\s{]+(?: [^"\s{]+)*?)"?\s*(?:\{|$)', re.M)
PATH_RE = re.compile(r"(/(?:Users|opt|usr)/[^\s\"'<>|]*?\.(?:py|sh|zsh|js|mjs|cjs|plist))\b")


def ours(p: str) -> bool:
    return p.startswith(str(J)) or p.startswith(str(HOME / ".claude"))


def suggest(path: str) -> str:
    """For a missing node binary, name the one on PATH now (launchd plists pin an absolute path)."""
    if Path(path).name != "node":
        return ""
    found = shutil.which("node")
    return f" (node on PATH now: {found}; repoint the plist)" if found else " (no node on PATH)"


def main() -> int:
    extra = sys.argv[sys.argv.index("--extra") + 1:] if "--extra" in sys.argv else []
    refs: list[dict] = []
    counts = {"launchd": 0, "config": 0, "processes": 0, "caddy": 0, "extra": 0}
    unreadable: list[str] = []

    def add(bucket: str, source: str, path: str) -> None:
        path = (path or "").strip().strip('"').strip("'")
        if not path.startswith("/"):
            return
        refs.append({"source": source, "path": path})
        counts[bucket] += 1

    try:
        for plist in sorted((HOME / "Library" / "LaunchAgents").glob("*.plist")):
            with open(plist, "rb") as fh:
                d = plistlib.load(fh)
            args = [str(a) for a in d.get("ProgramArguments") or [d.get("Program", "")]]
            paths = [a for a in args if a.startswith("/")] + [m for a in args for m in PATH_RE.findall(a)]
            named = re.match(r"^(com\.jarvis|io\.ntfy|org\.eventregistry)\.", plist.stem)
            if not named and not any(ours(p) for p in paths):
                continue
            for p in dict.fromkeys(paths):
                add("launchd", f"launchd {plist.stem}", p)
            if d.get("WorkingDirectory"):
                add("launchd", f"launchd {plist.stem} (working dir)", d["WorkingDirectory"])
    except Exception as exc:  # noqa: BLE001
        unreadable.append(f"launchd agents: {exc}")

    cfg: dict = {}
    try:
        sys.path.insert(0, str(J / "daemon"))
        import core  # noqa: E402
        cfg = core.load_config()
        for job in cfg.get("jobs", []):
            if not job.get("enabled", True):
                continue
            payload = job.get("payload") or {}
            for key in ("script", "out_dir"):
                if isinstance(payload.get(key), str):
                    add("config", f"job {job['name']} {key}", str(Path(payload[key]).expanduser()))
        for decl in cfg.get("actions", []):
            add("config", f"action {decl.get('name')}", str(decl.get("target") or ""))
    except Exception as exc:  # noqa: BLE001
        unreadable.append(f"config.toml: {exc}")

    try:
        ps = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
        for line in ps.splitlines():
            pid, _, cmd = line.strip().partition(" ")
            if not re.search(r"python|zsh|node", cmd.split(" ")[0] if cmd else ""):
                continue
            for p in PATH_RE.findall(cmd):
                if ours(p):
                    add("processes", f"process {pid}", p)
    except Exception as exc:  # noqa: BLE001
        unreadable.append(f"processes: {exc}")

    ignored: set[str] = set()
    try:
        paths_cfg = cfg.get("paths") or {}
        ignored = {str(Path(p).expanduser()) for p in paths_cfg.get("ignore_missing", [])}
        caddyfile = paths_cfg.get("caddyfile")
        if caddyfile:
            text = Path(caddyfile).expanduser().read_text(encoding="utf-8")
            for m in CADDY_PATH_RE.finditer(text):
                add("caddy", f"Caddyfile {m.group(1)} (line {text.count(chr(10), 0, m.start()) + 1})", m.group(2))
    except Exception as exc:  # noqa: BLE001
        unreadable.append(f"Caddyfile: {exc}")

    for p in extra:
        add("extra", "given with --extra", p)
    # glob-shaped action targets are patterns, not paths
    missing = [r for r in refs if not any(ch in r["path"] for ch in "*?[") and not Path(r["path"]).exists()
               and not any(r["path"] == i or r["path"].startswith(i.rstrip("/") + "/") for i in ignored)]
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps({"checked_at": datetime.now().astimezone().isoformat(), "checked": len(refs),
                                    "by_source": counts, "missing": missing, "unreadable": unreadable}, indent=2))
    summary = (f"{len(refs)} path(s) checked (launchd {counts['launchd']}, config {counts['config']}, "
               f"processes {counts['processes']}, caddy {counts['caddy']}); {len(ignored)} parked")
    if unreadable:
        summary += "; COULD NOT READ: " + " | ".join(unreadable)
    if missing:
        by_path: dict[str, list[str]] = {}
        for m in missing:
            by_path.setdefault(m["path"], []).append(m["source"])
        listing = "; ".join(f"{p} <- {srcs[0]}" + (f" (+{len(srcs) - 1} more)" if len(srcs) > 1 else "") + suggest(p)
                            for p, srcs in list(by_path.items())[:10])
        print(f"{len(by_path)} referenced path(s) do not exist: {listing}. {summary}", file=sys.stderr)
        return FINDING_EXIT
    if unreadable:
        # a source that could not be read is unchecked, not clean: fail loudly
        print(f"paths NOT fully checked - {summary}", file=sys.stderr)
        return 1
    print(f"paths OK - {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
