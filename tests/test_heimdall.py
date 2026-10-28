"""Heimdall tests, against a scratch Mimir store. Run: tests/run.sh (prints "N passed")"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="heimdall-test-"))
os.environ["HEIMDALL_HOME"] = str(_TMP)  # before anything imports heimdall
os.environ["MEMORY_DB"] = str(_TMP / "memory.db")  # Mimir's store, a scratch one
os.environ["MIMIR_OUTBOX"] = str(_TMP / "outbox")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path.home() / ".mind" / "mimir"))

import mem  # noqa: E402
import mimirrecords as mr  # noqa: E402

mr.use_local(mem.connect(shared=True))  # calls go to the scratch store in-process, no HTTP

import heimdall  # noqa: E402
from heimdall import alerts, checks, cli, incidents, store  # noqa: E402


def test_raise_groups_by_kind():
    a = heimdall.raise_("stuck-event:1", "one", "error")
    b = heimdall.raise_("stuck-event:2", "two", "error", evidence="e")
    assert a["incident"] == b["incident"] and b["count"] == 1
    inc = heimdall.open_incidents(["stuck-event"])[0]
    assert inc["member_count"] == 2 and inc["hits"] == 2 and inc["summary"] == "two" and inc["evidence"] == "e"
    assert heimdall.raise_("stuck-event:1", "again")["count"] == 2


def test_held_alert_opens_no_incident():
    r = heimdall.raise_("held:x", "postponed", incident=False)
    assert r["incident"] is None and r["count"] == 1 and not heimdall.open_incidents(["held"])


def test_resolve_is_idempotent_and_reopens_new_row():
    iid = heimdall.raise_("disk:/", "full")["incident"]
    assert heimdall.resolve(iid, heimdall.BY_HUMAN, "freed") and not heimdall.resolve(iid, heimdall.BY_HUMAN, "again")
    assert heimdall.get(iid)["resolution"] == "freed"
    assert heimdall.raise_("disk:/", "full again")["incident"] != iid


def test_reconcile_resolver_and_cannot_tell():
    heimdall.raise_("rv-yes:a", "x")
    heimdall.raise_("rv-no:a", "x")
    heimdall.raise_("rv-unknown:a", "x")
    heimdall.resolver("rv-yes")(lambda inc: (True, "gone"))
    heimdall.resolver("rv-no")(lambda inc: (False, "still"))
    heimdall.resolver("rv-unknown")(lambda inc: None)
    changed = heimdall.reconcile()
    assert any("rv-yes" in c for c in changed) and len(changed) == 1
    assert {i["kind"] for i in heimdall.open_incidents()} >= {"rv-no", "rv-unknown"}


def test_quiet_only_without_resolver():
    heimdall.raise_("quiet-kind:a", "x")
    heimdall.raise_("rv-no:b", "x")
    for inc in heimdall.open_incidents():  # as if nothing had fired for a long time
        mr.patch("heimdall", incidents.KIND, str(inc["id"]), {"last_seen_at": 0}, at=time.time(), wait=True)
    changed = heimdall.reconcile()
    open_kinds = {i["kind"] for i in heimdall.open_incidents()}
    assert "quiet-kind" not in open_kinds and "rv-no" in open_kinds and "rv-unknown" in open_kinds, changed


def test_prune_keeps_open():
    iid = heimdall.raise_("old:a", "x")["incident"]
    heimdall.resolve(iid, heimdall.BY_HUMAN, "t")
    mr.patch("heimdall", incidents.KIND, str(iid), {"resolved_at": 1}, at=time.time(), wait=True)
    newest = heimdall.raise_("newer:a", "x")["incident"]
    heimdall.resolve(newest, heimdall.BY_HUMAN, "t")
    mr.patch("heimdall", incidents.KIND, str(newest), {"resolved_at": 1}, at=time.time(), wait=True)
    assert heimdall.prune(90) >= 1 and heimdall.get(iid) is None and heimdall.open_incidents()
    assert heimdall.get(newest) is not None  # the newest id stays so no id is handed out twice
    assert heimdall.raise_("newer:b", "x")["incident"] > newest


def test_alerts_now_only_rewrites_on_change():
    incidents.alerts_now()
    text = store.ALERTS_NOW.read_text()
    assert "Open incidents" in text and incidents.alerts_now() is False
    heimdall.raise_("brand-new:a", "fresh")
    assert "brand-new" in store.ALERTS_NOW.read_text()


def test_alert_counts_rise_and_survive_a_replay():
    for _ in range(3):
        heimdall.raise_("counted:a", "msg", "warn")
    row = alerts.get("counted:a")
    assert row["count"] == 3 and row["message"] == "msg" and row["first_seen"] <= row["last_seen"]
    assert alerts.recent(1)[0][0] == "counted:a" and alerts.count_since(0) >= 1
    assert alerts.sum_since(0, "counted:") == 3
    before = time.time() - 1
    assert [k for k, *_ in alerts.since(before)].count("counted:a") == 1


def test_mimir_down_keeps_the_alert_and_loses_nothing():
    os.environ["MIMIR_URL"] = "none"  # Mimir answers nothing
    try:
        r = heimdall.raise_("down:a", "while down")
        assert r["count"] == 1 and r["incident"] is None  # no incident without a read, but no exception
        assert mr.pending("heimdall")["pending"] == 1  # the count waits in the outbox
        for call in (heimdall.open_incidents, lambda: alerts.recent(1)):
            try:
                call()
                assert False, "a read must say Mimir is down, not return nothing"
            except mr.MimirDown:
                pass
        assert incidents.alerts_now() is False
        assert cli.main(["incidents", "--open"]) == 3
    finally:
        del os.environ["MIMIR_URL"]
    assert alerts.get("down:a")["count"] == 1  # replayed
    assert mr.pending("heimdall")["pending"] == 0


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
    ca.ACKS = _TMP / "state" / "commit-audit-acks.json"
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
    # The HUD's Acknowledge button writes ACKS instead of config.toml (audit A27).
    ca.CONFIG.write_text("")
    assert ca.main() == 0 and json.loads(ca.ARTIFACT.read_text())["flagged"]
    ca.ACKS.write_text(json.dumps({sha[:9]: {"reason": "rebuild", "at": "2026-10-24"}}))
    assert ca.main() == 0 and json.loads(ca.ARTIFACT.read_text())["flagged"] == []


def test_backup_check_does_not_call_a_run_in_progress_a_missed_run():
    import json
    from datetime import datetime, timedelta
    cb = _load_check("check-backup.py")
    stamp = lambda h: (datetime.now() - timedelta(hours=h)).strftime("%F %T")  # noqa: E731
    assert cb.run_started("2026-10-27 02:30:05 === start\n2026-10-27 02:30:38 === done, nuc a\n") is None
    assert cb.run_started("2026-10-27 02:30:05 === start\n2026-10-27 02:30:38 === done, nuc a\n2026-10-28 02:30:04 === start\nsnapshot b saved\n")
    done_at = (datetime.now().astimezone() - timedelta(hours=29)).isoformat()
    status = {"ok": True, "at": done_at, "nuc": {"ok": True}, "offsite": {"state": "not-configured"}}
    drill = f"{stamp(24 * 24)} nuc: restore drill passed\n"
    cb.STATUS_FILE, cb.BACKUP_LOG, cb.ARTIFACT = _TMP / "bk-status.json", _TMP / "bk.log", _TMP / "state" / "checks" / "backup.json"
    cb.STATUS_FILE.write_text(json.dumps(status))
    ages = lambda: [p for p in json.loads(cb.ARTIFACT.read_text())["problems"] if "last backup run" in p or "has not finished" in p]  # noqa: E731
    cb.BACKUP_LOG.write_text(drill + f"{stamp(5)} === start\n")  # started 5h ago, still going
    cb.main()
    assert ages() == []
    cb.BACKUP_LOG.write_text(drill + f"{stamp(14)} === start\n")  # stuck past MAX_RUN_HOURS
    cb.main()
    assert len(ages()) == 2
    cb.BACKUP_LOG.write_text(drill)  # nothing running, last finish 29h ago: a real miss
    cb.main()
    assert len(ages()) == 1 and "29h ago" in ages()[0]


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
    wedged.listen(1)  # accepts into the backlog, never answers: Mimir on 10-18
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    closed_port = closed.getsockname()[1]
    closed.close()
    services = {"wedged": (f"http://127.0.0.1:{wedged.getsockname()[1]}/health", "w"),
                "closed": (f"http://127.0.0.1:{closed_port}/health", "c")}
    art = _TMP / "services.json"
    art.unlink(missing_ok=True)
    try:
        first = svc.main(services, art)  # one failed run is a blip: no alert yet, but the keys are in `failing`
        doc1 = json.loads(art.read_text())
        code = svc.main(services, art)
    finally:
        wedged.close()
    doc = json.loads(art.read_text())
    assert first == 0 and doc1["alerts"] == [] and doc1["failing"] == ["service-down:wedged", "service-down:closed"]
    assert code == svc.FINDING_EXIT
    assert [a["key"] for a in doc["alerts"]] == ["service-down:wedged", "service-down:closed"] == doc["failing"]
    svc.main({}, art)  # recovered: streak and failing clear
    assert json.loads(art.read_text())["failing"] == []


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
               "-\t0\tcom.anzes.env-check\n-\t1\tsh.brew.syncthing\n-\t1\tsh.brew.other\n-\t1\tcom.apple.x\n")
    rows = lc.parse(listing, {"com.anzes.helper", "sh.brew.syncthing"})
    assert [r["label"] for r in rows] == ["com.anzes.helper", "com.anzes.mac-backup", "com.anzes.env-check", "sh.brew.syncthing"]
    assert [r["label"] for r in rows if r["failing"]] == ["com.anzes.mac-backup", "sh.brew.syncthing"]


def test_config_drift_exempts_only_the_listed_personal_only_hook():
    cd = _load_check("config-drift.py")
    def hooks(*cmds):
        return {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": c} for c in cmds]}]}}
    rules = 'python3 "$HOME/.claude-personal/hooks/workers-rules.py"'
    assert cd.drift({"personal": hooks(rules), "work": hooks()})["work"]["hooks"] == []
    other = cd.drift({"personal": hooks(rules, "other.py"), "work": hooks()})["work"]["hooks"]
    assert [json.loads(e[2])["command"] for e in other] == ["other.py"]


def test_services_cover_the_localhost_daemons():
    svc = _load_check("check-services.py")
    assert {"ntfy", "crowdsec", "syncthing"} <= set(svc.SERVICES)


def test_dev_servers_declared_tunnel_is_info_but_a_second_process_is_still_a_clash():
    ds = _load_check("check-dev-servers.py")
    declared = {8092: "nuc"}
    ssh = {"pid": 1, "command": "ssh", "address": "127.0.0.1", "port": 8092}
    node = {"pid": 2, "command": "node", "address": "*", "port": 8092}
    assert ds.is_tunnel([ssh], 1, declared) and not ds.is_tunnel([node], 2, declared)
    assert not ds.is_tunnel([ssh, {**ssh, "port": 9999}], 1, declared)  # one undeclared port breaks it
    assert ds.conflicts([ssh], declared) == []
    clash = ds.conflicts([ssh, node], declared)
    assert len(clash) == 1 and "declared tunnel: nuc" in clash[0]


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("PASS", name)
    print(f"{n} passed")
