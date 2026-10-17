"""`sentinel` CLI (bin/sentinel).

  sentinel incidents [--open] [--json]   list incidents (default: report); exit 1 when --open finds any
  sentinel resolve <id> [why...]         human closes an incident
  sentinel alerts-now                    rewrite ALERTS-NOW.md from the open incidents
  sentinel report [days]

Reconcile is not here: Jarvis's resolvers run inside the daemon, so Jarvis triggers the pass.
`check <name>` arrives with the checks.
"""
from __future__ import annotations

import json
import sys

from . import incidents


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd, rest = (argv[0] if argv else "report"), argv[1:]
    if cmd == "incidents":
        if "--json" in rest:
            rows = incidents.open_incidents() if "--open" in rest else None
            if rows is None:
                days = 7
                import time
                with incidents.store.connect() as conn:
                    cols = incidents._COLS
                    rows = [dict(zip(cols, r)) for r in conn.execute(
                        f"SELECT {', '.join(cols)} FROM incidents WHERE opened_at >= ? ORDER BY opened_at DESC",
                        (time.time() - days * 86400,)).fetchall()]
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
    if cmd == "report":
        print(incidents.report(int(rest[0]) if rest else 7))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
