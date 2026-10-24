#!/Users/anzes/.jarvis/.venv/bin/python
"""Local service ports (audit A01, 2026-10-21; detection only).

Mimir's server kept its process for 3 days on 10-18 with no listener on :3024, and nothing alerted: launchd
said "running" and no check opened the port. This one GETs each service's /health. A service that does not
answer is one `service-down:<name>` alert in the artifact, delivered by the Jarvis job (deliver_alerts).
Usage: check-services.py
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FINDING_EXIT = 87  # a check ran and found a problem; jarvis daemon/core.py FINDING_EXIT

ARTIFACT = Path.home() / ".sentinel" / "state" / "checks" / "services.json"
SERVICES = {  # name -> (health url, launchd label)
    "hud": ("http://127.0.0.1:3022/", "com.jarvis.hud"),
    "mind": ("http://127.0.0.1:3024/health", "com.mind.server"),
    "budget": ("http://127.0.0.1:3025/health", "com.budget.server"),
    "herald": ("http://127.0.0.1:3026/health", "com.herald.server"),
    "gateway": ("http://127.0.0.1:3027/health", "com.gateway.server"),
    "ntfy": ("http://127.0.0.1:2586/v1/health", "ntfy"),
    "crowdsec": ("http://127.0.0.1:8080/health", "crowdsec container"),  # LAPI, published by OrbStack
    "syncthing": ("http://127.0.0.1:8384/rest/noauth/health", "sh.brew.syncthing"),
}
TIMEOUT_S = 5


def probe(url: str) -> str | None:
    """None when the service answers below 500; otherwise why not."""
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S):
            return None
    except urllib.error.HTTPError as exc:
        return None if exc.code < 500 else f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - refused, timed out, reset: all mean down
        return f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"


def main(services: dict = SERVICES, artifact: Path = ARTIFACT) -> int:
    rows, alerts = [], []
    for name, (url, label) in services.items():
        why = probe(url)
        rows.append({"name": name, "url": url, "ok": why is None, "error": why})
        if why:
            alerts.append({"key": f"service-down:{name}", "severity": "error", "cooldown_s": 3600,
                           "message": f"{name} ({label}) does not answer {url}: {why}"})
    problems = [a["message"] for a in alerts]
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(), "rows": rows,
                                    "problems": problems, "alerts": alerts}, indent=2))
    if problems:
        print("services: " + "; ".join(problems))
        return FINDING_EXIT
    print(f"services OK - {len(rows)} answered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
