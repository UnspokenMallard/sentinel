# Next prompt: Sentinel

DONE 2026-10-17: Sentinel is built and live (`~/.sentinel/DESIGN.md`, "As built"). What is left is a
check of the first nights and cleanup that waits on Anze. Herald is next (`~/.herald/NEXT-PROMPT.md`).

## When to fire this

Earliest-useful dates, not deadlines; being late costs nothing.

| When | Step | Waits on |
|---|---|---|
| DONE 2026-10-18 08:07 | `sentinel checks` shows every check `ran_at` within its schedule; no `handler-failed` for the 14 moved jobs (nuc 5m, builds 30m, hourly: paths disk endpoints stalled dev-servers retired config-drift host-cpu-load, nightly: backup repo-versions expiry, weekly Mon 06:45: log-signatures); `~/.jarvis/ALERTS-NOW.md` lists only open incidents. Clean for the 14; `log-signatures` first runs 2026-10-19 06:45 | the first night |
| DONE 2026-10-18 | Dropped `alerts` and `incidents` from `~/.jarvis/jarvis.db` on Anze's yes. `jarvis.db.bak-sentinel` was already gone | Anze's confirmation |
| ~~with Herald's build~~ DONE 2026-10-17 | `core.notify`'s push is Herald's desktop toast (`POST /toast`, `herald/toast.py`); the cooldown, backoff, postponing and `alerts.log` stay in Jarvis because they decide whether to say it (see `~/.herald/DESIGN.md` "As built") | Herald |
| DONE 2026-10-18 (mostly) | `backup-drill-sample` moved to `checks/`; `mac-backup` repointed. `local-llm-perf` runs through `sentinel-job.sh` via a wrapper, its script and artifact stay in Gateway. `alert-review` already runs under Analyst. `health-check.py` stays outside the daemon (Anze's decision). The three artifact resolvers stay in Jarvis (decided: Jarvis registers them) | none |

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
