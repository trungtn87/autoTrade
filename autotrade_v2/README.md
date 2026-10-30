# AutoTrade V2 — exchange execution engine

Runs inside the root `unified_app.py` on the **single production service**.
Engine endpoints are under `/engine`: `/engine/health` and
`/engine/status`.

- Market data: Supabase + BingX WebSocket closed 15m BTC/ETH candles;
  validate 3400 recent candles. REST only repairs actual gaps with backoff.
- Locked strategy: deterministic FINAL14 20FINAL logic; no new strategy
  inferred from the Dashboard.
- Execution: deduplicated BingX MARKET entry, confirmed fill, persistent
  protection journal, exchange-native full SL, 50% hard TP, and 50%
  trailing stop activating after favorable 0.5% move with 0.5% callback.
- Safeguards: credential preflight, exchange rate guard, exact position ID
  routing, uncertain-response recovery, no guessed position/order mutation.
- Closed-position cleanup: conservative 60s worker **enabled by default**.
  Only owned protective orders are canceled after unambiguous exchange proof
  the specific position is flat; does not resize active SL or interfere with
  another Combo. `EXIT_CLEANUP_ENABLED=false` is an emergency opt-out only.
- Legacy SL cancelReplace lifecycle is permanently disabled.
- Reporting: BTC/ETH/error Discord webhooks, bounded delivery/retry, with
  optional relay. Trading does not depend on Discord success.

The engine may submit **live** orders automatically when complete Supabase
and BingX credentials are present and the private credential preflight
passes. Only **one** live writer may run; there is no distributed leader
lease across hosts. See [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).
