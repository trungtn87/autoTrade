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

Draft PR #54, branch fix/v2-analytics-ownership. Existing archive modules moved
to V2 with compatibility imports, not duplicated. Dashboard starts no scheduler,
mounts no ingest, and live DB reads never fall back to theoretical reporting.
Legacy report calculation is retained in V2 only for old offline fixtures.

Archive pacing is at least 6s, with nonblocking execution activity/guard
observation and a 2-minute exclusion around candle boundaries. It waits in place
while execution is busy; it never takes ExecutionStore locks or reserves a
trading request slot. This is conservative execution headroom, not proof of
exchange-wide/IP quota isolation. Actual provider limits still need qualification.
HTTP 429 stops a burst and schedules cooldown, honoring Retry-After. Signing
happens after pacing. Every HTTP retry verifies the advisory lock connection.
A dedicated lock transaction disables its own server idle timeout; it does not
change settings on engine connections.

Per-symbol completed-window cursors, exact inserted/updated/unchanged counters
and raw UPSERTs commit atomically in sync_runs JSON. Restart resumes committed
progress; resumed_by lineage prevents replaying consumed ancestors. Coverage uses
end_ms rather than job completion time, including Dashboard freshness. The V2
status endpoint exposes redacted collector state/configuration codes, allowing
future runtime checks without SSH/log access.

The proposed result store has separate SQLAlchemy metadata so legacy init_db
cannot implicitly create trade_results. Its additive draft migration was generated
with checksum-verified Supabase CLI 2.81.3 and tested against isolated PostgreSQL
17. Generated columns expose typed report fields from one payload; RLS/revokes
prevent anonymous access. No production migration, archive or backfill executed.
Existing manual provenance tables/views remain untouched. Unknown Combo/entry
states persist as UNRESOLVED; duplicate mappings cannot count as CLOSED.

CLOSED requires a fully FILLED entry and balanced, exactly linked exit quantities.
TP+Trailing and TP+SL remain one intent. Missing monetary evidence remains NULL;
unknown outcomes are not counted as TP/SL wins/losses. Aggregate ROI, when data is
verified, uses total net PnL / total actual initial margin, not a sum of percentages.

Owner confirmed 2026-11-03: hosting pulls GitHub directly, without SSH/password
management or runtime logs. Repository deployment contract specifies main. Only
a separate branch has been pushed. The historical production trigger cannot be
confirmed from the available source/DB evidence; do not invent a missing-key
cause. Future acceptance can use redacted HTTP status and persisted DB audits.

Independent reviews prompted and verified fixes for duplicate mappings, premature
CLOSED, schema-absence HTTP failures, reconciliation lock/retries, CLI lock bypass,
Docker packaging, execution DB-lock contention, backfill starvation, resume
lineage, stale coverage display and SQL NULL identity checks.

Remaining acceptance gates; DO NOT MERGE/DEPLOY:

1. Provider/IP quota isolation is not empirically established.
2. Current archived data has no actual initial-margin or complete position/funding
   attribution evidence. Monetary reconciliation remains deliberately incomplete:
   PnL/fees/margin/ROI/outcome stay NULL. No claim of verified strategy performance.
3. Draft migration is locally validated but not applied. Historical Dashboard
   coverage must be staged and reconciled before replacing the existing view.
4. Process-start recovery marks old running audits interrupted under the collector
   lock while preserving raw cursors. Real-host restart acceptance remains required.
5. No Owner-approved production backfill or exchange comparison. Added/duplicate
   production counts are unknown, not zero. Phase E has not run.
6. PR #54 is a draft integration checkpoint, not final phase-by-phase acceptance.

Rollback before rollout: discard/revert branch; production/DB remain unchanged.
After an approved rollout restore the prior single-container image, with no engine
overlap. Preserve all new/raw/manual tables and data; do not drop history.

## Verification

* Current local Dashboard suite: 111 tests passed, including 30 focused cases.
* All 22 V2 offline commands, unified routes/lifespans/PWA, resource sampler,
  Python compile, JavaScript syntax and whitespace checks passed.
* Isolated PostgreSQL 17: migration, NULL constraints, event-ID UPSERT idempotency,
  anonymous denial, two-session advisory exclusion and disconnect release passed.
  The container has no network or published ports and uses only synthetic data.
* Remote baseline CI, real Chromium/PWA and full-history RUN17 parity all passed
  at commit 7b5a547. Later hardening must pass CI again before acceptance.
* Previous Docker build/network-none smoke passed with trading, websocket and
  bootstrap disabled and no credentials. This does not establish production health.
* Known credential-pattern scan passed; no credentials were fetched or printed.

Local TestClient initially hung under sandbox transport restrictions. Rerunning
with sandbox network permission while the suite still blocked external sockets
resolved it; no exchange network was enabled by the test runner.
