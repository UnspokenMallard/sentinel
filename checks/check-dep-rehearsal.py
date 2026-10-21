#!/Users/anzes/.jarvis/.venv/bin/python
"""Weekly dependency-update rehearsal for er-web and er-api (asked for by Anze 2026-10-03).

Answers "which dependency updates are safe to take this week?" without touching the
repositories. Each repo's `development` commit is exported with `git archive` into
~/Library/Caches/jarvis/dep-rehearsal (no worktree, no branch, nothing written to the
repo), updates are tried there, and the result is a report plus a patch that Anze
applies himself. Nothing is committed to, pushed from or installed into the repos.

Per repo:
  1. Baseline: `npm ci` and the checks on the untouched commit. A red baseline is
     reported and the repo is skipped - a rehearsal against a red tree proves nothing.
  2. Candidates: every direct dependency with a plain ^/~/exact semver spec. Target =
     the newest stable version in the same major (same minor for 0.x) published at
     least MIN_AGE_DAYS ago. Newer majors are listed, never tried.
  3. All candidate groups are tried together; if that is red, the set is split in
     halves until the breaking groups are found (at most MAX_TRIALS installs per repo).
     The safe remainder is then verified together once more.
  4. A patch of package.json + package-lock.json for the verified safe set.

Safety: installs run with --ignore-scripts, so no package install hook executes on this
machine, and the minimum release age keeps a freshly published (possibly compromised)
version out of the run.

A Sentinel check since 2026-10-18 (Jarvis keeps the clock): launchd com.jarvis.deprehearsal
(Mondays 01:00; see ~/.jarvis/install-launchd.sh) runs `sentinel-job.sh check dep-rehearsal`, and the
runner stamps the artifact state/checks/dep-rehearsal.json when it ends. Reports and the log stay in
~/.jarvis (reports/dep-rehearsal, logs/dep-rehearsal.log: report-push-deps and log-signatures read them).
Runs from launchd, not as a daemon job: a run takes far longer than [heartbeat] worker_max_staleness_s, and the
worker runs one handler at a time. The daemon job report-push-deps sends the summary to
the phone at 07:35 and refuses one older than its max_age_s - that is how a run that never
finished becomes visible. Verification is by artifact: the summary always says what ran.

Usage: check-dep-rehearsal.py [--repo er-web] [--baseline-only]
       check-dep-rehearsal.py --check   (daemon job dep-rehearsal-check: exit 1 if the last run errored;
                                         the handler's artifact_max_age_s catches a run that never finished.
                                         Read-only: sentinel's runner does not restamp the artifact for it)
"""
from __future__ import annotations
import sys as _sys
if _sys.version_info < (3, 11):  # /usr/bin/python3 is 3.9 (no tomllib); the daemon runs .venv (3.13)
    raise SystemExit(f"{__file__}: needs Python 3.11+, got {_sys.version.split()[0]}; "
                     "run it with ~/.jarvis/.venv/bin/python")

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

FINDING_EXIT = 87  # a check ran and found a problem; jarvis daemon/core.py FINDING_EXIT

HOME = Path.home()
CODE = HOME / "code" / "eventregistry"
# Checks are build + unit tests only (Anze, 2026-10-03): Cypress needs the dev server and
# mock backend up, which is more moving parts than a weekly unattended run should have.
# `rebuild` names the only packages whose install scripts run (npm rebuild, after every install):
# sharp 0.32 downloads its native binary in one and fails to load without it.
REPOS = {
    "er-web": {"ref": "refs/heads/development", "build": [["npm", "run", "build"], ["npm", "run", "build:server-express"]],
               "rebuild": []},
    "er-api": {"ref": "refs/heads/development", "build": [["npm", "run", "compile:server"]], "rebuild": ["sharp"]},
}
# Tests already failing on the baseline are not held against an update: a trial is red only
# when it fails a test the baseline passed, or passes fewer tests than the baseline.
JEST_OUT = ".rehearsal-jest.json"
JEST = ["npx", "jest", "--ci", "--json", f"--outputFile={JEST_OUT}"]
WORK = HOME / "Library" / "Caches" / "jarvis" / "dep-rehearsal"  # outside mac-backup's sources
OUT = HOME / ".jarvis" / "reports" / "dep-rehearsal"
SUMMARY = OUT / "latest.md"
ARTIFACT = HOME / ".sentinel" / "state" / "checks" / "dep-rehearsal.json"
LOG = HOME / ".jarvis" / "logs" / "dep-rehearsal.log"
MIN_AGE_DAYS = 7
MAX_TRIALS = 40  # a trial is ~15s on the Mac Studio; 12 left 38 of er-web's groups unclassified
CHECK_TIMEOUT_S = 1800
# Packages that must move in lockstep share a group. Otherwise a scope is one group, and
# every @types/* package and every unscoped package is its own.
FAMILIES = {"@angular": "angular", "@angular-devkit": "angular", "@ngtools": "angular", "@schematics": "angular"}
SPEC = re.compile(r"^([~^]?)(\d+\.\d+\.\d+)$")
STABLE = re.compile(r"^\d+\.\d+\.\d+$")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class OutOfTrials(Exception):
    pass


def log(text: str) -> None:
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")


def tail(text: str, lines: int = 40) -> str:
    return "\n".join(ANSI.sub("", text).rstrip().splitlines()[-lines:])


def node_env() -> dict:
    """launchd's PATH has no node (it comes from mise), so resolve it once."""
    env = dict(os.environ)
    node = None
    try:
        p = subprocess.run(["/opt/homebrew/bin/mise", "which", "node"], capture_output=True, text=True,
                           timeout=60, cwd=HOME)
        node = p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    node = node or shutil.which("node")
    if not node:
        raise RuntimeError("node not found: `mise which node` failed and node is not on PATH")
    env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
    env.update(CI="1", HUSKY="0", CYPRESS_INSTALL_BINARY="0", npm_config_fund="false",
               npm_config_audit="false", npm_config_update_notifier="false")
    return env


def run(cmd: list[str], cwd: Path, env: dict, timeout: int = CHECK_TIMEOUT_S) -> tuple[int, str, float]:
    start = time.monotonic()
    log(f"$ {' '.join(cmd)}")
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = 124, f"timed out after {timeout}s"
    secs = time.monotonic() - start
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(ANSI.sub("", out)[-20000:] + "\n")
    log(f"  exit {rc} in {secs:.0f}s")
    return rc, out, secs


def prepare(name: str, ref: str) -> tuple[Path, str]:
    """Export `ref` into a fresh scratch dir, made a throwaway git repo so package changes can be reset and diffed."""
    src, work = CODE / name, WORK / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    # full refs/heads path: a bare `development` is ambiguous with the `development` remote
    probe = subprocess.run(["git", "-C", str(src), "rev-parse", "--short", "--verify", ref], capture_output=True, text=True)
    if probe.returncode != 0:
        raise RuntimeError(f"{name}: ref `{ref}` not found in {src} - create or fetch that branch, or fix REPOS in {__file__}")
    sha = probe.stdout.strip()
    archive = subprocess.Popen(["git", "-C", str(src), "archive", "--format=tar", ref], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", str(work)], stdin=archive.stdout, check=True)
    if archive.wait() != 0:
        raise RuntimeError(f"git archive {ref} failed in {src}")
    ident = ["-c", "user.name=dep-rehearsal", "-c", "user.email=dep-rehearsal@localhost", "-c", "commit.gpgsign=false"]
    for args in (["init", "-q"], ["add", "-A"], [*ident, "commit", "-q", "-m", "baseline"]):
        subprocess.run(["git", *args], cwd=work, check=True, capture_output=True)
    return work, sha


def install(work: Path, cfg: dict, env: dict, cmd: list[str]) -> tuple[bool, str, str]:
    """(ok, failed command, output tail) for an install plus the allowlisted rebuilds."""
    for c in [cmd] + [["npm", "rebuild", pkg] for pkg in cfg["rebuild"]]:
        rc, out, _ = run(c, work, env)
        if rc != 0:
            return False, " ".join(c), tail(out)
    return True, "", ""


def jest_results(work: Path) -> tuple[int, dict[str, str]] | None:
    """(tests passed, {failing test or suite: first lines of its message}) from jest's --json file."""
    try:
        data = json.loads((work / JEST_OUT).read_text())
    except (OSError, ValueError):
        return None
    failures: dict[str, str] = {}
    for suite in data.get("testResults", []):
        path = os.path.relpath(suite.get("name") or suite.get("testFilePath") or "?", work)
        asserts = suite.get("assertionResults") or suite.get("testResults") or []
        bad = [a for a in asserts if a.get("status") == "failed"]
        for a in bad:
            title = a.get("fullName") or " ".join([*a.get("ancestorTitles", []), a.get("title", "")])
            failures[f"{path} > {title}"] = "\n".join(a.get("failureMessages") or [])
        if not bad and (suite.get("status") == "failed" or suite.get("testExecError")):
            failures[f"{path} (suite failed to run)"] = suite.get("message") or str(suite.get("testExecError"))
    return int(data.get("numPassedTests", 0)), failures


def run_checks(work: Path, cfg: dict, env: dict, baseline: dict | None = None) -> dict:
    """Build commands must exit 0; jest is judged against the baseline (None = this is the baseline)."""
    total = 0.0
    for cmd in cfg["build"]:
        rc, out, secs = run(cmd, work, env)
        total += secs
        if rc != 0:
            return {"ok": False, "failed": " ".join(cmd), "tail": tail(out), "seconds": total}
    (work / JEST_OUT).unlink(missing_ok=True)
    rc, out, secs = run(JEST, work, env)
    total += secs
    parsed = jest_results(work)
    if parsed is None:
        return {"ok": False, "failed": "npx jest --ci (no results file)", "tail": tail(out), "seconds": total}
    passed, failures = parsed
    res = {"ok": True, "seconds": total, "tests": passed, "failures": sorted(failures)}
    if baseline is None:
        return res
    new = [f for f in sorted(failures) if f not in set(baseline.get("failures", []))]
    if new:
        # One retry of just the files with new failures: er-api's process-lifecycle tests are timing
        # dependent, and one flaky test must not sink a whole update set.
        files = sorted({f.split(" > ")[0].removesuffix(" (suite failed to run)") for f in new})
        (work / JEST_OUT).unlink(missing_ok=True)
        _, _, secs = run([*JEST, *files], work, env)
        total += secs
        retry = jest_results(work)
        still = [f for f in new if retry is None or f in retry[1]]
        if not still:
            log(f"  flaky, passed on retry: {'; '.join(new)}")
            passed += len(new)
            res = {**res, "tests": passed, "flaky": new}
        new = still
    if new:
        detail = "\n".join(new[:15]) + "\n\n" + tail(ANSI.sub("", failures[new[0]]), 25)
        return {**res, "ok": False, "failed": f"npx jest --ci ({len(new)} new failure(s))", "tail": detail}
    if passed < baseline.get("tests", 0):
        return {**res, "ok": False, "failed": "npx jest --ci",
                "tail": f"only {passed} tests passed; the baseline passed {baseline['tests']}"}
    return res


def audit(work: Path, env: dict) -> dict | None:
    p = subprocess.run(["npm", "audit", "--json"], cwd=work, env=env, capture_output=True, text=True, timeout=300)
    try:
        return json.loads(p.stdout)["metadata"]["vulnerabilities"]
    except (ValueError, KeyError):
        return None


def published(name: str, work: Path, env: dict) -> dict[str, datetime] | None:
    try:
        p = subprocess.run(["npm", "view", name, "time", "--json"], cwd=work, env=env, capture_output=True,
                           text=True, timeout=180)
        times = json.loads(p.stdout)
    except (ValueError, subprocess.TimeoutExpired):
        return None
    return {v: datetime.fromisoformat(t.replace("Z", "+00:00")) for v, t in times.items() if STABLE.match(v)}


def vt(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def pick(current: str, times: dict[str, datetime], cutoff: datetime) -> tuple[str | None, str | None]:
    """(newest same-major target, newest newer-major version), both at least MIN_AGE_DAYS old."""
    cur = vt(current)
    newer = sorted((vt(v) for v, t in times.items() if t <= cutoff and vt(v) > cur), reverse=True)
    same = [v for v in newer if v[0] == cur[0] and (cur[0] > 0 or v[1] == cur[1])]
    major = [v for v in newer if v not in same]
    fmt = lambda v: ".".join(map(str, v))  # noqa: E731
    return (fmt(same[0]) if same else None), (fmt(major[0]) if major else None)


def group_key(name: str) -> str:
    if not name.startswith("@") or name.startswith("@types/"):
        return name
    scope = name.split("/")[0]
    return FAMILIES.get(scope, scope)


def candidates(work: Path, env: dict) -> tuple[list[dict], list[dict], list[str]]:
    pkg = json.loads((work / "package.json").read_text())
    lock = json.loads((work / "package-lock.json").read_text()).get("packages", {})
    deps, skipped = [], []
    for section in ("dependencies", "devDependencies"):
        for name, spec in pkg.get(section, {}).items():
            m = SPEC.match(spec)
            current = lock.get(f"node_modules/{name}", {}).get("version")
            if not m or not current or not STABLE.match(current):
                skipped.append(f"{name}@{spec}")
                continue
            deps.append({"name": name, "section": section, "prefix": m.group(1), "current": current})
    with ThreadPoolExecutor(8) as pool:
        times = list(pool.map(lambda d: published(d["name"], work, env), deps))
    cutoff = datetime.now(timezone.utc) - timedelta(days=MIN_AGE_DAYS)
    cands, majors = [], []
    for d, t in zip(deps, times):
        if t is None:
            skipped.append(f"{d['name']} (registry lookup failed)")
            continue
        target, major = pick(d["current"], t, cutoff)
        if target:
            cands.append({**d, "target": target, "group": group_key(d["name"])})
        if major:
            majors.append({**d, "latest": major})
    return cands, majors, skipped


def write_package_json(work: Path, cands: list[dict]) -> None:
    """Rewrite the specs, keeping the file's indent and line endings so the patch shows only real changes."""
    path = work / "package.json"
    text = path.read_bytes().decode("utf-8")
    m = re.search(r"^([ \t]+)\"", text, re.M)
    pkg = json.loads(text)
    for c in cands:
        pkg[c["section"]][c["name"]] = c["prefix"] + c["target"]
    indent = m.group(1) if m else "  "
    out = json.dumps(pkg, indent=indent, ensure_ascii=False) + ("\n" if text.rstrip("\r").endswith("\n") else "")
    path.write_bytes(out.replace("\n", "\r\n").encode() if "\r\n" in text else out.encode())


def keep_line_endings(work: Path, baseline_crlf: dict[str, bool]) -> None:
    """npm writes LF; er-web's files are CRLF. Convert back so the whole lockfile does not show as changed."""
    for name, crlf in baseline_crlf.items():
        path = work / name
        data = path.read_bytes()
        if crlf and b"\r\n" not in data:
            path.write_bytes(data.replace(b"\n", b"\r\n"))


def rehearse(name: str, work: Path, cfg: dict, env: dict, baseline: dict, groups: dict[str, list[dict]]) -> dict:
    keys = sorted(groups)
    trials = {"n": 0}
    crlf = {n: b"\r\n" in (work / n).read_bytes() for n in ("package.json", "package-lock.json")}
    proven_ok: set[str] = set()
    breaking: list[dict] = []
    excluded: set[str] = set()

    def trial(ks: list[str], final: bool = False) -> dict:
        if trials["n"] >= MAX_TRIALS and not final:  # the final verification always gets its trial
            raise OutOfTrials
        trials["n"] += 1
        log(f"{name}: trial {trials['n']} with {len(ks)} group(s): {', '.join(ks)}")
        subprocess.run(["git", "checkout", "-q", "--", "package.json", "package-lock.json"], cwd=work, check=True)
        write_package_json(work, [c for k in ks for c in groups[k]])
        ok, failed, out = install(work, cfg, env, ["npm", "install", "--ignore-scripts"])
        keep_line_endings(work, crlf)
        if not ok:
            return {"ok": False, "failed": failed, "tail": out}
        res = run_checks(work, cfg, env, baseline)
        if res["ok"]:
            proven_ok.update(ks)
        return res

    def bisect(ks: list[str], known: dict | None = None) -> None:
        res = known or trial(ks)
        if res["ok"]:
            return
        if len(ks) == 1:
            breaking.append({"groups": ks, "failed": res["failed"], "tail": res["tail"]})
            excluded.update(ks)
            return
        mid = len(ks) // 2
        before = len(breaking)
        bisect(ks[:mid])
        bisect(ks[mid:])
        if len(breaking) == before:  # each half passes alone, together they fail
            breaking.append({"groups": ks[mid:], "failed": res["failed"], "tail": res["tail"],
                             "together_with": ks[:mid]})
            excluded.update(ks[mid:])

    result = {"trials": 0, "breaking": breaking, "unclassified": [], "safe": [], "verified": False}
    first: dict | None = None
    try:
        first = trial(keys)
        if first["ok"]:
            result.update(safe=keys, verified=True)
        else:
            bisect(keys, first)
    except OutOfTrials:
        pass
    if not result["verified"]:
        safe = [k for k in keys if k in proven_ok and k not in excluded]
        result["unclassified"] = [k for k in keys if k not in proven_ok and k not in excluded]
        result["safe"] = safe
        if safe:
            res = trial(safe, final=True)
            result["verified"] = res["ok"]
            if not res["ok"]:
                result["verify_failure"] = {"failed": res["failed"], "tail": res["tail"]}
    result["trials"] = trials["n"]
    return result


def fmt_secs(s: float) -> str:
    return f"{int(s // 60)}m{int(s % 60):02d}s"


def fmt_audit(a: dict | None) -> str:
    if not a:
        return "audit unavailable"
    return ", ".join(f"{a.get(k, 0)} {k}" for k in ("critical", "high", "moderate"))


def do_repo(name: str, cfg: dict, env: dict, outdir: Path, baseline_only: bool) -> dict:
    work, sha = prepare(name, cfg["ref"])
    rec: dict = {"repo": name, "ref": cfg["ref"], "sha": sha}
    ok, failed, out = install(work, cfg, env, ["npm", "ci", "--ignore-scripts"])
    if not ok:
        return {**rec, "baseline": {"ok": False, "failed": failed, "tail": out}}
    rec["baseline"] = run_checks(work, cfg, env)
    rec["audit"] = audit(work, env)
    if not rec["baseline"]["ok"] or baseline_only:
        return rec
    cands, majors, skipped = candidates(work, env)
    rec.update(candidates=cands, majors=majors, skipped=skipped)
    if not cands:
        return rec
    groups: dict[str, list[dict]] = {}
    for c in cands:
        groups.setdefault(c["group"], []).append(c)
    rec["rehearsal"] = r = rehearse(name, work, cfg, env, rec["baseline"], groups)
    if r["verified"] and r["safe"]:
        # The work dir holds the state of the last green trial, which is the verified safe set.
        # Bytes, not text: text mode turns CRLF into LF and the patch stops matching er-web's CRLF files.
        diff = subprocess.run(["git", "diff", "--", "package.json", "package-lock.json"], cwd=work,
                              capture_output=True, check=True).stdout
        patch = outdir / f"{name}.patch"
        patch.write_bytes(diff)
        rec["patch"] = str(patch)
    rec["groups"] = groups
    rec["safe_count"] = len([c for k in r["safe"] for c in groups[k]]) if r["verified"] else 0
    return rec


def repo_report(rec: dict) -> tuple[str, str]:
    """(full markdown section, one-line phone summary)."""
    name, b = rec["repo"], rec.get("baseline", {})
    head = f"## {name} - {rec['ref']} @ {rec['sha']}\n\n"
    if not b.get("ok"):
        body = (f"Baseline is RED: `{b.get('failed')}` failed on the untouched commit, so nothing was tried.\n\n"
                f"```text\n{b.get('tail', '')}\n```\n")
        return head + body, f"{name}: baseline RED ({b.get('failed')}) - nothing tried, fix the tree first."
    lines = [f"Baseline green: checks {fmt_secs(b['seconds'])}, jest {b.get('tests')} tests passed. "
             f"Audit: {fmt_audit(rec.get('audit'))}.", ""]
    if b.get("failures"):
        lines += [f"Already failing on `{rec['ref']}` (ignored when judging updates): {len(b['failures'])}", ""]
        lines += [f"- {f}" for f in b["failures"][:15]]
        if len(b["failures"]) > 15:
            lines.append(f"- ... and {len(b['failures']) - 15} more (see {LOG.name})")
        lines.append("")
    r, groups = rec.get("rehearsal"), rec.get("groups", {})
    majors = rec.get("majors", [])
    phone = [name + ":"]
    if "candidates" not in rec:
        return head + "\n".join(lines) + "\n", f"{name}: baseline green (baseline-only run)."
    if not r:
        lines.append("No in-major updates older than "
                     f"{MIN_AGE_DAYS} days.")
        phone.append("nothing to update in-major.")
    else:
        safe = [c for k in r["safe"] for c in groups[k]]
        if safe and r["verified"]:
            lines += [f"### Safe to take: {len(safe)} package(s), verified together", "",
                      f"Apply on a clean `{rec['ref']}`: `git -C ~/code/eventregistry/{name} apply {rec['patch']}"
                      " && npm install`", "", "| package | from | to |", "|---|---|---|"]
            lines += [f"| {c['name']} | {c['current']} | {c['target']} |" for c in safe]
            lines.append("")
            phone.append(f"{len(safe)} safe (patch ready)")
        elif safe:
            vf = r.get("verify_failure", {})
            lines += ["### Safe set did not verify",
                      f"Each group passed in some trial, but together `{vf.get('failed')}` failed. No patch.", "",
                      f"```text\n{vf.get('tail', '')}\n```", ""]
            phone.append("safe set failed to verify, no patch")
        else:
            phone.append("nothing safe")
        if r["breaking"]:
            lines += ["### Breaks", ""]
            for br in r["breaking"]:
                pk = ", ".join(f"{c['name']} {c['current']} -> {c['target']}" for k in br["groups"] for c in groups[k])
                extra = f" (only together with {', '.join(br['together_with'])})" if br.get("together_with") else ""
                lines += [f"**{', '.join(br['groups'])}**{extra}: `{br['failed']}` failed. {pk}", "",
                          f"```text\n{br['tail']}\n```", ""]
            phone.append(f"{len(r['breaking'])} break ("
                         + "; ".join(f"{', '.join(br['groups'])}: {br['failed']}" for br in r["breaking"]) + ")")
        if r["unclassified"]:
            lines += [f"### Not classified ({MAX_TRIALS}-trial budget ran out)", "",
                      ", ".join(r["unclassified"]), ""]
            phone.append(f"{len(r['unclassified'])} unclassified")
        lines.append(f"Trials: {r['trials']}.")
        lines.append("")
    if majors:
        lines += ["### Newer major versions (listed, not tried)", ""]
        # A major far beyond anything the package has had (prismjs 1.30.0 -> 9000.0.2) is a squat or a
        # joke release, not an upgrade path.
        lines += [f"- {m['name']} {m['current']} -> {m['latest']}"
                  + (" **(unusual version number - check the package before trusting it)**"
                     if vt(m['latest'])[0] >= 100 > vt(m['current'])[0] else "") for m in majors]
        lines.append("")
        phone.append(f"{len(majors)} majors waiting")
    if rec.get("skipped"):
        lines += ["Skipped (spec is not a plain ^/~/exact version): " + ", ".join(rec["skipped"]), ""]
    phone.append(f"audit {fmt_audit(rec.get('audit'))}")
    return head + "\n".join(lines) + "\n", " ".join(phone[:1]) + " " + ", ".join(phone[1:]) + "."


def check() -> int:
    try:
        last = json.loads(ARTIFACT.read_text())
    except (OSError, ValueError) as exc:
        print(f"no readable rehearsal artifact at {ARTIFACT}: {exc}", file=sys.stderr)
        return 1
    if last.get("outcome") != "ran":
        print(f"dep rehearsal of {last.get('ran_at')} failed: {last.get('error')} - see {LOG}", file=sys.stderr)
        return FINDING_EXIT
    print(f"dep rehearsal of {last.get('ran_at')} ran: " + "; ".join(
        f"{k} baseline {'green' if v['baseline_ok'] else 'RED'}, {v['safe_verified']} safe, {v['breaking']} breaking"
        for k, v in last.get("repos", {}).items()))
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--check" in args:
        return check()
    only = args[args.index("--repo") + 1] if "--repo" in args else None
    baseline_only = "--baseline-only" in args
    for d in (WORK, OUT, LOG.parent, ARTIFACT.parent):
        d.mkdir(parents=True, exist_ok=True)
    lock = open(WORK / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another dep rehearsal is running", file=sys.stderr)
        return 1
    LOG.write_text("")
    started = datetime.now().astimezone()
    outdir = OUT / f"{started:%Y-%m-%d}"
    outdir.mkdir(exist_ok=True)
    sections, phone, recs, error = [], [], [], None
    try:
        env = node_env()
        for name, cfg in REPOS.items():
            if only and name != only:
                continue
            log(f"=== {name}")
            try:
                rec = do_repo(name, cfg, env, outdir, baseline_only)
            except Exception as exc:  # one repo failing must not hide the other's result
                log(traceback.format_exc())
                error = error or f"{name}: {type(exc).__name__}: {exc}"
                rec = {"repo": name, "ref": cfg["ref"], "sha": "?", "baseline": {"ok": False, "failed":
                       f"rehearsal crashed: {type(exc).__name__}: {exc}", "tail": tail(traceback.format_exc())}}
            recs.append(rec)
            full, line = repo_report(rec)
            sections.append(full)
            phone.append(line)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log(traceback.format_exc())
    finished = datetime.now().astimezone()
    report = outdir / "report.md"
    report.write_text(f"# Dependency rehearsal, {started:%Y-%m-%d}\n\n"
                      f"Ran {started:%H:%M}-{finished:%H:%M}. Versions newer than {MIN_AGE_DAYS} days are ignored; "
                      "installs ran with --ignore-scripts. Log: ~/.jarvis/logs/dep-rehearsal.log\n\n"
                      + (f"**The run failed: {error}**\n\n" if error else "") + "\n".join(sections))
    SUMMARY.write_text(f"Dependency rehearsal {started:%Y-%m-%d}\n\n"
                       + (f"RUN FAILED: {error}\n\n" if error else "")
                       + "\n".join(phone) + f"\n\nFull report: {report}\n")
    ARTIFACT.write_text(json.dumps({
        "ran_at": started.isoformat(timespec="seconds"), "finished_at": finished.isoformat(timespec="seconds"),
        "outcome": "error" if error else "ran", "error": error, "report": str(report),
        "repos": {r["repo"]: {"sha": r["sha"], "baseline_ok": r["baseline"].get("ok"),
                              "safe_verified": r.get("safe_count", 0),
                              "breaking": len(r.get("rehearsal", {}).get("breaking", [])),
                              "majors": len(r.get("majors", [])), "patch": r.get("patch")} for r in recs},
    }, indent=2))
    print(SUMMARY.read_text())
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
