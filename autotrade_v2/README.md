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

## 50/50 TP + trailing, lifecycle monitor (runtime activation)

The deployed FastAPI entrypoint `app.main` now defaults to enabling
`TP_TRAILING_SPLIT_ENABLED` and `POSITION_LIFECYCLE_ENABLED`, following an
explicit request to activate trailing using GitHub auto-deploy. The underlying
`Settings()` dataclass still defaults these values to false for offline
tests and tooling. If the Render environment defines either key, that value
takes precedence over the startup default. To disable 50/50 exits without a
GitHub commit, set `TP_TRAILING_SPLIT_ENABLED=false` in Render; disabling
`POSITION_LIFECYCLE_ENABLED` also forces trailing off. The callback rate is
`TP_TRAILING_CALLBACK_RATE=0.005` (0.5%) unless overridden.

**Deployment caveat:** A code push only auto-deploys if the Render service
follows `autotrade-v2-clean` with auto-deploy enabled. GitHub cannot reveal
whether Render currently overrides the feature flags; check `/health` for
`tp_trailing_split_enabled`, `position_lifecycle_enabled` and
`tp_trailing_callback_rate` after deploy.

**Concurrent combos on the same symbol and side are allowed.** There is no
position-busy entry gate. When BingX aggregates positionAmt across same-side
orders, never treat that aggregate as a single trade's quantity. Use the
individual entry's exchange-confirmed `executedQty` as the authoritative
quantity for its 50/50 split. Use exact decimal arithmetic and the current
symbol's `quantityPrecision`, `tradeMinQuantity`, and `tradeMinUSDT`:
allocate one half rounded down to the exchange step and the exact remainder
to the other. If either half is invalid, use a single full-quantity hard TP.
An exchange snapshot of positionAmt must contain at least the actual filled
quantity of this entry before partial exits are placed.

Hard SL is always placed and confirmed first. Each trade retains its own
persisted entry/SL/TP/trailing order IDs and, after terminal entry
confirmation, a durable position-lifecycle state. The Layer-3 monitor runs
at startup and every 15 seconds even if entry processing already finished.
For each trade:

1. Read the real `executedQty` values on **its own** BingX exit order IDs,
   not the expected TP volumes, to determine its remaining order allocation.
2. Cross-check the sum of active tracked combos' remaining exchange fills
   against BingX's account-wide `positionAmt`. A mismatch from an external
   close or in-flight trade requires reconciliation; it **never blocks new
   entries** and it never blindly changes another combo's SL.
3. When a partial close is confirmed, replace just that trade's old SL
   with an exact-quantity SL at the original stopPrice via BingX
   `cancelReplace` / `STOP_ON_FAILURE`. Persist deterministic replacement
   identifiers before posting and reconcile by clientOrderId on lost replies.
4. When all exit fills for **that trade** are confirmed (even if other
   same-side trades remain), cancel only its own remaining SL/TP/trailing
   order IDs, verifying each. Also clean up when BingX confirms the entire
   account-side position is flat, e.g. after liquidation/manual close.
5. Persist per-order cleanup progress so a Render restart resumes without
   replaying the market entry or cancelling an unrelated order. Raise a
   Layer-4 CRITICAL event if an exchange response is ambiguous or unsafe.

For new split trades, the exchange-native trailing activates after a favorable
**0.5% price move from the BingX-confirmed entry fill**, independent of each
combo's fixed TP. LONG activation is `avg_fill_price * 1.005`; SHORT is
`avg_fill_price * 0.995`. Configure this via
`TP_TRAILING_ACTIVATION_PCT=0.005` (default). Separately,
`TP_TRAILING_CALLBACK_RATE=0.005` sets the **0.5% retracement distance**
used by BingX's native trailing order (not leveraged PnL or a discrete
0.5%-per-tick step). Hard TP, original fill validation, and SL are unchanged.
Already-submitted trailing orders keep their original exchange activation;
restart/recovery must not duplicate or silently replace them. After deployment,
verify that no Render environment override changes either `0.005` setting.

**Production warning:** Tests simulate BingX responses but do **not**
establish live API compatibility, order routing semantics or cross-process
locks. An API rejection or unexpected exchange state can cause a partial
position to remain protected only by its original hard SL; inspect critical
lifecycle alerts and test-account behavior. If per-trade ownership is
uncertain due to external/manual activity, the monitor alerts instead
of cancelling/modifying potentially unrelated protections.

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
