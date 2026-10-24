"""Heimdall: what is true about the machines right now (see ../DESIGN.md).

    import heimdall
    heimdall.raise_("disk:/", "93% full", "warn", evidence="df says ...")   # record, never deliver
    heimdall.open_incidents(); heimdall.resolve(id, "human", "why")

Observes and records. Never pings, posts, postpones or holds work: Jarvis reads and acts.
"""
from __future__ import annotations

from . import alerts, incidents
from .incidents import (BY_HUMAN, BY_QUIET, BY_RESOLVER, RESOLVERS, alerts_now, get, kind_of, open_incidents,
                        prune, reconcile, report, resolve, resolver, summary_lines, unresolved)


def raise_(key: str, text: str, severity: str = "warn", evidence: str | None = None, incident: bool = True) -> dict:
    """Record an alert and attach it to its incident. Returns {"count", "incident"}.

    `incident=False` counts the alert and stops there: Jarvis passes it for an alert it holds back
    (postponed until its day), which must not open or refresh an incident.
    """
    count = alerts.record(key, text, severity)
    inc = incidents.record_alert(key, text, severity, evidence) if incident else None
    return {"count": count, "incident": inc}


__all__ = ["raise_", "resolve", "open_incidents", "unresolved", "reconcile", "resolver", "RESOLVERS", "prune",
           "alerts_now", "get", "kind_of", "report", "summary_lines", "BY_HUMAN", "BY_QUIET", "BY_RESOLVER",
           "alerts", "incidents"]
