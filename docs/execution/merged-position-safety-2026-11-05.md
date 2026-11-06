> Update 7 November 2026 (Vietnam): the Owner subsequently selected Option A,
> emergency closure of the entire affected merged container after required
> protection retries exhaust. The historical partial-combo handoff blockers below
> describe the earlier design. Current implementation, limits and test gates:
> [Protection retry and Force Close](protection-retry-once-or-close.md).
> Normal native-exit budget/SL resizing risks remain separate and explicit.

# BingX merged-position incident — investigation and safety groundwork

Status: **DRAFT / NOT ACCEPTED / DO NOT DEPLOY**. Updated 06 November 2026:
the blanket same-side entry rejection has been removed. Independently verified
same-side combos may enter and receive their own native SL/TP/trailing while
sharing a position ID. This update fixes the incident's fill-versus-net-delta
identity failure. It does not certify atomic native-exit budgeting or SL
replacement races. No live account actions, production DB writes, merges or
deploys were performed.

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
  The production resolver now validates the exact MARKET client/order ID,
  symbol, side, type and executed quantity before accepting its explicit
  `positionID`. Peer exits no longer have to leave a net delta equal to entry.
  A missing explicit ID still needs a durable, unambiguous baseline; missing or
  conflicting evidence never authorizes routing to an arbitrary same-side ID.
- `exit_cleanup.py`: remove the two-flat-snapshot cancellation authorization.
  Only exact owned fills summing to entry authorize cleanup; missing shared IDs
  remain diagnostics. Persist own entry/exit/remaining quantities. Continue
  monitoring previously complete entries even after safety is downgraded.
- `service.py`: use the ledger in the existing cleanup loop, include pending
  siblings, downgrade unsafe live `complete` states to reconciliation, preserve
  exact closed-combo cleanup while blocking mutations of live ambiguous peers,
  allow healthy same-side entry after a fresh read-only exposure/protection
  reconciliation. Genuine missing protection, unexplained quantity drift,
  unowned exits, duplicate IDs or unknown submissions still prevent increasing
  exposure. Closed-combo exits must be cleaned before another entry can use
  that side. A healthy peer's mere presence is never a blocker.
  Persist recovery backoff, limit automated attempts to eight, bound identical
  failure alerts and preserve the no-new-entry recovery rule.
- `store.py`: one store-wide PostgreSQL advisory session lock across execution
  and cleanup; SQLite host-wide file locking for offline/local operation. No
  leases expire mid-call. This intentionally serializes more than one symbol.
- `final14_executor.py`: normalize entry aliases; refuse emergency closing an
  unproven smaller residual by attributing a shared position size to a combo;
  remove the floating point tolerance for cross-combo close sizing. Persist
  verified fill identity before any position/protection calls; persist the
  resolved container before conditional POSTs. Re-read every required protection
  before COMPLETE, validating exact order ID, symbol, side, type, original size
  and active state. A canceled SL during setup cannot yield COMPLETE. Exceptions
  after a durable fill retain known exposure. Same-side recovery never blindly
  closes the shared container.
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
This draft detects the sum-of-resting-orders budget diagnostic and does **not**
implement an unproven replacement protocol or claim server-side OCO. To honor
the Owner's latest instruction, the diagnostic alone is not an admission veto
for every otherwise healthy same-side combo. It remains explicit as
`EXIT_BUDGET_UNVERIFIED`; it is not evidence that a verified active SL has gone
missing. COMPLETE describes verified own protections, not certification of
exchange-side mutually exclusive execution. Actual own SL size mismatches after
TP fills remain critical, retain the existing SL and require reconciliation;
they are not silently treated as safe or adjusted by blind subtraction.

The Owner's requirements to retain all native independent full SL/TP/trailing
and guarantee their total executable quantity never exceeds exposure need a
verified exchange-side atomic mutually-exclusive reservation mechanism, or a
revised execution model. No such capability has been established for the
current merged Hedge-mode orders. It would be unsafe to present a relaxed
identity rule alone as the complete fix.

## Verification

- Full offline V2 suite: PASS, 23 command groups including 42 unittest cases;
  external sockets blocked. The suite is rerun on changes; see PR CI for the
  final commit evidence. Dashboard regression: PASS, 135 tests. Compile and
  whitespace checks pass.
- New merged regression script passed: shared IDs; TP overlap; owned trailing
  completion; exact-only cleanup; simultaneous completion; missing snapshots;
  manual SL cancellation; manual size drift; invalid side/type; duplicate
  ownership; missing history; exit budget; independent store locks; incident
  size-delta reproduction. Lost conditional POST/restart tests run separately
  for SL, TP and trailing and assert no duplicate/no shape-only adoption.
- PostgreSQL integration script: **CI PASS** on the preceding draft commits,
  including the existing PostgreSQL analytics integration. Locally BLOCKED:
  this environment
  rejects `socket(AF_UNIX)` with `PermissionError: [Errno 1] Operation not
  permitted`, before the isolated server starts. CI installs dev-only
  `pgserver==0.1.4` and runs the integration without production credentials.
  The new update adds the full engine schedule on PostgreSQL: C16 entry,
  C7 same-side entry overlapping C16 TP, exact C16 cleanup preserving C7,
  and restart idempotency. Review the rerun on the final commit as well.
- PostgreSQL checks cover independent sessions, WAL visibility/restart,
  competing lock exclusion, exception release and immutable historical marks.
  Passing those does not establish exchange-side atomicity or safe behavior
  after loss of the advisory-lock database session mid-exchange operation.
- Dashboard regression suite and compile checks are run without modifying
  Dashboard. No real BingX orders/test orders are submitted.

## Unfinished acceptance and manual intervention

The production fill-based resolver and healthy same-side admission are now
implemented, not a proposal. `merged_entry_test.py` runs the real service/store/
executor over a Decimal-based fake exchange with shared container IDs and
parent-linked fills. It exercises same-side admission, partial/full peer exit
during entry, simultaneous exits, exact cleanup, delayed snapshots, lost MARKET
and conditional replies, restart before SL, SL canceled during setup, manual
position changes, missing/duplicate/wrong-side/type history, TP with oversized
SL retained for reconciliation, competing stores and closed-position orphan
cleanup. These are tested safety outcomes, **not acceptance of the strict
atomic-exit-budget requirement**. The last case deliberately escalates the
oversized SL rather than pretending it can be resized race-free.

Stop-quantity replacement, full native-exit budget safety, lock-session-loss
fencing and automatic live shared-position protection restoration still require
verification. Unknown conditional POSTs never get a second POST. Pending
recovery eventually requires manual intervention rather than retrying forever.
The existing reconciliation loop now watches filled nonterminal trades as well
as completed ones, including recovery-exhausted states with known container IDs.

Absent/mismatched history, unknown accepted POST, external/manual size changes,
missing active SL and full SL after partial TP must remain visible risks; no
automatic whole-container close or cancellation is allowed. An ambiguous POST
cannot be safely recovered solely from matching parameters.

## Safe rollout after acceptance

1. Do not merge/deploy this draft or grant it approval as a complete fix.
2. Resolve the exit-budget/atomic protection design and wire proven fill-based
   protection handling without changing 20FINAL targets or ratios.
3. Run all 15 end-to-end event schedules on a deterministic fake exchange,
   PostgreSQL concurrency/crash tests and independent review of the final SHA.
4. Only after Owner approval, pause new entries during rollout, reconcile a
   read-only snapshot of every active combo and exact protection order, and
   identify any manual intervention needed before resuming entries.
5. Do not replay old signals, overwrite historical results, or automatically
   close/repair the current live position from this report.
