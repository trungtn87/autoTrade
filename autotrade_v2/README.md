# AutoTrade V2 Clean

A clean, layered rebuild of the FINAL14 live engine.

## Architecture

1. **Data** — public market input, candle validation, persistence, MarketSnapshot.
2. **Strategy** — deterministic FINAL14 only. No network, DB writes, Discord, or order calls.
3. **Execution** — TradeIntent -> BingX order execution. Disabled by default.
4. **Control** — typed errors, logging, throttling/circuit-breakers, health and test gates.
5. **Orchestrator** — thin sequencing only; contains no trading logic.

Dependency direction is one way:

    Data -> Strategy -> Execution

Control observes all layers. Strategy never imports Data or Execution.

## Runtime data path

    BingX historical REST -> DB
    BingX 15m WebSocket -> DB
    DB -> FINAL14
    TradeIntent -> BingX authenticated trade API

Historical REST is bootstrap/backfill only. The live path never polls Kline REST and never performs automatic gap recovery. WebSocket data is written only after a 15m candle is confirmed closed; FINAL14 always reads the persisted DB snapshot.

## Safety state

V2 starts with:

    BOOTSTRAP_ENABLED=false
    WEBSOCKET_ENABLED=false
    EXECUTION_ENABLED=false
    DRY_RUN=true

So a fresh deploy is inert until the persistent database is attached and the data path is explicitly enabled.

## Locked strategy

The FINAL14 research implementation and config are vendored from production commit
`d73d53309e8df7777f23f1c5514e2d677734a7b8`.

CI compares old production and V2 strategy outputs on the same deterministic 3400-candle input.

## Tests

- compile all V2 modules
- old-vs-V2 FINAL14 signal parity
- Layer-1 one-request + DB contract
- Layer-3 execution gate remains closed by default
