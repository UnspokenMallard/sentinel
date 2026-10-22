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
from sentinel import alerts, checks, cli, incidents, store  # noqa: E402

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


def test_check_runs_and_stamps_the_artifact():
    (_TMP / "checks").mkdir(exist_ok=True)
    (_TMP / "checks" / "check-demo.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "p = Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text(json.dumps({'failing': ['demo:x'] if len(sys.argv) > 2 else []}))\n")
    art = checks.artifact_path("demo")
    assert checks.names() == ["demo"]
    assert checks.run("demo", [str(art)]) == 0
    doc = __import__("json").loads(art.read_text())
    assert doc["ok"] is True and doc["exit"] == 0 and "ran_at" in doc
    assert checks.run("demo", [str(art), "fail"]) == 0
    assert __import__("json").loads(art.read_text())["ok"] is False  # exit 0 but something failing
    assert checks.run("nope", []) == 2


def _load_check(file: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(file[:-3].replace("-", "_"), Path(__file__).resolve().parent.parent / "checks" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_commit_audit_pure_rules():
    ca = _load_check("check-commit-audit.py")
    assert ca.crlf_blowups({"a.html": 2600, "b.ts": 40}, {"a.html": 70, "b.ts": 40}) == ["a.html: 2600 lines, 70 without CR changes"]
    assert ca.unmentioned_deletions("Restyle charts", ["js/zoom.tpl.html"]) == ["js/zoom.tpl.html"]
    assert ca.unmentioned_deletions("Remove the old zoom handler", ["js/zoom.tpl.html"]) == []
    assert ca.TRAILER_RE.search("Fix x\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    assert ca.is_acknowledged("93e315b87abc", {"93e315b87": "fixed"}) and not ca.is_acknowledged("93e315b87abc", {"93e3": "short"})


def test_commit_audit_flags_into_the_artifact_and_exits_0():
    import json
    import subprocess
    ca = _load_check("check-commit-audit.py")
    repo = _TMP / "audited" / "repo"
    repo.mkdir(parents=True)
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    git("init", "-q")
    git("config", "user.email", "me@example.com")
    git("config", "user.name", "me")
    (repo / "a.txt").write_text("x\n")
    git("add", "-A")
    git("commit", "-qm", "Add a\n\nCo-authored-by: Claude <noreply@anthropic.com>")
    ca.ROOTS, ca.EXTRA = [_TMP / "audited"], []
    ca.REPORT, ca.ARTIFACT, ca.CONFIG = _TMP / "r" / "latest.md", _TMP / "state" / "checks" / "commit-audit.json", _TMP / "none.toml"
    assert ca.main() == 0
    doc = json.loads(ca.ARTIFACT.read_text())
    assert doc["commits"] == 1 and doc["failing"] and doc["alerts"][0]["key"] == "commit-audit:flagged"
    assert "attribution trailer" in ca.REPORT.read_text()
    git("commit", "-q", "--allow-empty", "-m", "Clean")
    sha = subprocess.run(["git", "-C", str(repo), "log", "-2", "--format=%H"], capture_output=True, text=True).stdout.split()[1]
    ca.CONFIG.write_text(f'[commit_audit.acknowledged]\n"{sha[:9]}" = "test"\n')
    assert ca.main() == 0
    doc = json.loads(ca.ARTIFACT.read_text())
    assert doc["flagged"] == [] and doc["alerts"] == [] and doc["failing"] == []


def test_check_with_dash_check_does_not_restamp():
    import json
    (_TMP / "checks" / "check-readonly.py").write_text(
        "import json, sys\nfrom pathlib import Path\np = Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text(json.dumps({'outcome': 'ran'}))\n")
    art = checks.artifact_path("readonly")
    assert checks.run("readonly", [str(art)]) == 0 and "ran_at" in json.loads(art.read_text())
    art.write_text(json.dumps({"outcome": "ran"}))
    assert checks.run("readonly", [str(art), "--check"]) == 0
    assert "ran_at" not in json.loads(art.read_text())


def test_dep_rehearsal_check_reads_the_last_run_and_picks_targets():
    import datetime as dt
    import json
    dr = _load_check("check-dep-rehearsal.py")
    dr.ARTIFACT = _TMP / "state" / "checks" / "dep-rehearsal.json"
    dr.ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    dr.ARTIFACT.unlink(missing_ok=True)
    assert dr.check() == 1  # no artifact: the check cannot run
    dr.ARTIFACT.write_text(json.dumps({"ran_at": "t", "outcome": "error", "error": "boom"}))
    assert dr.check() == dr.FINDING_EXIT
    dr.ARTIFACT.write_text(json.dumps({"ran_at": "t", "outcome": "ran",
                                       "repos": {"er-web": {"baseline_ok": True, "safe_verified": 3, "breaking": 1}}}))
    assert dr.check() == 0
    old, new = dt.datetime(2026, 1, 1), dt.datetime(2026, 10, 1)
    times = {"1.2.0": old, "1.3.0": old, "1.3.1": new, "2.0.0": old}
    assert dr.pick("1.2.0", times, dt.datetime(2026, 9, 1)) == ("1.3.0", "2.0.0")  # 1.3.1 is too fresh
    assert dr.pick("0.4.0", {"0.4.1": old, "0.5.0": old}, dt.datetime(2026, 9, 1)) == ("0.4.1", "0.5.0")
    assert dr.group_key("@angular/core") == dr.group_key("@angular-devkit/build-angular") == "angular"
    assert dr.group_key("@types/node") == "@types/node" and dr.group_key("@nestjs/core") == "@nestjs"


def test_services_drill_closed_and_wedged_listeners():
    import json
    import socket
    svc = _load_check("check-services.py")
    svc.TIMEOUT_S = 1
    wedged = socket.socket()
    wedged.bind(("127.0.0.1", 0))
    wedged.listen(1)  # accepts into the backlog, never answers: Mind on 10-18
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    try:
        code = svc.main({"wedged": (f"http://127.0.0.1:{wedged.getsockname()[1]}/health", "w"),
                         "closed": (f"http://127.0.0.1:{closed_port}/health", "c")}, _TMP / "services.json")
    finally:
        wedged.close()
    doc = json.loads((_TMP / "services.json").read_text())
    assert code == svc.FINDING_EXIT
    assert [a["key"] for a in doc["alerts"]] == ["service-down:wedged", "service-down:closed"]


def test_launchd_flags_only_idle_agents_with_a_bad_exit():
    lc = _load_check("check-launchd.py")
    listing = ("PID\tStatus\tLabel\n12\t-15\tcom.jarvis.hud\n-\t0\tcom.mind.sleep\n"
               "-\t1\tcom.jarvis.healthcheck\n-\t1\tcom.apple.other\n")
    assert lc.main(listing, _TMP / "launchd.json") == 0
    doc = __import__("json").loads((_TMP / "launchd.json").read_text())
    assert doc["failing"] == ["com.jarvis.healthcheck last exited 1"] and len(doc["rows"]) == 3


def test_launchd_watches_anzes_and_brew_agents_and_exempts_keepalive_sigterm():
    lc = _load_check("check-launchd.py")
    listing = ("PID\tStatus\tLabel\n-\t-15\tcom.anzes.helper\n-\t-15\tcom.anzes.mac-backup\n"
               "-\t0\tcom.anzes.env-check\n-\t1\tsh.brew.ollama\n-\t1\tsh.brew.other\n-\t1\tcom.apple.x\n")
    rows = lc.parse(listing, {"com.anzes.helper", "sh.brew.ollama"})
    assert [r["label"] for r in rows] == ["com.anzes.helper", "com.anzes.mac-backup", "com.anzes.env-check", "sh.brew.ollama"]
    assert [r["label"] for r in rows if r["failing"]] == ["com.anzes.mac-backup", "sh.brew.ollama"]


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("PASS", name)
    print(f"{n} passed")
