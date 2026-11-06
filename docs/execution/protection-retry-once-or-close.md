# Protection retry and emergency Force Close — Option A

Owner decision: 6 November 2026; implementation completed in PR #68.
Merge requires current-head CI, including real isolated PostgreSQL fault schedules.
No test submits a live BingX order or writes the production database.

## Normal execution

Healthy Hedge Mode entries on the same symbol and side remain allowed. Each
combo is identified by its exact MARKET/client order and fills, independently
of concurrent peer exits. The exchange position ID identifies a shared container.
SL is verified first, then TP and the configured trailing allocation. The
20FINAL strategy, target ratios, sizing, 50x leverage and Dashboard are unchanged.

Every required protective leg has a durable budget of at most two POSTs. Only
explicit documented protective POST rejections (101204, 109400, 109425) authorize
the one retry. A timeout, missing acknowledgement, verification GET error or
rate limit is reconciled; it never becomes permission to blindly POST again.

## Exhausted protection in a shared container

`force_close.py` supersedes the former shared-container manual hold with the
Owner-approved whole-container emergency policy. It does not attempt to reserve
one combo's residual with an asynchronous snapshot.

1. Persist a per-symbol/side emergency epoch in existing `processed_targets`
   JSON, target `bingx_force_close`. No new table, credential or public API.
   Block new entries for this side, including pending peers; unaffected symbols
   and the opposite side continue. Generic cleanup cannot mutate this side.
2. Enumerate exact entry evidence for all affected combos, their owned exits,
   current positions, and native exit/trigger history. Manual/unowned same-side
   orders, unaccounted size changes and unknown protective POST outcomes hold
   the emergency unresolved. They never authorize broad cancellation.
3. Persist each exact-ID cancellation before DELETE. Cancel TP/trailing before
   SL to retain coverage as long as possible. Verify terminal native orders and
   attributable trigger fills from fresh exchange observations. A lost DELETE
   reply or cancellation/trigger race is reconciled. At most two DELETEs per ID.
4. Re-read position size and exit states immediately before MARKET. Require
   native residuals minus verified emergency fills to equal actual position
   size, no competing native exits, and terminal prior MARKETs. Persist a
   deterministic CID and exact contract-precision quantity before sending once.
5. Query every uncertain MARKET by its CID. Never resend a prepared CID whose
   outcome is unknown, including a crash after WAL but before POST. Active or
   partial MARKETs remain unresolved. A terminal partial close permits a new,
   uniquely identified residual close after fresh reconciliation, bounded to
   three MARKET submissions per epoch.
6. Require verified zero exposure, no owned active exits and exact attributable
   native/market fills before finalizing. Persist the epoch's quantity
   allocations, then affected combo states, then `closed`. Recovery resumes
   interrupted finalization even when the initiator already became terminal.
   Quantity allocations are not claimed to be exchange-provided per-combo PnL.
7. Otherwise persist `unresolved`, continue bounded read recovery every minute
   and emit CRITICAL alerts at most once per five minutes with affected combos,
   close requests/fills, remaining exposure and active protection IDs.

The confirmed status is `force_close_confirmed`; an uncertain outcome is
`force_close_unresolved`, never COMPLETE or a false CLOSED. Existing exclusive
combo abort handling remains available when the container was not shared.

## Coordinator ownership

All automated mutations share the account-wide PostgreSQL advisory lock.
State writes reuse that exact session; an ownership check verifies its advisory
lock before each mutation, including after private request-guard waiting.
A lost session never reconnects silently and cannot write stale progress.
The next owner reads the durable epoch and reconciles before acting. Use a
session-preserving/direct PostgreSQL connection; transaction pooling cannot
preserve a session advisory lock. SQLite tests use the existing OS lock.

A database lock cannot retract a request already in flight or fence a manual
exchange user. Fresh fills/quantity checks detect observable interference and
hold unresolved. Cancellation precedes MARKET, so if the exchange cannot close
there can be an uncovered interval requiring CRITICAL intervention. This is an
explicit emergency failure, not a promise that an unavailable API always closes.

## Validation

`force_close_test.py` exercises the real service, executor and durable store:
BTC/ETH LONG/SHORT, two/three combos, every failed protective leg, overlapping
entry/TP, partial TP then SL, simultaneous trailing fills, cancellation races,
lost DELETE/MARKET responses, partial and terminal-partial closes, missing
history, stale/error/429 reads, manual orders/quantity, wrong close identity,
every emergency persistence boundary, database write errors, unaffected sides
and private transport fencing. The fake exchange does not implement OCO.

`scripts/check-merged-postgres.py` runs the same schedules on a disposable real
PostgreSQL server, then checks two deployment instances, terminates the owning
DB session during cancellation and verifies recovery by a new owner. CI also
runs the full offline engine/Dashboard suites and full-history strategy parity.

This emergency policy does not certify atomic native-exit budgeting or automatic
SL resizing after a normal partial TP. Those existing risks remain explicit in
the ledger; it does not silently change the strategy to solve them.
