# Next prompt: Sentinel

DONE 2026-10-17: Sentinel is built and live (`~/.sentinel/DESIGN.md`, "As built"). What is left is a
check of the first nights and cleanup that waits on Anze. Herald is next (`~/.herald/NEXT-PROMPT.md`).

## When to fire this

Earliest-useful dates, not deadlines; being late costs nothing.

| When | Step | Waits on |
|---|---|---|
| 2026-10-18 08:00 | `sentinel checks` shows every check `ran_at` within its schedule; no `handler-failed` for the 14 moved jobs (nuc 5m, builds 30m, hourly: paths disk endpoints stalled dev-servers retired config-drift host-cpu-load, nightly: backup repo-versions expiry log-signatures); `~/.jarvis/ALERTS-NOW.md` lists only open incidents | the first night |
| after that check, Anze | drop `alerts` and `incidents` from `~/.jarvis/jarvis.db` and delete `~/.jarvis/jarvis.db.bak-sentinel` | Anze's confirmation |
| with Herald's build | `core.notify`'s delivery half (cooldown, push, `_push`, `ping.py`) goes behind Herald | Herald |
| optional, later | move `health-check.py` (a Jarvis job instead of `com.jarvis.healthcheck`), the three artifact resolvers, and the four checks left in Jarvis (see DESIGN "As built") | Anze's call |

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
