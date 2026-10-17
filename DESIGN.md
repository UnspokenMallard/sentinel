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
