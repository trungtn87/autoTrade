# Analytics architecture investigation — 2026-11-03

Baseline: main e2868623ef4a0ff56972f21cdac8122e808e41dd; clean checkout.
Open PR search returned none. Commit combined-status endpoint returned no statuses;
this does not establish CI success. PR 53 is excluded.

## Confirmed source findings (baseline line numbers)

* mobile.py:30–44 owns archive startup/shutdown; :07 scheduler is a Dashboard worker.
* unified_app.py:38–41 explicitly enters both lifespans. No evidence that mounted
  lifespans are omitted in this revision. Production revision remains unknown.
* render_archive.py:50–55 configuration validation, :60–68 sync history query,
  collect.py:33–44 DDL, bridge and repair, and sync.py client construction all
  occur before SyncRun insertion. Any failure here leaves no failed sync row.
  Scheduler catches exceptions and logs only their class, retrying after 15m.
* render_archive.py:67 searches details LIKE '%days=3%', but sync.py success
  details are JSON with lookback='days=3'; this part is compatible.
* render_archive.py:42–44 caps catch-up to three days: older gaps can be skipped
  permanently after a new successful run. Daily work precedes catch-up planning.
* collect.py:27,34–46 holds a transaction-scoped advisory lock on a separate
  connection while actual work uses other connections. It excludes concurrent
  collectors while the transaction survives, but idle-in-transaction timeout or
  connection loss during long work needs verification in the deployed pooler.
* theory.py build_theory derives outcomes from planned prices/half averages and
  can classify combined exits without proving total closed quantity. It also
  calculates margin using entry notional/leverage, an estimate, not actual margin.
  Those outputs must not represent actual normalized trade results.
* mobile.py mounts /collector ingest and computes outcomes in HTTP requests;
  these responsibilities violate the requested read-only Dashboard boundary.

## Production incident: not yet confirmed

The Owner reports last successful sync at 2026-10-30 01:07 and continued engine
writes. These observations were not independently queried in this session.
No VPS access or runtime environment bindings are available. No assertion about
missing/invalid read-only keys, actual worker liveness, provider failure, DB lock
or production HEAD is justified. Need redacted runtime revision, startup/worker
logs, variable presence/format and sync/event timestamps to identify the trigger.
Local credential absence says nothing about production credentials.

## Sequential review units

A: this evidence report. B: move existing archive implementation to V2, lifecycle
ownership, strict GET allowlist, audited failures and bounded catch-up. C: backend
reconciler with exact identities and quantity checks, private normalized storage
only after live schema review. D: read normalized DB results; retain mobile layout,
remove ingest mount and theoretical outcome fallback. E: Owner-approved read-only
backfill and reconciliation; never execute against production in this task.

No production deploy, backfill, schema application, strategy or execution changes.

## Independently verified Supabase evidence (read-only, 2026-11-03 UTC)

Connected project name FINAL14-Autotrade, PostgreSQL 17.6. No writes executed.

| Observation | Result |
| --- | --- |
| Last successful sync finished | 2026-10-29 18:07:24.193249 UTC = 30/10 01:07:24 Vietnam |
| Successful / failed runs | 61 / 5 |
| Latest failed run | 2026-10-25 10:03:26.117342 UTC |
| BTC fills | 57; latest 2026-10-29 17:08:15 UTC |
| ETH fills | 19; latest 2026-10-29 05:12:25 UTC |
| BTC / ETH orders | 191 / 150; same latest timestamps as respective fills |
| Latest income | 2026-10-29 17:08:15 UTC |
| Latest bingx_state processed_at | 2026-11-02 19:53:50.149834 (timestamp WITHOUT timezone) |
| System startup events since Oct 30 | 13; latest 2026-11-02 07:24:06.733348 UTC |
| trade_results table | Does not exist |
| Existing manual objects | manual_trade_results, manual_trade_results_effective, manual_dashboard_trade_results |

The incident interval is confirmed, not its production trigger. No new failure
rows after Oct 25 supports the pre-audit failure/lifecycle hypotheses but does
not distinguish them. Absence of an audit row is not proof a scheduler never ran.

Raw-field inventory: fills have realisedPNL, commission and commissionAsset;
orders have positionID and leverage but no actual initial-margin field;
position-history has no archived rows. Income types include funding fees,
insurance clearing, realized PnL and trading fees. Consequently actual margin
and funding attribution cannot be inferred safely from currently archived data.
The draft deliberately does not manufacture ROI or allocate position-wide PnL.

## Implementation status / acceptance gates

Draft branch: fix/v2-analytics-ownership. Existing archive modules were moved to
V2 with compatibility imports, not duplicated. Dashboard application no longer
starts a scheduler or mounts ingest. Historical report calculation is retained
in V2 solely for old offline fixture regression; live DB reads never fall back to
that report. Dashboard layout is retained, unknown results are labeled unknown.

The proposed result store uses a separate SQLAlchemy metadata registry so legacy
init_db cannot implicitly create trade_results. No migration has been applied,
and no production backfill or BingX request has been performed by this session.
When schema is absent, API reports normalized results unavailable. This is a
rollout blocker: do not replace the live Dashboard until migration and staged
historical reconciliation preserve its data coverage.

Independent review found and prompted fixes for hidden duplicate mappings,
partially-filled entry finalization, missing-schema HTTP failures, stale advisory
connection detection, reconciliation retries, CLI workflow path and container
package inclusion. This is not final approval. Remaining blockers:

1. Strict execution-priority/shared-IP quota reservation is not implemented.
2. Money verification is deliberately incomplete: PnL/fees/margin/ROI and outcome
   remain NULL even for quantity-confirmed CLOSED results.
3. Migration design needs final typed-column/index/grant review and local Postgres
   verification. Existing manual provenance objects must remain untouched.
4. A killed run can leave running audit rows; interrupted-run recovery needs an
   explicit ownership/lease design and restart integration test.
5. Raw sync step success and whole-attempt success are separate audit rows; raw
   counts do not mean newly inserted counts. Backfill reporting still needs exact
   inserted/updated/unchanged counters and completeness checkpoints.
6. No VPS environment/log evidence, post-deploy evidence, or exchange comparison.
7. Full-history parity/remote CI/browser acceptance remain required.

Rollback before rollout: discard/revert branch; production and database have not
changed. After an approved rollout: restore prior single-container image (never
start a second engine), preserve all archive/result/manual tables; do not drop
historical data. Prefer an additive forward repair after checking schema version.

## Verification completed locally

* Dashboard: 102 tests passed, including 21 new focused cases.
* V2: all 22 commands in scripts/check-offline.py passed; external TCP blocked
  by the suite's sitecustomize. No strategy/execution source diff.
* Unified routes/lifespans, public PWA routes, monitoring unit check passed.
* Python compile, JavaScript syntax and git diff whitespace checks passed.
* Docker build passed using the production Dockerfile plus a temporary BuildKit
  CA mount for this environment; no CA or credentials copied into layers.
* Container smoke passed with --network none, execution/websocket/bootstrap off,
  DRY_RUN=true, empty database credentials; Dashboard and V2 lifespans worked and
  /collector/v1/signals was absent. Smoke uses injected DB-only fixture, not
  production data or an applied result-table migration.
* Independent review repeated after fixes; no final deployment approval.

Remote Actions, full-history dataset parity, Chromium acceptance and a real
Postgres collector-lock/restart test are not reported as passed. The local sandbox
initially blocked TestClient's event-loop transport; rerunning with sandbox
network permission while the test runner itself still blocked external sockets
resolved the hang. No exchange requests were enabled by that rerun.
