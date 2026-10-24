#!/Users/anzes/.jarvis/.venv/bin/python
"""Credential expiry watch (detection only).

Secret files carry no expiry metadata, so the dates live in secrets/expiry.json,
one entry per credential, written by hand when the credential is created or
renewed:  {"npm (event-registry)": "2026-12-31", "notion": "2027-03-01"}
Values are ISO dates. Fails when a recorded date is within WARN_DAYS or past.
A null value means the provider was checked and sets no expiry; it is listed but never counted down.
Credentials with no entry are listed as info only, never as failures. Never
reads or prints a secret value; never calls a provider.
Usage: check-expiry.py
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

FINDING_EXIT = 87  # a check ran and found a problem; jarvis daemon/core.py FINDING_EXIT

JARVIS = Path.home() / ".jarvis"
REGISTRY = JARVIS / "secrets" / "expiry.json"
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Heimdall owns a check's artifact and history (2026-10-17)
ARTIFACT = CHECKS / "expiry.json"
WARN_DAYS = 14
SECRET_FILES = sorted(p.stem for p in (JARVIS / "secrets").glob("*.json") if p.name != "expiry.json")


def main() -> int:
    today = date.today()
    try:
        recorded = json.loads(REGISTRY.read_text())
    except (OSError, ValueError) as exc:
        print(f"expiry registry unreadable ({REGISTRY}): {exc}", file=sys.stderr)
        return 1

    problems: list[str] = []
    rows: list[dict] = []
    for name, iso in recorded.items():
        if iso is None:  # checked at the provider: no expiry set (null), so nothing to count down
            rows.append({"name": name, "expires": None, "days_left": None})
            continue
        try:
            due = date.fromisoformat(iso)
        except (TypeError, ValueError):
            problems.append(f"{name}: bad date {iso!r}")
            continue
        days = (due - today).days
        rows.append({"name": name, "expires": iso, "days_left": days})
        if days < 0:
            problems.append(f"{name} EXPIRED {-days}d ago ({iso})")
        elif days <= WARN_DAYS:
            problems.append(f"{name} expires in {days}d ({iso})")

    unrecorded = [s for s in SECRET_FILES if not any(s in k for k in recorded)]
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps({
        "checked_at": today.isoformat(), "rows": rows, "unrecorded": unrecorded, "problems": problems,
    }, indent=2))

    if problems:
        print("credentials: " + "; ".join(problems), file=sys.stderr)
        return FINDING_EXIT
    dated = [r for r in rows if r["expires"]]
    nearest = min((r["days_left"] for r in dated), default=None)
    tail = f"; nearest in {nearest}d" if nearest is not None else ""
    print(f"credentials OK - {len(dated)} dated, {len(rows) - len(dated)} recorded as no-expiry{tail}; "
          f"no date recorded for: {', '.join(unrecorded) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
