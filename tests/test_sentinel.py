"""Sentinel tests. Run: ~/.jarvis/.venv/bin/python ~/.sentinel/tests/test_sentinel.py"""
import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sentinel-test-"))
os.environ["SENTINEL_HOME"] = str(_TMP)  # before anything imports sentinel
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sentinel  # noqa: E402
from sentinel import alerts, cli, incidents, store  # noqa: E402

assert store.DB_PATH.parent == _TMP / "state", store.DB_PATH


def test_raise_groups_by_kind():
    a = sentinel.raise_("stuck-event:1", "one", "error")
    b = sentinel.raise_("stuck-event:2", "two", "error", evidence="e")
    assert a["incident"] == b["incident"] and b["count"] == 1
    inc = sentinel.open_incidents(["stuck-event"])[0]
    assert inc["member_count"] == 2 and inc["hits"] == 2 and inc["summary"] == "two" and inc["evidence"] == "e"
    assert sentinel.raise_("stuck-event:1", "again")["count"] == 2


def test_held_alert_opens_no_incident():
    r = sentinel.raise_("held:x", "postponed", incident=False)
    assert r["incident"] is None and r["count"] == 1 and not sentinel.open_incidents(["held"])


def test_resolve_is_idempotent_and_reopens_new_row():
    iid = sentinel.raise_("disk:/", "full")["incident"]
    assert sentinel.resolve(iid, sentinel.BY_HUMAN, "freed") and not sentinel.resolve(iid, sentinel.BY_HUMAN, "again")
    assert sentinel.get(iid)["resolution"] == "freed"
    assert sentinel.raise_("disk:/", "full again")["incident"] != iid


def test_reconcile_resolver_and_cannot_tell():
    sentinel.raise_("rv-yes:a", "x")
    sentinel.raise_("rv-no:a", "x")
    sentinel.raise_("rv-unknown:a", "x")
    sentinel.resolver("rv-yes")(lambda inc: (True, "gone"))
    sentinel.resolver("rv-no")(lambda inc: (False, "still"))
    sentinel.resolver("rv-unknown")(lambda inc: None)
    changed = sentinel.reconcile()
    assert any("rv-yes" in c for c in changed) and len(changed) == 1
    assert {i["kind"] for i in sentinel.open_incidents()} >= {"rv-no", "rv-unknown"}


def test_quiet_only_without_resolver():
    sentinel.raise_("quiet-kind:a", "x")
    sentinel.raise_("rv-no:b", "x")
    with store.connect() as conn:
        conn.execute("UPDATE incidents SET last_seen_at = 0 WHERE resolved_at IS NULL")
    changed = sentinel.reconcile()
    open_kinds = {i["kind"] for i in sentinel.open_incidents()}
    assert "quiet-kind" not in open_kinds and "rv-no" in open_kinds and "rv-unknown" in open_kinds, changed


def test_prune_keeps_open():
    iid = sentinel.raise_("old:a", "x")["incident"]
    sentinel.resolve(iid, sentinel.BY_HUMAN, "t")
    with store.connect() as conn:
        conn.execute("UPDATE incidents SET resolved_at = 1 WHERE id = ?", (iid,))
    assert sentinel.prune(90) >= 1 and sentinel.get(iid) is None and sentinel.open_incidents()


def test_alerts_now_only_rewrites_on_change():
    incidents.alerts_now()
    text = store.ALERTS_NOW.read_text()
    assert "Open incidents" in text and incidents.alerts_now() is False
    sentinel.raise_("brand-new:a", "fresh")
    assert "brand-new" in store.ALERTS_NOW.read_text()


def test_cli():
    assert cli.main(["incidents", "--open"]) == 1
    assert cli.main(["resolve", "999999", "x"]) == 1
    assert cli.main(["nope"]) == 2


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("PASS", name)
    print(f"{n} passed")
