# Sentinel

What is true about the machines right now: every health check, the alert and incident store
with its lifecycle, and the evidence behind each. Split out of Jarvis on 2026-10-17 so Jarvis is
an orchestrator.

## Decisions (Anze, 2026-10-17)

| Question | Decision |
|---|---|
| Home | `~/.sentinel`, own git repo, code and `state/` together, own venv |
| Schedule | Jarvis triggers every check; Sentinel has no timers (`com.jarvis.healthcheck` becomes a Jarvis job) |
| Process | CLI and library, no service; state in `state/sentinel.db` and `state/checks/*.json` |
| Sessions | Built in its own session after Budget, from this design |

## Boundary

Sentinel **observes and records**: runs a check, raises or resolves an incident with its
evidence, keeps the lifecycle (open, resolved, recurring). It never pings, posts, postpones,
starts sessions or files HUD findings. Jarvis reads open incidents and acts: ping through
Herald, postpone (`postpone.py` stays in Jarvis), diagnose sessions in the HUD, gates.

## What moves here

- Checks (scripts today, each a Jarvis job): `check-backup`, `check-builds`, `check-disk`,
  `check-endpoints`, `check-expiry`, `check-nuc`, `check-paths`, `check-stalled`,
  `check-dev-servers`, `check-host-cpu-load`, `check-retired` / `retired`, `repo-versions`,
  `config-drift`, `log-signatures`, `backup-drill-sample`, `local-llm-perf`, and
  `health-check.py` (its launchd agent `com.jarvis.healthcheck` becomes a Jarvis job).
- The store and lifecycle: `daemon/incidents.py`, the `alerts` and incident tables in
  `jarvis.db`, `ALERTS-NOW.md` generation, `alert-review.py` (the LLM review of noisy alerts
  proposes, Jarvis files).
- `core.notify` splits: the record half (`sentinel.raise_(key, text, severity, evidence)`)
  moves here; the deliver half (cooldown, ping through Herald, postpone filter) stays in Jarvis.

Stays in Jarvis: `watchers.py` (directory events are orchestration input), `errorlog.py` (the
shared Jarvis + HUD system error log; revisit), `check-hud-findings.py` (about Jarvis's own
findings), `postpone.py`, the soak gate and checkpoint (they read Sentinel).

## Interface

- CLI `sentinel check <name> [--json]` (exit 0 ok, 1 problem, 2 could not check), `sentinel
  incidents [--open] [--json]`, `sentinel resolve <id>`, `sentinel alerts-now`.
- Library `sentinel.raise_()`, `sentinel.resolve()`, `sentinel.open_incidents()`, importable
  from Jarvis's venv (stdlib + sqlite only).
- Each check writes `state/checks/<name>.json` (`ran_at`, `ok`, `problems`, `evidence`).

## First steps for the build session

1. Map every reader of `alerts`, incidents and `ALERTS-NOW.md` (checkpoint, soak gate, morning
   digest, HUD `incidents.js`, `status.py`, selftest) before moving anything.
2. Back up `jarvis.db`; move the tables with their rows; keep incident ids.
3. Move checks one family at a time, each with its selftest checks, and repoint its job.
4. Split `core.notify` last, with the delivery half calling Herald once Herald exists (until
   then it calls `ping.py` as today).

Load-bearing rules from Jarvis: the counts-only ping, postponed alerts stay silent until their
day, an incident resolves only on evidence, never on silence. Read `~/.jarvis/NEXT-PROMPT.md`
before touching `~/.jarvis`.

## As built (2026-10-17)

Two stages in one session (Anze: "store first, checks second").

- `sentinel/` (paths from `SENTINEL_HOME`, default `~/.sentinel`; tests set it to scratch): `store.py`
  (`state/sentinel.db`, config), `alerts.py` (the alert record: one row per key, count), `incidents.py`
  (lifecycle, resolver registry, reconcile, prune, `alerts_now`), `checks.py` (runner and envelope),
  `cli.py`, `bin/sentinel`, `bin/sentinel-job.sh` (Jarvis's job entry point), `config.toml`
  (`[incidents]`, `alerts_now` path), `tests/test_sentinel.py`, `tools/migrate-from-jarvis.py` (one-off).
- Moved with their rows: `alerts` (261) and `incidents` (55, ids kept). The old tables are still in
  `jarvis.db`, unused, until Anze confirms dropping them; backup `~/.jarvis/jarvis.db.bak-sentinel`.
- `core.notify` split: Sentinel records (`sentinel.raise_(key, text, severity, evidence, incident=)`:
  the alert count and the incident), Jarvis delivers (postpone filter, cooldown with backoff,
  `alerts.log`, push). Jarvis keeps `alert_writes` and a new `alert_delivery(key, last_written)`.
  A postponed alert is counted with `incident=False`.
- Resolvers: the registry and the pass are Sentinel's. The five that read Jarvis's state (stuck-event,
  loop-crash/stale, burn, delivery, handler-failed) and the three that read check artifacts
  (machine-down, build-failed, server-failed) are registered by Jarvis's `daemon/incidents.py` on import
  (the three artifact ones could move here; they read `state/checks/nuc.json`, `builds.json` and the
  HUD's `servers-health.json`). The daemon's sentinel loop calls `sentinel.reconcile()`.
- `ALERTS-NOW.md` (still at `~/.jarvis/ALERTS-NOW.md`): open incidents, rewritten when one opens or
  resolves, not on every hit.
- Checks: 14 scripts moved to `checks/` (`backup builds disk endpoints expiry nuc paths stalled dev-servers
  host-cpu-load retired repo-versions config-drift log-signatures`). Jarvis's jobs keep their names and
  schedules; each now runs `sentinel-job.sh check <name>` and asserts `state/checks/<name>.json`. The
  runner stamps `ran_at`, `exit`, `ok` into the artifact. A check never alerts: `nuc` and `builds` put
  `alerts: [{key, message, severity, cooldown_s}]` in the artifact and the job's `deliver_alerts = true`
  makes the script handler call `core.notify`.
- Differences from the design above:
  - The check exit codes are the scripts' own (0 ran, non-zero could not run), not 0/1/2; the artifact
    says whether the machines are fine (`ok`).
  - Reports stay in `~/.jarvis/reports/` (retired, config-drift, log-signatures, dev-servers) and
    `repo-versions` still writes `~/.jarvis/state/versions/` because the session-start hook reads it.
  - `check-paths` still reads Jarvis's job config through `core.load_config`, and `check-builds` and
    `repo-versions` still import Jarvis's `scripts/notmine.py`.
  - Not moved: `health-check.py` (its launchd agent `com.jarvis.healthcheck` still runs it), `local-llm-perf`
    (it feeds Jarvis's local-LLM triage), `backup-drill-sample` (called by `~/.local/bin/mac-backup`, not a
    job), `alert-review` (it reads Jarvis's `alert_writes` and `postponed.json`, which are delivery data).
