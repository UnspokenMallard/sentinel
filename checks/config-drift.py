#!/Users/anzes/.jarvis/.venv/bin/python
"""Hooks and deny-rule drift between the two Claude Code configs: detect, propose, apply on request.

Both configs (~/.claude-personal and ~/.claude-work) are meant to run the same hook
set and the same deny rules (CLAUDE.md, "Claude Code environment architecture").
Drift happens when a hook or deny rule is added to one and not the other
(2026-10-06..08: read-guard.py on one side, route-prompt.py on the other).

Check (default, the scheduled job): compares the `hooks` blocks and
`permissions.deny`. On drift it writes reports/config-drift/latest.md with what
each side lacks, adds a review item so the ping names it, and exits 1 so the job
alerts. On no drift it clears that review item and exits 0.

Apply (`--apply`, run by Anze only): writes the UNION into both settings.json -
every hook and deny rule present on either side ends up on both. It only adds,
never removes: a rule missing on one side may have been removed on purpose, and
the union keeps it until Anze removes it from both. Each file is backed up to
state/config-drift/ first. The daemon never runs --apply: hooks are the guard
layer, and a daemon that writes them could switch a guard off.
Usage: config-drift.py [--apply]
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

FINDING_EXIT = 87  # a check ran and found a problem; jarvis daemon/core.py FINDING_EXIT

HOME = Path.home()
JARVIS = HOME / ".jarvis"
CONFIGS = {"personal": HOME / ".claude-personal" / "settings.json",
           "work": HOME / ".claude-work" / "settings.json"}
REPORT = JARVIS / "reports" / "config-drift" / "latest.md"
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Heimdall owns a check's artifact and history (2026-10-17)
ARTIFACT = CHECKS / "config-drift.json"
BACKUPS = CHECKS / "config-drift"
SOURCE = "config-drift"


def hook_entries(settings: dict) -> list[tuple[str, str | None, str]]:
    """Every hook as (event, matcher, canonical JSON of the hook), order kept."""
    out = []
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups:
            for hook in group.get("hooks", []):
                out.append((event, group.get("matcher"), json.dumps(hook, sort_keys=True)))
    return out


def deny_rules(settings: dict) -> list[str]:
    return list((settings.get("permissions") or {}).get("deny") or [])


def lacking(have: dict, other: dict) -> dict:
    """What `have` lacks that `other` has: {"hooks": [...], "deny": [...]}."""
    mine = set(hook_entries(have))
    deny = set(deny_rules(have))
    return {"hooks": [e for e in hook_entries(other) if e not in mine],
            "deny": [r for r in deny_rules(other) if r not in deny]}


def with_union(settings: dict, missing: dict) -> dict:
    """A copy of `settings` with the missing hooks and deny rules added. Never removes anything."""
    out = copy.deepcopy(settings)
    hooks = out.setdefault("hooks", {})
    for event, matcher, hook_json in missing["hooks"]:
        groups = hooks.setdefault(event, [])
        group = next((g for g in groups if g.get("matcher") == matcher), None)
        if group is None:
            group = {"matcher": matcher, "hooks": []} if matcher is not None else {"hooks": []}
            groups.append(group)
        group.setdefault("hooks", []).append(json.loads(hook_json))
    if missing["deny"]:
        out.setdefault("permissions", {}).setdefault("deny", []).extend(missing["deny"])
    return out


def describe(name: str, missing: dict) -> list[str]:
    lines = []
    for event, matcher, hook_json in missing["hooks"]:
        cmd = json.loads(hook_json).get("command", hook_json)
        lines.append(f"- {name} lacks hook {event}" + (f" [{matcher}]" if matcher else "") + f": `{cmd}`")
    lines += [f"- {name} lacks deny rule `{r}`" for r in missing["deny"]]
    return lines


def load() -> dict[str, dict]:
    return {name: json.loads(path.read_text(encoding="utf-8")) for name, path in CONFIGS.items()}


def drift(settings: dict[str, dict]) -> dict[str, dict]:
    return {"personal": lacking(settings["personal"], settings["work"]),
            "work": lacking(settings["work"], settings["personal"])}


def _review():
    sys.path.insert(0, str(Path.home() / ".forge"))  # the review queue is Brokkr's since 2026-10-17
    from brokkr import review  # noqa: E402

    return review


def check() -> int:
    settings = load()
    gaps = drift(settings)
    count = sum(len(g["hooks"]) + len(g["deny"]) for g in gaps.values())
    now = datetime.now().astimezone()
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps({"checked_at": now.isoformat(), "differences": count,
                                    "gaps": {n: {"hooks": [list(e) for e in g["hooks"]], "deny": g["deny"]}
                                             for n, g in gaps.items()}}, indent=2))
    review = _review()
    if count == 0:
        changed = False
        for item in review.waiting():
            if item["source"] == SOURCE:
                changed |= review.clear(int(item["id"]), "accepted", "configs back in sync")
        if changed:
            review.render()
        print("config drift: none - hooks and deny rules identical in both configs")
        return 0

    lines = [f"# Config drift - {now:%Y-%m-%d %H:%M}", "",
             f"{count} difference(s) between ~/.claude-personal and ~/.claude-work (hooks, permissions.deny).", ""]
    for name, gap in gaps.items():
        lines += describe(name, gap)
    lines += ["", "Proposed fix: the union (adds only, removes nothing; backups go to state/config-drift/):", "",
              "    ~/.jarvis/scripts/config-drift.py --apply", "",
              "If a difference is a deliberate removal, remove that entry from the other config by hand instead."]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    review.add(SOURCE, f"Claude config drift: {count} difference(s); see reports/config-drift/latest.md",
               task_id="config-drift")
    review.render()
    print(f"config drift: {count} difference(s): " + "; ".join(l[2:] for l in lines if l.startswith("- "))[:500],
          file=sys.stderr)
    return FINDING_EXIT


def apply() -> int:
    settings = load()
    gaps = drift(settings)
    if not any(g["hooks"] or g["deny"] for g in gaps.values()):
        print("nothing to apply: configs already in sync")
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    for name, gap in gaps.items():
        if not (gap["hooks"] or gap["deny"]):
            continue
        path = CONFIGS[name]
        (BACKUPS / f"backup-{stamp}-{name}.json").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        merged = with_union(settings[name], gap)
        path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for line in describe(name, gap):
            print(f"added: {line[2:]}")
    print(f"backups: {BACKUPS}/backup-{stamp}-*.json")
    return check()


def main() -> int:
    return apply() if "--apply" in sys.argv else check()


if __name__ == "__main__":
    sys.exit(main())
