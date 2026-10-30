# Production deployment — ONE application

This is the current deployment contract for AutoTrade. **Do not deploy a
second engine, create a second dashboard service or bring back Render configs.**

## Hosting settings (Nhân Hòa Vibe / Docker)

| Setting | Value |
| --- | --- |
| GitHub | `trungtn87/autoTrade` |
| Branch | `main` |
| Root / Docker context | `/` (or `.` for context path) |
| Builder | `Dockerfile` |
| Dockerfile path | `Dockerfile` |
| Container port | `8000` |
| Workers / instances | `1` / `1` |
| Start | Docker CMD runs `uvicorn unified_app:app` |
| Dashboard | `/`, `/mobile` |
| Status | `/health` (Dashboard), `/engine/health` (V2), `/engine/status` (V2) |

Railpack is supported by the **root** `railpack.json`, but Dockerfile is the
recommended default. No manual command override. Configure the host HTTPS
certificate or an upstream TLS terminator correctly; a Certificate Provider
value of `None` alone does not establish that TLS is valid.

## Required Environment — complete functional setup

Set these **eight entries** on the **single** hosting application before live
cutover. Use existing values from prior hosting where available. Never place
real values in source, GitHub issues or build arguments.

| Variable | Purpose / obtain from |
| --- | --- |
| `DATABASE_URL` | Existing **Supabase PostgreSQL** connection string from Connect (TLS) |
| `BINGX_API_KEY` | Existing trade-enabled BingX API key for the V2 engine |
| `BINGX_API_SECRET` | Corresponding BingX trade API secret |
| `DISCORD_WEBHOOK_BTC` | Existing Discord BTC channel → Integrations → Webhooks |
| `DISCORD_WEBHOOK_ETH` | Existing Discord ETH channel → Integrations → Webhooks |
| `DISCORD_WEBHOOK_ERROR` | Existing Discord error channel → Integrations → Webhooks |
| `BINGX_READ_API_KEY` | A **separate BingX read-only** key for history collection |
| `BINGX_READ_API_SECRET` | Matching read-only secret |

The following are **only conditional** on an already-used feature:
- `DISCORD_RELAY_URL` and `DISCORD_RELAY_TOKEN`: **keep both** if the
  previous hosting sends Discord through a relay.
- `DASHBOARD_START_AT`: keep if you intentionally lock an existing historical
  reporting cutoff.
- `COLLECTOR_TOKEN`: only if an external Combo-link webhook receiver is in use.
- Email/SMTP variables: only when email alerting is in use (Discord is the
  primary configured notification path).
- `EXIT_CLEANUP_ENABLED=false`: **emergency opt-out only**; safe orphan cleanup
  is now **enabled by default**. If an old host still carries the value
  `false`, remove it after confirming the cleanup mechanism is approved.
  No extra variable is needed on a clean install.

**Do not add** `EXECUTION_ENABLED`, `DRY_RUN`, `WEBSOCKET_ENABLED`,
`POSITION_LIFECYCLE_ENABLED`, `TP_TRAILING_SPLIT_ENABLED`, `DB_SCHEMA`,
`DASHBOARD_PASSWORD`, `APP_SESSION_SECRET`, `DASHBOARD_PUBLIC_READONLY`,
`BINGX_REQUEST_DELAY` or `BINGX_SYMBOLS` for the normal deployment.
The unified process provides the desired defaults:
- Live execution only with complete Supabase + trade credentials, plus BingX
  private preflight and candle readiness. **No live/dry-run switch.**
- WebSocket BTC-USDT/ETH-USDT on closed 15m candles, 3400-candle validation.
- Full native SL, fixed TP on 50% and exchange-native trailing on 50%;
  trailing activation **+0.5%**, callback **0.5%**.
- Legacy post-entry SL cancel/replace lifecycle stays **disabled**.
- **Conservative 60s cleanup ON**: only known owned orphan orders after an
  exchange-confirmed flat position; no SL resizing or guessed cancellation.
- Dashboard opens directly without login, with public read-only redacted data.
  Archive checks run in-app hourly; no Render or extra hosted collector needed.

## Required cutover checks

1. **Before starting this new writer:** stop and disable every older BingX
   trading writer (including suspended Render services that might restart).
   Take care with host rolling deployments: two overlapping engine processes
   on the same account can place duplicate trades. No distributed fencing
   exists in this version.
2. Deploy one instance/worker and check exact deployed `main` SHA. `/health`
   should return `autotrade-dashboard`, `/mobile` should load without login.
3. `/engine/health`: confirm `ok=true`, both symbols have 3400 valid candles,
   `websocket_enabled=true`, `ws.connected=true`, `ws.data_healthy=true`,
   `execution_enabled=true`, `execution_credentials_verified=true`,
   `dry_run=false`, `exit_cleanup_enabled=true`,
   `position_lifecycle_enabled=false`, `tp_trailing_split_enabled=true` and
   `bingx_trade_guard.blocked=false`. A stale-candle 503 is distinct from a
   crashed server.
4. Confirm all three `layer4.discord_*_configured` flags are true AND that
   actual BTC/ETH/error **test notifications arrive**. Configured is not
   equivalent to delivered; Discord cannot block exchange order protection.
5. Confirm the Dashboard archive has recent `sync_runs` and actual BingX
   history/Combo attribution. Validate TP, SL and trailing from a **real**
   exchange order before declaring end-to-end execution accepted.
6. Verify no orphan protections remain after verified closed positions.
   Lack of evidence must fail closed (do not cancel another position's order).
7. Keep secrets private; rotate the old webhook that had been committed in
   the removed legacy Flask file, if it remains usable. Removing the file
   does not erase historical commits.

**Existing runtime data is not deleted or migrated by this repository
cleanup.** The owner is responsible for removing Render services after safe
writer cutover. CI never sends real orders, and green CI does not certify a
completed trade or live Discord delivery.
