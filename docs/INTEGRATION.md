# V2 + Dashboard integration record

## Source heads

- Main before integration: `9bdc9b0f4b32d98fee7c9ffa37b3dbbb43fe6308`.
- V2 source branch (unchanged): `dc554f782668fa03b8f4f212feaa66bdba153c1f`.
- Fresh checkout, clean working tree before merge. No local uncommitted work existed.
- Integration branch: `integrate/v2-live-dashboard-20261030`, based on main.

## Conflict resolution and preservation

16 add/add conflicts in Dashboard: .env.example, README-RENDER-SUPABASE.md,
README.md, analytics.py, bingx.py, collect.py, mobile.py, static/app.css,
static/app.js, static/index.html, static/sw.js, sync.py, render-dashboard.yaml,
tests/test_analytics.py, tests/test_mobile_bridge.py, tests/test_sync.py.
All resolved to current main's newer Dashboard, including every other Dashboard
file. No application code or formulas edited. Engine app, strategy, execution,
recovery, cleanup, SQL and tests copied byte-for-byte from V2. V2's deletion of
root legacy bingx_server.py/render.yaml/requirements.txt was reversed to retain
main functionality. Do not select the legacy root for a new deployment.

Proof commands (must produce no filenames):

```sh
git diff dc554f782668fa03b8f4f212feaa66bdba153c1f -- autotrade_v2/app autotrade_v2/tests autotrade_v2/sql
git diff 9bdc9b0f4b32d98fee7c9ffa37b3dbbb43fe6308 -- dashboard/autotrade_live dashboard/tests
```

## Related open PRs evaluated, not merged

- #41 (`4ac2f9c`): VPS-only tooling; excluded. Review findings include live approval
  gate bypass on restart, backup ownership, certificate reload and recovery cooldown.
- #42 (`1dfd619`): root Dockerfile assumes root build context. Adapted as a
  service-local Dashboard Dockerfile, preserving GET-only separation. Its branch,
  root Dockerfile and deployment instructions were not merged wholesale.
- #43 (`b7b8787`): explicit V2 Railpack startup incorporated with dynamic PORT
  support; service-local Dashboard startup added. PR itself remains open.

## Fresh local checks

- V2: all 20 `*_test.py` scripts PASS, 10 unittest cases PASS, FastAPI import PASS.
  Covers strategy insufficient data, historical data contract, closed-candle gating,
  execution credential gate, Entry/TP/SL, trailing 0.005, duplicate prevention,
  restart reconciliation, crash journal, orphan cleanup and exchange backoff.
- Dashboard: 68 tests PASS using both full requirements and minimal web requirements.
  Covers API/authentication, histories, Combo, fees/PnL, TP/SL/trailing attribution,
  archive scheduler, bridge and terminal/PWA static contracts.
- Both dependency environments `pip check` PASS; Python compile PASS;
  app.js and sw.js syntax PASS.
- Tests execute in sanitized subprocesses with external sockets blocked; no real
  BingX orders or production database writes. A local proxy support dependency
  (`socksio`) was needed for constructing mocked HTTPX clients.
- Local Docker unavailable; independent Docker builds/startup checks delegated to
  fresh GitHub CI on the integration HEAD. Full-history RUN17 parity also runs in CI.
- Supabase read-only connector verification PASS: existing FINAL14-Autotrade
  project, 341 orders, 76 fills, 109 links, 2 forced orders, 385 incomes and 109
  persisted engine states read. No schema/data mutations performed. Latest archive
  success read at 2026-10-29 18:07 UTC; this does not prove current archive freshness.
- Existing Dashboard functions consumed that read-only production snapshot locally
  and generated 109 history records. Existing data has 109 unresolved strategy
  outcomes; this integration preserves that behavior and does not relabel them.
- Real mobile browser/PWA testing added to independent CI, on deterministic local
  fixtures only (production snapshot is temporary and excluded from Git). Local
  Chromium could not launch because this runtime denies its Unix sockets, so the
  browser test runs on GitHub's Ubuntu runner. This is emulation, not a physical
  Android install or an authenticated production ASGI database connection.
- First integration HEAD 8c7411d passed all six fresh workflows, including both
  Docker builds/default entrypoints and full-history 14+6 parity on RUN17
  2021-01-01 through 2026-09-20. New HEAD must pass its own fresh CI again.
- Merge requires all mandatory checks PASS; missing evidence is a blocker.

## Files added by source merge

```text
.github/workflows/autotrade-v2-check.yml
.github/workflows/check-trailing-entry-005.yml
.github/workflows/discord-batch-tests.yml
.github/workflows/final14-realdata-parity.yml
.python-version
autotrade_v2/README.md
autotrade_v2/app/__init__.py
autotrade_v2/app/config.py
autotrade_v2/app/contracts.py
autotrade_v2/app/control/__init__.py
autotrade_v2/app/control/bingx_guard.py
autotrade_v2/app/control/discord_batch.py
autotrade_v2/app/control/errors.py
autotrade_v2/app/control/events.py
autotrade_v2/app/control/logging.py
autotrade_v2/app/data/__init__.py
autotrade_v2/app/data/historical.py
autotrade_v2/app/data/service.py
autotrade_v2/app/data/store.py
autotrade_v2/app/data/validator.py
autotrade_v2/app/data/websocket.py
autotrade_v2/app/execution/__init__.py
autotrade_v2/app/execution/close_position_check.py
autotrade_v2/app/execution/executor.py
autotrade_v2/app/execution/exit_cleanup.py
autotrade_v2/app/execution/final14_executor.py
autotrade_v2/app/execution/identity.py
autotrade_v2/app/execution/lifecycle.py
autotrade_v2/app/execution/protection_journal.py
autotrade_v2/app/execution/quantity.py
autotrade_v2/app/execution/service.py
autotrade_v2/app/execution/store.py
autotrade_v2/app/main.py
autotrade_v2/app/orchestrator.py
autotrade_v2/app/strategy/__init__.py
autotrade_v2/app/strategy/engine.py
autotrade_v2/app/strategy/final14_config.py
autotrade_v2/app/strategy/final14_exact_strategy.py
autotrade_v2/app/strategy/final14_research/__init__.py
autotrade_v2/app/strategy/final14_research/indicators.py
autotrade_v2/app/strategy/final14_research/layer3_long.py
autotrade_v2/app/strategy/final14_research/signals.py
autotrade_v2/app/strategy/final14_research/smc_ob.py
autotrade_v2/app/strategy/final14_research/smc_structure.py
autotrade_v2/app/strategy/final14_research/two_trail_layer12.py
autotrade_v2/app/strategy/strategy.py
autotrade_v2/requirements.txt
autotrade_v2/sql/discord_trade_outbox.sql
autotrade_v2/sql/system_events.sql
autotrade_v2/tests/backtest_realdata_parity.py
autotrade_v2/tests/bingx_request_guard_test.py
autotrade_v2/tests/data_layer_test.py
autotrade_v2/tests/dependency_rules_test.py
autotrade_v2/tests/emergency_close_reconcile_test.py
autotrade_v2/tests/execution_gate_test.py
autotrade_v2/tests/execution_preflight_test.py
autotrade_v2/tests/execution_reconcile_test.py
autotrade_v2/tests/exit_cleanup_test.py
autotrade_v2/tests/historical_chunk_test.py
autotrade_v2/tests/layer4_event_test.py
autotrade_v2/tests/legacy_execution_test.py
autotrade_v2/tests/live_recovery_test.py
autotrade_v2/tests/log_redaction_test.py
autotrade_v2/tests/position_id_routing_test.py
autotrade_v2/tests/position_lifecycle_test.py
autotrade_v2/tests/protection_journal_test.py
autotrade_v2/tests/strategy_insufficient_candles_test.py
autotrade_v2/tests/test_discord_batch.py
autotrade_v2/tests/tp_trailing_split_test.py
autotrade_v2/tests/trailing_activation_test.py
autotrade_v2/tests/utils.py
autotrade_v2/tests/websocket_closed_candle_test.py
```

Additional integration files: root README/.gitignore, this report and deployment
runbook, per-service Dockerfile/.dockerignore/railpack.json, Dashboard minimal web
requirements, offline test harness, and independent test/build CI. Existing V2
workflows are retained and enabled for main; parity enabled on integration PRs.

No strategy, BingX order behavior, production environment, Supabase data/schema,
or deployed service changed by this PR. Old branches remain intact.
