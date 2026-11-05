# BingX merged-position incident — investigation and safety groundwork

Status: **DRAFT / NOT ACCEPTED / DO NOT DEPLOY**. This change is not the complete
merged-position trading implementation requested by the Owner. In particular,
it conservatively blocks another same-side entry while a tracked combo remains
live; it does not yet authorize independent concurrent combos in a merged
container. No live account actions, production DB writes, merges or deploys
were performed during this investigation.

## Base and related changes

Base `main`: `e656d49cf41252680c6524e6f4a46fb7317a944d`, fetched before editing.
Open PR #67 concerns insufficient-margin rejections and Discord. Its executor
and reconciliation-test files overlap; do not overwrite it when integrating.
No Dashboard, strategy, target, leverage, notional, credential or public route
changes are included. Existing `processed_targets` storage is reused.

## Evidence and root cause

Read-only queries against `FINAL14-Autotrade.public.processed_targets` and
`public.system_events` established the following. Times below are Vietnam time
(UTC+7), 05 November 2026. Exchange facts are the evidence persisted by the
running bot, not a fresh authenticated BingX snapshot.

| Event | Time | Exact evidence |
|---|---|---|
| C16 entry | 20:00:29.905 | MARKET `2118327594881601536`; fill `0.0013`; entry `79701.6` |
| C7 entry | 21:45:23.846 | MARKET `2118353993583513600`; fill `0.0013`; entry `78778.5` |
| C16 TP execution | 21:45:25 | child `2118353999560396800`; trigger `2118327640569516032`; fill `0.0007` |
| First C7 identity failure | 21:45:29.136 | `unable to attribute exactly one positionId to this entry`; FILLED true |
| C16 trailing execution | 22:15:20 | child `2118361526779990016`; trigger `2118327655971000320`; fill `0.0006` |
| C16 SL cleanup request | 22:15:32.147 | exact SL `2118327615298834432`, one persisted cancel attempt |
| C16 SL canceled | 22:15:33 | persisted exchange history: CANCELLED, executedQty `0.0000` |

Both entry evidence records contain **`positionID=2118327594902573058`**.
C7's persisted baseline is `{2118327594902573058: 0.0013}`; its final state is
`position_identification_pending`, with no confirmed protection IDs.

The confirmed code defect is the identification invariant in `identity.py`:
all old IDs must remain and exactly one size delta must equal the new entry
fill. If C16 TP executes during C7 entry, the net delta is only
`0.0013 - 0.0007 = 0.0006`. This method cannot resolve the valid merged entry.
The offline incident simulation reproduces that rejection. Recovery reuses the
same old baseline and later reports `a pre-existing position disappeared during
entry`. The original raw position snapshots are not persisted, so the exact
reason that ID disappeared (snapshot lag, rotation, actual state) is unproven.
Do not invent that part of the incident.

Another confirmed compatibility defect: entry evidence uses `positionID`, while
execution identity routing read only `positionId`. Alias normalization now
rejects conflicting values rather than guessing. This alone is not permission
to infer combo ownership from a container ID.

**The C16 SL deletion occurred about 30 minutes after C7's first failure.** Its
own TP plus trailing fills sum exactly to its entry. Therefore the evidence
does not support claiming that deletion was the original cause of C7's missing
protection. C16's SL could temporarily have covered fungible exposure; deleting
it exposed the already unresolved protection gap, but ownership must not be
reassigned on that basis.

## Changes in this draft

- `identity.py`: normalize `positionId`/`positionID`; reject conflicting aliases.
- `merged.py`: pure ledger, per-combo exit quantities by kind, exact-owned-fill
  arithmetic, aggregate exchange comparison, duplicate ownership rejection,
  active SL checks, unowned order checks and executable exit budget diagnostics.
  `entry_proof()` validates exact entry client/order identity; it is groundwork,
  not yet wired to replace the production snapshot-delta resolver.
- `exit_cleanup.py`: remove the two-flat-snapshot cancellation authorization.
  Only exact owned fills summing to entry authorize cleanup; missing shared IDs
  remain diagnostics. Persist own entry/exit/remaining quantities. Continue
  monitoring previously complete entries even after safety is downgraded.
- `service.py`: use the ledger in the existing cleanup loop, include pending
  siblings, downgrade unsafe live `complete` states to reconciliation, preserve
  exact closed-combo cleanup while blocking mutations of live ambiguous peers,
  block new same-side entry while another tracked combo is unresolved/live.
  Persist recovery backoff, limit automated attempts to eight, bound identical
  failure alerts and preserve the no-new-entry recovery rule.
- `store.py`: one store-wide PostgreSQL advisory session lock across execution
  and cleanup; SQLite host-wide file locking for offline/local operation. No
  leases expire mid-call. This intentionally serializes more than one symbol.
- `final14_executor.py`: normalize entry aliases; refuse emergency closing an
  unproven smaller residual by attributing a shared position size to a combo;
  remove the floating point tolerance for cross-combo close sizing.
- `protection_journal.py`: persist an exact POST acknowledgement before GET
  verification. A lost POST response with only a new price/quantity match is
  **not ownership proof**, even if there is only one matching order. No resend.
- Tests and CI: strengthen obsolete tests which allowed flat-ID cleanup,
  shape-only adoption or unproven residual closing. Add merged incident/ledger
  regressions and isolated PostgreSQL integration script.

## Safety blocker: simultaneous native exits

The existing strategy execution submits full-size SL plus TP/trailing totaling
another full entry size: **2Q executable conditional quantity for Q exposure**.
The recorded orders have `reduceOnly=false`. Merely toggling that flag is not a
valid hedge-mode fix: the official BingX reference says not to send reduceOnly
in Hedge Mode, and documents clientOrderId only for MARKET/LIMIT.

Primary reference inspected:
https://github.com/BingX-API/api-ai-skills/blob/main/skills/swap-trade/api-reference.md

A side-wide reduce-only cap would still not isolate combo ownership: an old
combo's overlarge SL could consume a live peer's remaining quantity. REST polling
and cancel/replace cannot make that race atomic. Posting replacement SL first
creates overlapping executable protection; canceling old SL first opens a gap.
This draft therefore detects budget overcommit and does **not** implement an
unproven replacement protocol or claim server-side OCO.

The Owner's requirements to retain all native independent full SL/TP/trailing
and guarantee their total executable quantity never exceeds exposure need a
verified exchange-side atomic mutually-exclusive reservation mechanism, or a
revised execution model. No such capability has been established for the
current merged Hedge-mode orders. It would be unsafe to present a relaxed
identity rule alone as the complete fix.

## Verification

- Full offline V2 suite passed before the final recovery-limit adjustment:
  23 command groups, including 42 unittest cases; external sockets blocked.
  The suite is rerun after final changes; see PR CI for final commit evidence.
- New merged regression script passed: shared IDs; TP overlap; owned trailing
  completion; exact-only cleanup; simultaneous completion; missing snapshots;
  manual SL cancellation; manual size drift; invalid side/type; duplicate
  ownership; missing history; exit budget; independent store locks; incident
  size-delta reproduction. Lost conditional POST/restart tests run separately
  for SL, TP and trailing and assert no duplicate/no shape-only adoption.
- PostgreSQL integration script: **LOCAL BLOCKED**, not PASS. This environment
  rejects `socket(AF_UNIX)` with `PermissionError: [Errno 1] Operation not
  permitted`, before the isolated server starts. CI installs dev-only
  `pgserver==0.1.4` and runs the integration without production credentials.
- PostgreSQL checks cover independent sessions, WAL visibility/restart,
  competing lock exclusion, exception release and immutable historical marks.
  Passing those does not establish exchange-side atomicity or safe behavior
  after loss of the advisory-lock database session mid-exchange operation.
- Dashboard regression suite and compile checks are run without modifying
  Dashboard. No real BingX orders/test orders are submitted.

## Unfinished acceptance and manual intervention

This PR is safety groundwork, **not acceptance of all 15 requested end-to-end
scenarios**. The ledger models the shared quantity but the production entry
resolver still needs integration with independently verified fills. Existing
pending trades are not automatically granted container ownership. Stop-quantity
replacement, full native-exit budget safety, lock-session-loss fencing,
post-crash acknowledgement ownership and live completed-trade protection
restoration still require design/verification. Pending recovery eventually
requires manual intervention rather than retrying forever.

Absent/mismatched history, unknown accepted POST, external/manual size changes,
missing active SL and full SL after partial TP must remain visible risks; no
automatic whole-container close or cancellation is allowed. An ambiguous POST
cannot be safely recovered solely from matching parameters.

## Safe rollout after acceptance

1. Do not merge/deploy this draft or grant it approval as a complete fix.
2. Resolve the exit-budget/atomic protection design and wire proven fill-based
   entry reconciliation without changing 20FINAL targets or ratios.
3. Run all 15 end-to-end event schedules on a deterministic fake exchange,
   PostgreSQL concurrency/crash tests and independent review of the final SHA.
4. Only after Owner approval, pause new entries during rollout, reconcile a
   read-only snapshot of every active combo and exact protection order, and
   identify any manual intervention needed before resuming entries.
5. Do not replay old signals, overwrite historical results, or automatically
   close/repair the current live position from this report.
