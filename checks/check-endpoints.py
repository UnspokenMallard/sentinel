#!/Users/anzes/.jarvis/.venv/bin/python
"""Public endpoint probe: HTTP status and TLS certificate expiry (detection only).

For each host in [endpoints] hosts (config.toml) it makes one request over HTTPS
and reads the leaf certificate's notAfter date. A host with an entry in
[endpoints] health is fetched (GET) at that path and must answer 2xx/3xx; the
others get a HEAD on "/" and only must not answer 5xx. Appends one line per host per
run to state/endpoints-history.jsonl. Fails when a host is unreachable, answers 5xx,
or its certificate expires within CERT_WARN_DAYS. Without a health path, 4xx is
recorded, not a failure: several of these hosts answer 401/404 to a bare HEAD.
Usage: check-endpoints.py
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import json
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

FINDING_EXIT = 87  # a check ran and found a problem; jarvis daemon/core.py FINDING_EXIT

JARVIS = Path.home() / ".jarvis"
CHECKS = Path.home() / ".sentinel" / "state" / "checks"  # Heimdall owns a check's artifact and history (2026-10-17)
HISTORY = CHECKS / "endpoints-history.jsonl"
ARTIFACT = CHECKS / "endpoints.json"
CERT_WARN_DAYS = 14
TIMEOUT_S = 10


def http_status(host: str, path: str | None = None) -> int:
    """HEAD on / without a health path, GET on the path with one. 0 when nothing answered."""
    method = ["-sI"] if path is None else ["-s"]
    r = subprocess.run(["curl", *method, "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(TIMEOUT_S),
                        f"https://{host}{path or '/'}"], capture_output=True, text=True, timeout=TIMEOUT_S + 5)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


def cert_not_after(host: str) -> datetime | None:
    """notAfter of the leaf certificate, as an aware datetime, or None if the TLS handshake fails."""
    cmd = (f"echo | openssl s_client -servername {host} -connect {host}:443 2>/dev/null "
           f"| openssl x509 -noout -enddate 2>/dev/null")
    r = subprocess.run(["/bin/zsh", "-c", cmd], capture_output=True, text=True, timeout=TIMEOUT_S + 10)
    line = r.stdout.strip()
    if not line.startswith("notAfter="):
        return None
    return datetime.strptime(line.split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)


def judge(host: str, status: int, path: str | None, days: int | None) -> list[str]:
    """Problems for one probe result. Pure, so the selftest can drive it."""
    problems = []
    if status == 0:
        problems.append(f"{host} unreachable")
    elif status >= 500:
        problems.append(f"{host} answers {status}")
    elif path is not None and not 200 <= status < 400:
        problems.append(f"{host}{path} answers {status} (health path, expected 2xx/3xx)")
    if days is None:
        problems.append(f"{host} TLS handshake failed")
    elif days <= CERT_WARN_DAYS:
        problems.append(f"{host} certificate expires in {days}d")
    return problems


def main() -> int:
    cfg = tomllib.loads((JARVIS / "config.toml").read_text())
    hosts = (cfg.get("endpoints") or {}).get("hosts") or []
    health = (cfg.get("endpoints") or {}).get("health") or {}
    if not hosts:
        print("no [endpoints] hosts configured", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    rows, problems = [], []
    for host in hosts:
        path = health.get(host)
        status = http_status(host, path)
        not_after = cert_not_after(host)
        days = (not_after - now).days if not_after else None
        rows.append({"host": host, "path": path, "status": status, "cert_days_left": days})
        problems += judge(host, status, path, days)

    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a") as fh:
        for r in rows:
            fh.write(json.dumps({"at": now.isoformat(), **r}) + "\n")
    ARTIFACT.write_text(json.dumps({"checked_at": now.isoformat(), "rows": rows, "problems": problems}, indent=2))

    if problems:
        print("endpoints: " + "; ".join(problems) + f". ({len(hosts)} probed)", file=sys.stderr)
        return FINDING_EXIT
    nearest = min((r["cert_days_left"] for r in rows if r["cert_days_left"] is not None), default=None)
    print(f"endpoints OK - {len(hosts)} probed; nearest certificate expiry {nearest}d")
    return 0


if __name__ == "__main__":
    sys.exit(main())
