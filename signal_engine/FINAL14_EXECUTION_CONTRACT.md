# FINAL14 execution contract v2

Reference: RR_TP22 run 35623101331, attempt 2, research commit `11c215db8cc1be1c0872360291f308bba7582cf7`. Exactly 8 BTC and 6 ETH cases from final14_config.py. No entry, Layer 2, TP or SL optimization in this change.

## Shared decisions

- Closed native candles only: 15m for C7/C9/C10; 1h for C1/C2/C3/C4/C6/TIER. A 1h event is consumed at the close of its fourth 15m candle.
- Vendored research modules calculate entry events, confirmed higher-timeframe inputs and same-bar SMC/OB. The reference source is pinned in CI.
- `tp_pct` and `sl_pct` in config are FRACTIONS (0.020 means 2%). `final14_policy.levels` calculates levels once. The executor rejects signals whose levels differ from the locked config before sending anything.
- MARKET entry, target notional $100 per case. Quantity is $100 / signal close, rounded down to exchange quantity precision. 50x leverage affects required margin, not target notional.
- TP and SL remain anchored to the signal close. They do not move to the eventual fill price. Only exchange price precision rounding applies.
- Attached TAKE_PROFIT_MARKET and STOP_MARKET use CONTRACT_PRICE and the parent position quantity. No trailing, partial TP, netting or reversal between cases. Hedge + SEPARATE_ISOLATED must pass before an entry.
- One active OR pending position per symbol+combo, with durable database uniqueness. Once an old position is confirmed absent by two successful reads in the current scan, a new event can enter on that same scan. The old event can never be resubmitted.

## Persistent lifecycle

`reserved -> submitting -> accepted -> active -> closed`, with pending/unknown states for uncertain responses. The intent and deterministic clientOrderId are committed before POST. The orderId is committed as soon as it is received. The journal survives restart, timeout, later Discord failure and a crash between exchange acceptance and local fill processing.

A pending order is queried by orderId or clientOrderId. No blind retry occurs, even if the lookup errors or reports no record. An uncertain case stays locked for reconciliation; it is not interpreted as flat. An unresolved case may require operator investigation if BingX can no longer provide its order history. No forced unlock or expiry is performed.

The engine verifies the returned attached TP/SL type, trigger source, trigger prices and quantity before reporting a complete order. Missing/ambiguous protection stays flagged and locked, with an error notification through the existing reporting path. It does not fabricate success or automatically resubmit protection. `positionID` and `positionId` are accepted. Legacy active FINAL14 records migrate into the journal; existing exchange orders are not rewritten by this migration. Unmanaged exchange positions block new entries for that symbol and are not automatically closed.

## Verification

- Unit/contract tests cover all 28 BUY/SELL scan-to-exchange payload paths with fake indicator gates and fake exchange transport; invalid percent scaling; concurrent claims; POST timeout; GET timeout; pending fill; missing protection; restart; same-scan re-entry; permanent event dedupe; and legacy migration.
- CI compares full historical event sets against the pinned research source, tests 12,000-bar warmup and runs the actual live adapter at recent historical event closes for every available case/direction plus the final eight closes per symbol. The replay checks entry, TP, SL, timestamp, missing signals and extra signals.
- The runtime pandas/numpy pins match the reference backtest.
- /health exposes the execution contract, source research commit and RENDER_GIT_COMMIT. `execution_ready` only means DB and credentials exist; per-order gates and successful live protection are separate evidence.

## Limits of equivalence

Live exchange fills cannot equal a candle simulator in every path. The reference uses exact close entry and exact trigger exit, applies 0.05% fee per side, excludes entry-candle exits and pessimistically chooses SL when both levels are touched in the same later candle. Live orders start after the signal close; latency, tick rounding, spread, slippage and actual fees differ. If both TP and SL prices trade within a 15m candle, the exchange follows the actual sequence, which 15m OHLC cannot reconstruct. Funding, liquidation, unavailable margin, outages and end-of-backtest mark-to-market are not modeled by the reference.

Thus equality here means the same selected signals, fixed levels, sizing target and independent full-position exit policy. It is not a guarantee of identical trade count or PnL under outages/market microstructure. Attached TP/SL remain on the exchange when the service sleeps. Lost scans are not backfilled into late market entries.

BingX API reference used: https://github.com/BingX-API/api-ai-skills/blob/main/skills/swap-trade/api-reference.md (order query by clientOrderId, attached TP/SL and SEPARATE_ISOLATED).
