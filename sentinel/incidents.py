"""Incidents: alerts with a lifecycle, so a fixed problem stops failing gates.

Moved here from Jarvis's `daemon/incidents.py` on 2026-10-17 with its rows and ids. The reasoning
that decided the shape is kept; the history of the 2026-09-05 soak-gate failure is in Jarvis's
JOURNAL.md.

### Identity is the alert *kind*, not the alert key

An incident is keyed on the part of the alert key before the first `:` (`stuck-event`, `loop-crash`),
and the individual keys are its members. 94 stuck events and one crashed worker were one incident with
one cause. One open incident per kind, enforced by a partial unique index.

### Resolution is evidence, and where there is none it says so

  * **a resolver**: a registered function that re-asks the original question against live state. The
    only kind of resolution that is evidence. Resolvers that read Jarvis's own state (heartbeats, jobs,
    the event queue) are registered by Jarvis at startup with `@resolver(kind)`; Sentinel holds the
    registry and runs the pass.
  * **quiet**: nothing of that kind fired for `quiet_resolve_s`. Inference, labelled as such, and only
    for kinds with no resolver. A resolver that exists and cannot answer never degrades into a guess.
  * **a human**: `sentinel resolve <id> <why>`.

A resolver returns three values: resolved, still true, or None = cannot tell. Cannot tell keeps the
incident open. "Could not check" is its own answer and never the good one.

Sentinel observes and records. It never pings, posts, postpones or holds work: Jarvis reads the open
incidents and acts.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable

from . import store
from .store import log

# resolved, evidence  |  still open, evidence  |  None = cannot tell
Resolver = Callable[[dict], "tuple[bool, str] | None"]

RESOLVERS: dict[str, Resolver] = {}

BY_RESOLVER = "auto:resolver"
BY_QUIET = "auto:quiet"
BY_HUMAN = "human"

# Sample of member keys kept on the row. The count is exact; the list is a sample, because a storm can
# produce thousands of keys and a row that grows with the storm is the storm.
_MEMBER_SAMPLE = 20

_COLS = ("id", "kind", "summary", "severity", "opened_at", "last_seen_at", "hits", "members",
         "member_count", "resolved_at", "resolved_by", "resolution", "evidence")


def _cfg() -> dict:
    section = dict(store.load_config().get("incidents") or {})
    section.setdefault("enabled", True)
    # 24h. Long on purpose: this is the inferred path, and the cost of holding a resolved incident open
    # one more day is a line in a report, while the cost of closing a live one early is the failure this
    # module exists to stop. 0 disables quiet resolution entirely.
    section.setdefault("quiet_resolve_s", 86400)
    return section


def kind_of(key: str) -> str:
    """`stuck-event:1001349` -> `stuck-event`."""
    return key.split(":", 1)[0].strip() or key


def resolver(kind: str) -> Callable[[Resolver], Resolver]:
    """Register the function that re-asks a kind's question against live state."""

    def wrap(fn: Resolver) -> Resolver:
        RESOLVERS[kind] = fn
        return fn

    return wrap


def record_alert(key: str, message: str, severity: str = "warn", evidence: str | None = None) -> int | None:
    """Open or refresh the incident this alert belongs to; returns its id.

    Called on every raise, not only the ones Jarvis delivers: a repeat held back by cooldown is still
    evidence the condition holds, and `last_seen_at` is what quiet-resolution reads.

    Never raises. A bookkeeping error here must not be able to swallow an alert.
    """
    try:
        if not _cfg()["enabled"]:
            return None
        now = time.time()
        kind = kind_of(key)
        opened = False
        with store.connect() as conn:
            row = conn.execute(
                "SELECT id, members, member_count FROM incidents WHERE kind = ? AND resolved_at IS NULL",
                (kind,),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO incidents(kind, summary, severity, opened_at, last_seen_at, hits, members, "
                    "member_count, evidence) VALUES(?, ?, ?, ?, ?, 1, ?, 1, ?)",
                    (kind, message[:500], severity, now, now, json.dumps([key]), evidence),
                )
                incident_id = int(cur.lastrowid) if cur.lastrowid is not None else None
                opened = True
            else:
                incident_id, members_json, member_count = row
                try:
                    members = list(json.loads(members_json))
                except (TypeError, ValueError):
                    members = []
                new_member = key not in members
                if new_member and len(members) < _MEMBER_SAMPLE:
                    members.append(key)
                conn.execute(
                    "UPDATE incidents SET last_seen_at = ?, hits = hits + 1, summary = ?, severity = ?, "
                    "members = ?, member_count = ?, evidence = COALESCE(?, evidence) WHERE id = ?",
                    (now, message[:500], severity, json.dumps(members),
                     member_count + (1 if new_member else 0), evidence, incident_id),
                )
        if opened:
            alerts_now()
        return int(incident_id) if incident_id is not None else None
    except Exception as exc:  # noqa: BLE001 - bookkeeping must never eat an alert
        log.debug("sentinel: could not record %s: %s", key, exc)
        return None


def open_incidents(kinds: list[str] | None = None) -> list[dict]:
    sql = f"SELECT {', '.join(_COLS[:9])}, evidence FROM incidents WHERE resolved_at IS NULL"
    args: list[Any] = []
    if kinds:
        sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
        args.extend(kinds)
    sql += " ORDER BY opened_at"
    cols = (*_COLS[:9], "evidence")
    with store.connect() as conn:
        return [dict(zip(cols, r)) for r in conn.execute(sql, args).fetchall()]


def get(incident_id: int) -> dict | None:
    """One incident, open or resolved."""
    with store.connect() as conn:
        row = conn.execute(f"SELECT {', '.join(_COLS)} FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    return dict(zip(_COLS, row)) if row else None


def unresolved(kind_prefixes: list[str]) -> list[dict]:
    """Open incidents whose kind starts with any of these. The call a gate makes: "are there unresolved
    crashes" is a different question from "did a crash alert fire in the last 48h"."""
    return [inc for inc in open_incidents() if any(inc["kind"].startswith(p) for p in kind_prefixes)]


def resolve(incident_id: int, by: str, resolution: str) -> bool:
    """Close one incident. Returns False if it was already closed.

    Idempotent by the `resolved_at IS NULL` guard rather than by reading first: the reconcile pass and
    a human at a terminal can race, and the loser should be a no-op, not a rewrite of someone else's
    resolution.
    """
    with store.connect() as conn:
        cur = conn.execute(
            "UPDATE incidents SET resolved_at = ?, resolved_by = ?, resolution = ? "
            "WHERE id = ? AND resolved_at IS NULL",
            (time.time(), by, resolution[:500], incident_id),
        )
        done = cur.rowcount > 0
    if done:
        alerts_now()
    return done


def reconcile() -> list[str]:
    """Ask every open incident whether it is still true. Returns what changed."""
    settings = _cfg()
    if not settings["enabled"]:
        return []

    changed: list[str] = []
    quiet_s = float(settings["quiet_resolve_s"] or 0)
    now = time.time()

    for inc in open_incidents():
        fn = RESOLVERS.get(inc["kind"])
        if fn is not None:
            try:
                verdict = fn(inc)
            except Exception as exc:  # noqa: BLE001 - a broken resolver holds it open
                log.debug("sentinel: resolver for %s raised: %s", inc["kind"], exc)
                verdict = None
            if verdict is not None and verdict[0]:
                if resolve(inc["id"], BY_RESOLVER, verdict[1]):
                    changed.append(f"resolved #{inc['id']} {inc['kind']} - {verdict[1]}")
            # Still true, or cannot tell: either way it stays open, and it is never eligible for the
            # quiet path.
            continue

        if quiet_s and (now - inc["last_seen_at"]) >= quiet_s:
            age = int((now - inc["last_seen_at"]) / 3600)
            if resolve(
                inc["id"],
                BY_QUIET,
                f"inferred: no {inc['kind']} alert for {age}h, and no resolver exists for this kind",
            ):
                changed.append(f"resolved #{inc['id']} {inc['kind']} - quiet {age}h (inferred)")
    return changed


def prune(retain_days: int = 90) -> int:
    """Resolved incidents only. An open one is never pruned, at any age."""
    cutoff = time.time() - retain_days * 86400
    with store.connect() as conn:
        return conn.execute(
            "DELETE FROM incidents WHERE resolved_at IS NOT NULL AND resolved_at < ?", (cutoff,)
        ).rowcount


def human(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def summary_lines() -> list[str]:
    """One line per open incident, for the surfaces a human reads."""
    lines: list[str] = []
    now = time.time()
    for inc in open_incidents():
        no_resolver = "" if inc["kind"] in RESOLVERS else "  [no resolver]"
        member = f" x{inc['member_count']}" if inc["member_count"] > 1 else ""
        lines.append(
            f"#{inc['id']} {inc['kind']}{member} - open {human(now - inc['opened_at'])}, "
            f"last seen {human(now - inc['last_seen_at'])} ago{no_resolver}: {inc['summary'][:120]}"
        )
    return lines


def report(days: int = 7) -> str:
    cutoff = time.time() - days * 86400
    with store.connect() as conn:
        recent = conn.execute(
            "SELECT id, kind, opened_at, resolved_at, resolved_by, resolution, hits, member_count "
            "FROM incidents WHERE opened_at >= ? ORDER BY opened_at DESC LIMIT 20", (cutoff,),
        ).fetchall()
        by_reason = conn.execute(
            "SELECT resolved_by, COUNT(*) FROM incidents WHERE resolved_at >= ? GROUP BY resolved_by", (cutoff,),
        ).fetchall()

    openrows = open_incidents()
    unresolvable = [i for i in openrows if i["kind"] not in RESOLVERS]

    lines = [f"incidents - last {days}d", ""]
    lines.append(f"  open              : {len(openrows)}")
    lines.append(f"  open, no resolver : {len(unresolvable)}")
    lines.append(f"  resolvers         : {len(RESOLVERS)} ({', '.join(sorted(RESOLVERS))})")
    if by_reason:
        lines.append("")
        for reason, count in by_reason:
            lines.append(f"  resolved {reason or '?':16} {count}")
    if openrows:
        lines += ["", "  OPEN:"]
        lines += [f"    {ln}" for ln in summary_lines()]
    if recent:
        lines += ["", "  recent:"]
        for iid, kind, opened, resolved, by, resolution, hits, members in recent:
            stamp = datetime.fromtimestamp(opened).strftime("%m-%d %H:%M")
            state = f"resolved {human(resolved - opened)} later by {by}" if resolved else "OPEN"
            lines.append(f"    #{iid:<5} {stamp}  {kind:16} {hits:>4} hits  {members:>3} keys  {state}")
            if resolved and resolution:
                lines.append(f"           {resolution[:100]}")
    return "\n".join(lines)


def alerts_now() -> bool:
    """Write the open incidents to ALERTS-NOW.md ("what is wrong right now"); True when it changed.

    Only the fields that change when the set of open incidents changes go in, so a repeat hit does not
    rewrite the file and a reader's "changed in the last 24h" means something happened.
    """
    lines = [
        "# Open incidents",
        "",
        "What is wrong right now, from Sentinel's store (`sentinel incidents`). Rewritten when an incident "
        "opens or resolves. `~/.jarvis/logs/alerts.log` is the full record of what was said.",
        "",
    ]
    openrows = sorted(open_incidents(), key=lambda i: i["opened_at"], reverse=True)
    if not openrows:
        lines.append("None open.")
    for inc in openrows:
        stamp = datetime.fromtimestamp(inc["opened_at"]).astimezone().isoformat(timespec="seconds")
        member = f" x{inc['member_count']}" if inc["member_count"] > 1 else ""
        lines.append(f"- `{stamp}` **{inc['severity'].upper()}** [{inc['kind']}]{member} #{inc['id']} "
                     f"{inc['summary'][:400]}")
    text = "\n".join(lines) + "\n"
    path = store.ALERTS_NOW
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as exc:
        log.debug("sentinel: could not write %s: %s", path.name, exc)
        return False
