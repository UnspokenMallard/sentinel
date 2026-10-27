# Heimdall tasks (from the 2026-10-27 audit)

- [ ] [medium] [improvement] Auto-resolve service-down and finding incidents — `heimdall/incidents.py` (resolver registry) — add a resolver that closes the incident when the next `check services` run has the key `ok` — acceptance: with ntfy healthy, `bin/sentinel incidents` shows #157/#160-style incidents closed within one check cycle and `ALERTS-NOW.md` drops them.
- [ ] [medium] [underperforming] Stop `wasted-spend` re-raising every hour — Fafnir raise site and Heimdall dedupe key — raise once per new failed run or when the sum changes — acceptance: `alerts.log` shows at most one `wasted-spend:hud-audit-nightly` line per new failed run.
- [ ] [low] [improvement] Require 2 consecutive failures (or a shared cause) before `service-down` ERROR — `checks/check-services.py` — acceptance: one OrbStack restart yields one incident, not four.
- [ ] [low] [improvement] Acknowledge or fix commit-audit line-ending flags — er-web `.gitattributes` decision, then ack #156 through the HUD — acceptance: incident #156 closed and next `commit-audit` run flags 0.
- [ ] [low] [improvement] Make tests runnable by a standard command — `tests/test_heimdall.py` — add `tests/run.sh` or unittest wrappers — acceptance: one documented command prints 20 passed.
- [ ] [low] [improvement] Remove 0-byte `state/sentinel.db` and `state/outbox.jsonl` after Anze's yes — add to `pending-confirmations.toml` first.
- [ ] [low] [improvement] Retire stale `~/.jarvis/state/{config-drift,path-check,disk-check,endpoints-check,builds-check,nuc-check,expiry-check}.json` after Anze's yes — acceptance: no file in `~/.jarvis/state` older than its live Sentinel twin.
