# AutoTrade V2

Layered live engine for the locked FINAL14 strategy.

## Runtime architecture

1. **Layer 1 — Data**
   - Bootstrap/backfill from public BingX REST.
   - Live 15m candles from BingX WebSocket.
   - Persist only closed candles to Supabase.
   - Validate the full 3400-candle strategy window before handing it to Layer 2.
   - A lightweight watchdog checks only store count/latest timestamp during normal operation and uses REST only to repair a real gap.

2. **Layer 2 — Strategy**
   - Deterministic FINAL14 only.
   - Input: validated MarketSnapshot.
   - Output: TradeIntent.
   - No network, DB writes, Discord, or order calls.
   - Fails closed with L2.FINAL14.INSUFFICIENT_CANDLES when fewer than 3400 candles are available.

3. **Layer 3 — Execution**
   - TradeIntent goes directly to the BingX executor.
   - MARKET entry, fill confirmation, full hard TP and full hard SL.
   - No trailing and no partial exit.
   - Persistent event-id dedupe prevents resending processed orders.

4. **Layer 4 — Control**
   - Structured events, audit persistence, Discord notifications, error classification, health reporting, and BingX request guard.

## Limited 60-second orphan exit cleanup

After BingX accepts Entry → full SL → hard TP 50% → trailing 50%, exit
execution is still exchange-native. Since BingX may leave conditional exits
open after a full trade closure, an **independent 60-second cleaner** checks
*only* strategies with a durable entry `positionId`, their exact stored SL,
TP, trailing IDs and actual exchange executedQty. It reads one openOrders and
one positions snapshot per active symbol each cycle. If stored exit IDs are
missing, it obtains one bounded 6-day `allOrders` history per symbol, and
matches the exact string/integer IDs. It never trusts an unrelated order
returned from `GET /trade/order`, and it never sends DELETE for an order not
visible in the current openOrders snapshot. All requests share the existing
BingX rate guard.

An orphan is deleted ONLY after (a) this combo's own filled exit quantities
sum exactly to its actual entry fill, or (b) the exact positionId is flat in
two consecutive 60-second passes. Each DELETE targets one verified owned
orderId, is durably journaled BEFORE sending, and has at most two attempts.
Missing/contradictory exchange evidence blocks mutation with an error alert.
It never uses cancel-all, cancelReplace, SL resizing or any unrelated order.
The original 15-second unfinished setup recovery is retained separately.

**Partial fill caveat:** if trailing/TP closes half, the cleaner warns when
the original SL exceeds the combo's remaining quantity, but does NOT modify
that SL. BingX does not reliably OCO separate conditional orders. Review
oversized SL warnings and use an exchange-native whole-position SL product
before treating this configuration as fully protected in all partial-close
scenarios. Historical records missing positionId are skipped; legacy
`pending_sl_replace` states raise a special warning because an untracked
SL replacement cannot be safely deleted.

This limited feature can be disabled independently with
`EXIT_CLEANUP_ENABLED=false`. Default is false (explicit opt-in only). No new trading signal,
entry, or dashboard API requests are added. It is NOT a guarantee against
unexpected exchange-side execution, API outage, or position liquidation.

## 50/50 TP + exchange-native trailing (BingX-managed exits)

The deployed FastAPI entrypoint keeps 50% hard TP, 50% exchange-native
trailing and full-quantity hard SL. The SL is submitted and verified before
TP/trailing. Trailing activation is 0.5% favorable movement from the actual
BingX entry fill; its callback rate is 0.5%. Render overrides are
`TP_TRAILING_SPLIT_ENABLED`, `TP_TRAILING_ACTIVATION_PCT` and
`TP_TRAILING_CALLBACK_RATE`.

**BingX owns exit execution after all four orders are confirmed.** The bot
no longer polls completed positions every 15 seconds, automatically cancels
remaining exit orders, or calls `cancelReplace` to resize SL after TP/trailing
partial fills. `POSITION_LIFECYCLE_ENABLED` is now ignored at runtime, even
if a legacy Render environment variable is still set. `/health` reports
`position_lifecycle_enabled=false` while the 50/50 split stays on.

**Do not remove the one-time placement recovery:** after a dropped reply or
Render restart, an unfinished entry/SL/TP/trailing setup is reconciled
idempotently so the bot does not duplicate entry or abandon protection.
The existing per-order write-ahead journal and emergency safeguards remain
in the initial placement path. These are not post-entry position management.

**Exchange caveat:** separate `STOP_MARKET`, `TAKE_PROFIT_MARKET`, and
`TRAILING_STOP_MARKET` orders are not automatically an OCO bracket.
The bot no longer resizes/cancels them after partial fills. Test how BingX
handles the *actual account mode* with multiple combos sharing the same
`positionId` and confirm no over-sized orphan exit is left. If the
exchange does not manage them safely, do not deploy this mode to live
funds without an exchange-native alternative.

No extra periodic private BingX GET is introduced. A completed trade's
protection order IDs are still written to the execution record for later,
independent read-only dashboard analysis; exact per-combo PnL is optional.

## Locked strategy

Version: FINAL14_RR_TP2_2026-09-21

Source backtest:
- GitHub run 35623101331
- Commit 11c215db8cc1be1c0872360291f308bba7582cf7
- 100% hard TP + hard SL
- TP <= 2%
- Selection requires non-negative PnL on 6M, 1Y, 3Y and FULL; then maximizes 3Y PnL.

The vendored FINAL14 strategy files remain locked. Runtime cleanup is performed outside the strategy core and CI verifies parity.

## Production data path

    BingX REST bootstrap/recovery -> Supabase candles
    BingX 15m WebSocket -> closed candle -> Supabase candles
    validated 3400-candle snapshot -> FINAL14
    TradeIntent -> BingX authenticated trade API

/health uses lightweight candle-store metadata instead of loading and recalculating the full 3400-candle window. Full validation still occurs on bootstrap, live candle ingestion, and recovery before strategy execution.

## Tests

CI checks:
- Python compilation
- layer dependency boundaries
- FINAL14 parity
- insufficient-candle fail-closed behavior
- Layer 1 storage/recovery contracts
- WebSocket closed-candle gate
- Layer 3 execution/preflight/dedupe behavior
- Layer 4 event trace
