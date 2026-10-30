# AutoTrade production functional baseline — 2026-10-30

## Scope

Single-service `main` (one Docker container/one port) is the supported
deployment. This baseline replaces historical Render, Flask, Streamlit, and
two-service launch arrangements. Removing unused files does not change the
locked trading algorithm or database schema.

**Last user-provided running-state evidence**, obtained before this cleanup:
- `/engine/health` returned `ok=true`, both BTC-USDT and ETH-USDT had 3400
  current 15m candles, WebSocket connected/data healthy.
- BingX credentials verified; `execution_enabled=true`, `dry_run=false`,
  request guard not blocked.
- Native 50/50 TP/trailing on, callback rate 0.005.
- BTC, ETH and error Discord webhook configuration present.
- `position_lifecycle_enabled=false` by deliberate design.
- `exit_cleanup_enabled=false` was a configuration defect: the baseline
  changes the **default to true** and preserves an explicit emergency opt-out.
- The mobile Dashboard rendered without login and displayed strategy/Combo
  analytics. The displayed 30-day theoretical ROI is not account PnL.

The snapshot above does **not** prove real orders, live TP/SL, trailing
execution, orphan cleanup completion, or Discord delivery. Those need
post-deployment evidence. Do not label them PASS from CI alone.

## Frozen functional responsibilities

1. **Engine V2:** closed-candle 15m data → Supabase → locked FINAL14 20FINAL
   strategy → event-deduplicated BingX order handling and recovery.
2. **Exchange protection:** entry fill → full native SL + 50% hard TP +
   50% native trailing (activation 0.5%, callback 0.5%); persisted protection
   journal; read-only preflight; rate-limit/backoff.
3. **Closed-position cleanup:** 60s conservative poll for owned, proven flat
   position only. Never resize a live SL; never cancel peer Combo orders.
4. **Notifications:** separate Discord BTC, ETH and error webhooks;
   relay retained if configured. Delivery failures do not change trading.
5. **Dashboard:** mobile-first public read-only redacted Live Terminal;
   Supabase analytics, theoretical and actual BingX history/Combo matching,
   hourly backend GET-only archive and optional manual GitHub backfill.
6. **Hosting:** one repository branch, one process, one instance, one port,
   no automatic distributed single-writer lease. Render migration and old
   server shutdown are operational tasks, not GitHub code cleanup.

## Tests required on EVERY release

- V2 and Dashboard offline unit/regression suites, including entry dedupe,
  recovery, SL/TP/trailing, rate guard, 60s orphan safety and Discord.
- Unified process startup and route tests, Docker image startup, no-network
  smoke tests and Railpack dependency smoke test.
- Mobile Chromium/PWA and JavaScript syntax validation.
- Locked FULL-HISTORY strategy parity workflow (preserve dataset provenance).
- A real-host runtime checklist per [DEPLOYMENT.md](DEPLOYMENT.md).

**Source-only changes never authorize a deploy or live trade.**
The repository's existing Git history remains available for auditing and
historical backtests. Unmerged experimental branches are not production.
