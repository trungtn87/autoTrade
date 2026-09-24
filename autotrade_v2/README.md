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

## Safety state

V2 starts with:

    AUTO_SCHEDULER=false
    EXECUTION_ENABLED=false
    DRY_RUN=true

So creating/deploying the service does not place orders and does not schedule BingX market calls.

## Locked strategy

The FINAL14 research implementation and config are vendored from production commit
`d73d53309e8df7777f23f1c5514e2d677734a7b8`.

CI compares old production and V2 strategy outputs on the same deterministic 3400-candle input.

## Tests

- compile all V2 modules
- old-vs-V2 FINAL14 signal parity
- Layer-1 one-request + DB contract
- Layer-3 execution gate remains closed by default
