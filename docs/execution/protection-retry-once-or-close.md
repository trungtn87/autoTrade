# Protection setup: confirm, retry once, otherwise abort the combo

Status: **PARTIALLY IMPLEMENTED — not approved for live deployment.**

Implemented: durable <=2 protection POSTs per leg for explicit documented POST
rejections (101204, 109400, 109425), no duplicate on uncertain POST/verification
failure, abort coordinator, exact residual MARKET close when the selected
container is exclusively owned, close-fill confirmation, and exact post-close
cleanup. An active SL is retained until owned closure is proven. Close quantity
uses the contract precision, not a blind four-decimal rounding.

State is stored in the existing execution JSON: `protection_attempts`,
`protection_journal`, `abort`, and `emergency_close`. Owned MARKET abort fills
are included in the existing exit ledger, so a closed failed entry does not
reappear as live quantity after restart. PostgreSQL integration exercises SL,
TP and trailing exhaustion/closure.

**Not implemented/certified:** safe partial-combo abort while its container is
still shared with live peers and competing exits. That case retains confirmed
protection, reports CRITICAL and does not submit a blind MARKET close. Once
peers have actually closed and their exits are verified cleaned, recovery can
close the now-exclusive residual. The tests deliberately assert this guard;
it is a remaining acceptance blocker, not fulfillment of all shared aborts.

Owner decision, 06 November 2026: after entry, require TP/SL (and the existing
trailing leg when configured); at most one protection retry; if still unable to
establish required protection, exit that combo. Healthy same-side combos remain
allowed. This decision supersedes leaving a trade running merely because its
SL is active when TP/trailing setup fails.

## Outcome contract

For a filled entry, success means all required owned protection orders have
been verified against BingX, or owned exit fills have already closed the combo.
A failure outcome means its exact remaining quantity has been exchange-confirmed
closed. Neither an accepted POST nor a close request is confirmation of success.

`COMPLETE` is allowed only for a live combo with fully verified required
protection. `ABORT_CLOSED` requires actual owned close/exit fills summing to the
entry fill. Any uncertain or unsafe state remains non-success and CRITICAL.
No claim is made that an external API can always accept a protection or close.

## Normal path

1. Persist the exact filled MARKET identity, executed quantity and container ID.
   Never resubmit the entry as a protection retry.
2. Submit SL and verify its exact ID, symbol, side, type, trigger, quantity and
   active state. Do not submit TP/trailing until SL is verified.
3. Submit and verify TP, then the existing trailing allocation if configured.
   Use the original strategy targets and allocation; do not change the strategy
   to make a failed submission pass.
4. Re-read required protections and owned fills before `COMPLETE`. If a target
   filled during setup, account for that owned fill, not as a missing order to
   recreate. Verify remaining protection against the owned residual.

## One retry means at most two POSTs per required leg

Count SL, TP and trailing separately. Save attempt count and prepared intent
BEFORE every POST in the existing execution-state journal. Counts survive
restart, direct execute calls and scheduled recovery. Recovery cannot reset the
budget or create a fresh entry. No new credentials, tables or cron are needed.

| First attempt outcome | Allowed next action |
|---|---|
| Exact order is verified active | Continue; no second POST |
| Explicit exchange rejection establishes no order was accepted | Wait through the existing request guard, recheck exposure, POST once more |
| Exact owned order is confirmed canceled/rejected with zero fills and no executing trigger child | Recheck exposure and use the one remaining POST |
| Timeout, disconnection, HTTP/server ambiguity, missing acknowledgement | Reconcile reads; do not assume rejection and do not POST again |
| Known order ID exists but verification is temporarily unavailable | Re-query that ID; do not create a replacement |
| Second permitted POST is definitively rejected / verified not usable | Enter `ABORT_REQUIRED` |

The transport must explicitly classify a definitive rejection; it must not
catch every exception as retryable. A missing row in open orders, or a single
price/quantity match, is not proof that the first POST failed. Bound read
reconciliation by the existing pacing/backoff policy. An unresolved submission
moves to `ABORT_RECONCILE_REQUIRED`, never a third POST or reported success.

## Abort path applies to SL, TP and trailing setup failure

Even if SL is active, a definitively exhausted TP/trailing setup enters the same
abort policy requested by the Owner. Keep the SL until the abort protocol has a
verified safe handoff; do not simply delete it and hope the MARKET close works.

Before any close POST:

- Acquire the same account/store mutation exclusion used by setup/recovery.
- Freeze new actions for the affected unsafe symbol/side; this is a transient
  failure guard, not a blanket same-side admission policy.
- Prove the entry and every owned exit/close fill by exact order/trigger IDs.
  Compute `own_remaining = entry_filled - owned_exit_filled - owned_close_filled`.
  Never substitute the shared position's amount or an average entry price.
- Reconcile the aggregate exchange quantity against all tracked residuals.
  Unknown manual changes, missing history or unknown accepted POSTs prevent a
  blind close. A stale pre-entry snapshot cannot authorize the close.
- Establish that existing owned conditional exits cannot also execute against
  the same residual during/after the abort. Pending trigger children count as
  outstanding executions. Do not rely on cancel acknowledgement alone.
- Establish that concurrently executable peer exits cannot consume this combo's
  reserved residual. A database lock serializes bot workers, not BingX matching.

Once these conditions are proven, persist one deterministic emergency-close
client ID and the exact owned residual before sending the opposite MARKET close
through the existing trade client. Never use close-all or the whole container
quantity. Confirm the exact close ID, side, type and cumulative filled quantity.
An ambiguous close POST is queried by that client ID and is never blindly sent
again. A partial close does not mean flat; any later residual submission needs
terminal evidence for the previous close and a fresh owned-fill calculation.

After actual full closure, cancel only verified leftover orders belonging to
that combo. Verify their terminal statuses. Do not touch another combo's orders.
If no quantity remains because an owned SL/TP/trailing already filled, do not
send an additional MARKET close.

## Shared-position handoff is a release prerequisite

An active SL plus an emergency MARKET close can both fill and consume a peer's
quantity. Canceling the SL first instead creates an unprotected interval if the
close then fails. Retry-once alone does not solve this race. Snapshot equality
and a bot-side database lock do not prove an atomic exchange-side reservation.

Therefore implementation must provide and test a trustworthy exchange-side
handoff/execution constraint, or another verified isolation mechanism, before
removing the current shared-position emergency-close guard. It must preserve
same-side admission and the strategy. Do not substitute an unconditional close
of `executed_qty` and call it safe. The earlier native exit-budget/SL replacement
blocker is not waived by this design.

If the necessary ownership or execution proof cannot be obtained, keep verified
protection, suppress further risk-increasing actions and send one CRITICAL
alert with periodic bounded escalation for manual intervention. This is an
explicit failure requiring intervention, not a normal successful fallback.
No implementation may promise that the account is flat without exchange proof.

## Implementation and acceptance checklist

- Extend `protection_journal.py` with durable attempt/verdict states and a
  definitive-rejection classifier. Separate POST from read verification errors.
- Connect exhausted required protection to one abort coordinator in
  `final14_executor.py` / `service.py`; remove the keep-running TP-failure policy
  only after the safe handoff is implemented. Keep public APIs unchanged.
- Reuse existing execution-state storage, emergency-close journaling, private
  request guard and reconcile loop. Cleanup must not independently mutate a
  combo while its setup/abort owner holds the mutation lock.
- Test initial success; retry success; two rejections for each leg; lost POST
  response with/without acknowledgement; verification-only failure; crash before
  POST/after POST/after acknowledgement; already-filled target; manual cancel;
  partial close; lost close response; peer exit at every abort boundary; and
  concurrent workers. Assert <=2 protection POSTs per leg, zero duplicate
  entries, exact owned closure, no peer order mutation and no false COMPLETE.
- Run the real executor schedules on isolated PostgreSQL and BTC/ETH in both
  directions. A simulator that assumes OCO or suppresses competing fills is not
  evidence for the shared-position handoff guarantee.

No deployment, merge or actual BingX account action is authorized by this
specification; the original Owner-review gate remains in force.
