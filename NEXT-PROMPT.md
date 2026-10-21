# Next prompt: Sentinel

DONE 2026-10-17: Sentinel is built and live (`~/.sentinel/DESIGN.md`, "As built"). What is left is a
check of the first week, run by the Jarvis job `sentinel-first-week`. Herald is built (row below).

## When to fire this

Earliest-useful dates, not deadlines; being late costs nothing.

| When | Step | Waits on |
|---|---|---|
| DONE 2026-10-18 08:07 | `sentinel checks` shows every check `ran_at` within its schedule; no `handler-failed` for the 14 moved jobs (nuc 5m, builds 30m, hourly: paths disk endpoints stalled dev-servers retired config-drift host-cpu-load, nightly: backup repo-versions expiry, weekly Mon 06:45: log-signatures); `~/.jarvis/ALERTS-NOW.md` lists only open incidents. Clean for the 14; `log-signatures` first runs 2026-10-19 06:45 | the first night |
| 2026-10-19 07:30 (Jarvis job `sentinel-first-week`, automatic) | `log-signatures` ran on schedule via `sentinel-job.sh`; every `sentinel-job.sh` job last ran clean; no open `handler-failed` for them. Result in `~/.jarvis/state/sentinel-first-week.md`; a FAIL is one warn alert. On PASS, remove the job row from `~/.jarvis/config.toml` and the script | log-signatures' first Monday run (06:45) |
| DONE 2026-10-18 | Dropped `alerts` and `incidents` from `~/.jarvis/jarvis.db` on Anze's yes. `jarvis.db.bak-sentinel` was already gone | Anze's confirmation |
| ~~with Herald's build~~ DONE 2026-10-17 | `core.notify`'s push is Herald's desktop toast (`POST /toast`, `herald/toast.py`); the cooldown, backoff, postponing and `alerts.log` stay in Jarvis because they decide whether to say it (see `~/.herald/DESIGN.md` "As built") | Herald |
| DONE 2026-10-18 (mostly) | `backup-drill-sample` moved to `checks/`; `mac-backup` repointed. `local-llm-perf` runs through `sentinel-job.sh` via a wrapper, its script and artifact stay in Gateway. `alert-review` already runs under Analyst. `health-check.py` stays outside the daemon (Anze's decision). The three artifact resolvers stay in Jarvis (decided: Jarvis registers them) | none |

## Audit 2026-10-21: your items

From the Jarvis audit (HUD task audit-jarvis-and-all-its-com-e5a9). The evidence, effort, risk and
done-criteria of each item are in `~/.jarvis/AUDIT-2026-10-21.md`; the phases and Anze's decisions
are in `~/.jarvis/NEXT-PROMPT.md`. Build an item only after Anze approves its phase. Prompt A below
covers these rows too. The rule stays: this component measures or knows; Jarvis alerts and acts.

- **A01 (Phase 0)** A port-level service check: connect to every service port (`/health`, not `/`)
  and report `service-down:<name>`. Mind's listener was dead for 3 days and nothing here saw it.
- **A03 (Phase 0)** A launchd check: last exit status of every `com.jarvis.*`, `com.mind.*` and
  component agent, and the age of its log against its schedule. `com.jarvis.healthcheck` sat at
  exit 1 for 2 days unseen.
- **A06 (Phase 0)** config-drift is right: the work config lacks the `trigger-recall` hook. Anze
  decides both or personal-only (D2). If personal-only, the check needs an allowlist entry.
- **A07 (Phase 0)** A check that finds something must not look like a crash: today it exits 1,
  and Jarvis retries it 3 times, burn-alerts and remediates it. 74% of alerts are such meta-alerts.
  Agree the contract with Jarvis (a distinct exit code or a result file) before changing either.
- **A24 (Phase 2)** Stalled sessions (one sat idle 168.5 h) should become a HUD finding through
  Jarvis, not only a detection.
- **A27 (Phase 2)** commit-audit flags deletions that are part of a rebuild; Anze needs an
  Acknowledge path that is not a config edit.
- **A38 (Phase 3)** dep-rehearsal and commit-audit are designated to move here from Analyst.
- **A44 (Phase 4)** dep-rehearsal fails on "refname 'development' is ambiguous": use
  `refs/heads/development`.

| When (earliest useful, not a deadline) | Step | Waits on |
|---|---|---|
| with Phase 0 | A07 contract with Jarvis first, then the A01 and A03 checks | Phase 0 approval |
| 2026-10-26 (after the Monday run) | A44: did dep-rehearsal fail again | the weekly run |
| with Phase 2 | A24, A27 | Phase 2 approval |

## A: advance the work

> Read `~/.sentinel/DESIGN.md` and this file. Do the first row whose date has passed and report.
> Rules: Sentinel observes and records; it never alerts, pings, postpones or holds work (Jarvis
> does). An incident resolves on evidence, never on silence ("cannot tell" keeps it open). Never
> resolve an incident to make a gate go green. A check never calls `core.notify`: it writes
> `alerts` in its artifact and the job delivers. Never drop a table or delete a backup without
> Anze's confirmation. Stop and tell me if anything differs from what this file expects.

## B: status only

> Run `~/.sentinel/bin/sentinel incidents --open`, `~/.sentinel/bin/sentinel checks` and
> `~/.jarvis/.venv/bin/python ~/.sentinel/tests/test_sentinel.py`. Change nothing.

## C: something is broken

> Sentinel or an alert path broke. First, do not delete `state/sentinel.db`, a backup or an
> artifact: they are the evidence. Copy the db with `sqlite3 .backup`, run
> `~/.jarvis/.venv/bin/python ~/.jarvis/status.py` and the Jarvis selftest, read
> `~/.jarvis/logs/jarvis.log`, and report before fixing. `git -C ~/.jarvis log` shows the two cutover
> commits ("Move the alert record and incident store into Sentinel", "Move the check scripts into
> Sentinel ..."); the old tables and `jarvis.db.bak-sentinel` hold the pre-cutover rows.
