"""`sentinel` CLI (bin/sentinel).

  sentinel incidents [--open] [--json]   list incidents (default: report); exit 1 when --open finds any
  sentinel resolve <id> [why...]         human closes an incident
  sentinel alerts-now                    rewrite ALERTS-NOW.md from the open incidents
  sentinel report [days]
  sentinel check <name> [args...]        run a check; its artifact is state/checks/<name>.json
  sentinel checks                        list the checks and when each last ran

Reconcile is not here: Jarvis's resolvers run inside the daemon, so Jarvis triggers the pass.
"""
from __future__ import annotations

import json
import sys

from . import checks, incidents
from ._mimir import records as mr


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except mr.MimirDown:
        print("sentinel: Mimir is not reachable; the incidents live there. Start it and run again.", file=sys.stderr)
        return 3


def _main(argv: list[str] | None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd, rest = (argv[0] if argv else "report"), argv[1:]
    if cmd == "incidents":
        if "--json" in rest:
            rows = incidents.open_incidents() if "--open" in rest else incidents.recent(7)
            print(json.dumps(rows))
            return 0
        if "--open" in rest:
            lines = incidents.summary_lines()
            print("\n".join(lines))
            return 1 if lines else 0
        print(incidents.report())
        return 0
    if cmd == "resolve" and rest:
        try:
            iid = int(rest[0])
        except ValueError:
            print(f"sentinel: not an id: {rest[0]}")
            return 2
        why = " ".join(rest[1:]) or "resolved by hand"
        if incidents.resolve(iid, incidents.BY_HUMAN, why):
            print(f"resolved #{iid}: {why}")
            return 0
        print(f"sentinel: #{iid} is not open (already resolved, or no such id)")
        return 1
    if cmd == "alerts-now":
        print("written" if incidents.alerts_now() else "unchanged")
        return 0
    if cmd == "check" and rest:
        return checks.run(rest[0], rest[1:])
    if cmd == "checks":
        for n in checks.names():
            try:
                doc = json.loads(checks.artifact_path(n).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = {}
            print(f"{n:16} ran_at={doc.get('ran_at') or doc.get('checked_at') or 'never'} ok={doc.get('ok')}")
        return 0
    if cmd == "report":
        print(incidents.report(int(rest[0]) if rest else 7))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
