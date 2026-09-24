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
