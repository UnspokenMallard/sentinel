#!/Users/anzes/.jarvis/.venv/bin/python
"""NUC reachability (added 2026-10-14): raises incident `machine-down:nuc` when it is not.

Anze: "If you detect that ... a machine is not reachable ... make a notification and bug me."
The NUC is the backup target (02:30) and the remote worker, and until now only its own
watchdog pair (ntfy.sh, outside Jarvis) noticed it gone.

The router (the default gateway, read live: it is 192.168.1.254, not .1) is probed too,
from this same process. On 2026-10-01 the NUC was declared offline from two probes that
were themselves broken (lesson "Declared the NUC offline and out of the ingress path from
two invalid probes"): when the router does not answer either, this probe cannot tell,
exits 1 and raises nothing about the NUC.

TCP goes through /usr/bin/nc, not a Python socket: the venv python gets "[Errno 65] No
route to host" for every LAN address (macOS Local Network privacy, measured 2026-10-14)
while system binaries reach it.

A failure is probed again after RETRY_S before it counts, so one dropped packet is not an
incident. A down NUC exits 0: the check worked, and the alert is `machine-down:nuc`
through the artifact's `alerts` (Jarvis delivers: an incident, the phone ping), not a failed job.
Writes state/checks/nuc.json (Heimdall): {checked_at, failing: [alert keys], ...}; the incident's
resolver reads it. Usage: check-nuc.py
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
import time
from datetime import datetime, timezone
from pathlib import Path

JARVIS = Path.home() / ".jarvis"
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Heimdall owns a check's artifact and history (2026-10-17)
ARTIFACT = CHECKS / "nuc.json"
NUC = "192.168.1.185"  # ~/.ssh/config Host nuc
KEY = "machine-down:nuc"
RETRY_S = 20


def pings(host: str) -> bool:
    r = subprocess.run(["/sbin/ping", "-c", "2", "-t", "4", host], capture_output=True, timeout=15)
    return r.returncode == 0


def tcp(host: str, port: int, timeout: int = 5) -> bool:
    r = subprocess.run(["/usr/bin/nc", "-z", "-G", str(timeout), host, str(port)],
                       capture_output=True, timeout=timeout + 10)
    return r.returncode == 0


def gateway() -> str | None:
    r = subprocess.run(["/sbin/route", "-n", "get", "default"], capture_output=True, text=True, timeout=10)
    m = re.search(r"gateway:\s*(\S+)", r.stdout)
    return m.group(1) if m else None


def judge(router: bool, nuc_ping: bool, nuc_ssh: bool) -> tuple[str, str | None]:
    """("ok" | "down" | "cannot-tell", message). Pure, so the selftest can drive it."""
    if nuc_ssh:
        return "ok", None
    if not router:
        return "cannot-tell", "the router does not answer either (or there is no default route); the probe is broken"
    if nuc_ping:
        return "down", f"NUC ({NUC}) answers ping but not ssh :22 - sshd or WSL is down"
    return "down", f"NUC ({NUC}) is unreachable: no ping, no ssh :22, while the router answers"


def probe() -> tuple[str, str | None, dict]:
    nuc_ssh = tcp(NUC, 22)
    if nuc_ssh:
        return "ok", None, {"router": None, "nuc_ping": None, "nuc_ssh": True}
    time.sleep(RETRY_S)
    router = gateway()
    seen = {"router": bool(router) and pings(router), "nuc_ping": pings(NUC), "nuc_ssh": tcp(NUC, 22)}
    verdict, message = judge(seen["router"], seen["nuc_ping"], seen["nuc_ssh"])
    return verdict, message, seen


def main() -> int:
    verdict, message, seen = probe()
    now = datetime.now(timezone.utc).isoformat()
    if verdict == "cannot-tell":
        # No artifact refresh: a stale artifact is the resolver's "cannot tell" too.
        print(f"nuc: {message}", file=sys.stderr)
        return 1
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    # "alerts": what Jarvis should say. Heimdall records; the job handler delivers (deliver_alerts).
    alerts = [{"key": KEY, "message": message, "severity": "error", "cooldown_s": 3600}] if verdict == "down" else []
    ARTIFACT.write_text(json.dumps({"checked_at": now, "failing": [KEY] if verdict == "down" else [],
                                    "message": message, "alerts": alerts, **seen}, indent=2))
    if verdict == "down":
        print(f"nuc: DOWN - {message}")
        return 0
    print("nuc OK - ssh :22 answers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
