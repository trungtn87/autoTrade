# Independent deployment from main

No deployment, production environment change, migration or bot cutover is
performed by the integration PR. Keep Supabase; do not provision a replacement
Nhân Hòa database. Never put real secrets in this repository or build arguments.

## Set environment before the first startup

Create **two separate services**, both repository `trungtn87/autoTrade`, branch
`main` after acceptance. Use exactly one instance and one worker for each.

| Setting | Engine | Dashboard |
| --- | --- | --- |
| Root/build path | `/autotrade_v2` | `/dashboard` |
| Dockerfile | `Dockerfile` inside selected root | `Dockerfile` inside selected root |
| Docker context | `autotrade_v2` | `dashboard` |
| Railpack config | `autotrade_v2/railpack.json` | `dashboard/railpack.json` |
| Startup | `python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1` | `python -m uvicorn autotrade_live.mobile:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1` |
| Container/domain port | `PORT`, default `8000` | `PORT`, default `8000` |
| Status | `/status`; `/health` can be 503 if candles are stale | `/health`, `/login`, `/mobile`, `/api/dashboard` |

On Nhân Hòa Vibe Hosting, select GitHub, branch and the root above, then Dockerfile
(preferred, reproducible minimal Dashboard dependencies) or Railpack. Set all
runtime Environment variables below **before deploy**. Railpack's default Python
install uses that root's `requirements.txt`; Dashboard includes legacy Streamlit
tools there, while Docker installs only `requirements-web.txt`. Neither root
imports or launches the other application. Do not override the startup with the
other service's command. Confirm domain target port matches `PORT`. Successful
build logs alone do not demonstrate successful startup: inspect runtime logs.

## Engine environment

Keep the existing production values unchanged; these names describe configuration,
not permission to enable live trading in this task.

| Variable | Value/purpose |
| --- | --- |
| `DATABASE_URL` | Existing Supabase PostgreSQL URL, TLS `sslmode=require` |
| `PORT` | `8000`, or match domain mapping |
| `BOOTSTRAP_ENABLED` | Existing setting; `false` for offline acceptance |
| `WEBSOCKET_ENABLED` | Existing setting; live stream normally `true`; `false` for offline checks |
| `EXECUTION_ENABLED` | `false` for acceptance; live only on the approved unique writer |
| `DRY_RUN` | `true` for acceptance; preserve approved live value during later cutover |
| `BINGX_API_KEY`, `BINGX_API_SECRET` | Engine-only secrets required for live execution |
| `SYMBOLS` | Existing universe, default `BTC-USDT,ETH-USDT` |
| `BINGX_WS_URL` | Existing override if any; default `wss://open-api-swap.bingx.com/swap-market` |
| `TP_TRAILING_SPLIT_ENABLED` | Preserve live value, runtime default `true` |
| `TP_TRAILING_ACTIVATION_PCT` | `0.005` (0.5%) |
| `TP_TRAILING_CALLBACK_RATE` | `0.005` (0.5%) |
| `EXIT_CLEANUP_ENABLED` | Preserve existing approved opt-in; default `false` |
| `STATE_DB` | Default `autotrade_v2.db`; Supabase is the production state source |
| `DISCORD_ENABLED`, `DISCORD_WEBHOOK_BTC`, `DISCORD_WEBHOOK_ETH`, `DISCORD_WEBHOOK_ERROR` | Existing reporting settings; disable for offline checks |
| `DISCORD_RELAY_URL`, `DISCORD_RELAY_TOKEN` | Preserve if configured |
| `EMAIL_ALERTS_ENABLED`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_STARTTLS`, `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_TEST_ON_STARTUP` | Preserve existing optional alert configuration |
| `LOG_LEVEL`, `BINGX_BASE_URL` | Preserve overrides if configured |

The code still ignores the retired `POSITION_LIFECYCLE_ENABLED` flag. Do not change
strategy or exit behavior during deployment. Import can connect to Supabase and
initialize existing store tables, so never smoke-test with production credentials
in CI. Offline acceptance uses temporary SQLite only. Before a later live cutover,
stop/disable every old execution writer, including suspended hosts that may resume;
this document does not authorize that cutover.

## Dashboard environment

| Variable | Value/purpose |
| --- | --- |
| `DATABASE_URL` | Existing Supabase PostgreSQL URL, TLS `sslmode=require` |
| `DB_SCHEMA` | `autotrade_analytics` |
| `PORT` | `8000`, or match domain mapping |
| `DASHBOARD_PUBLIC_READONLY` | Preserve current access mode; `false` requires login, `true` exposes metrics publicly |
| `DASHBOARD_PASSWORD` | Private secret, at least 12 characters, required for private mode |
| `APP_SESSION_SECRET` | Private random secret, at least 32 characters, required for private mode |
| `COOKIE_SECURE` | `true` with production HTTPS |
| `DASHBOARD_START_AT` | Copy existing value exactly if configured; retains reporting window |
| `BINGX_READ_API_KEY`, `BINGX_READ_API_SECRET` | Separate exchange key restricted to read-only permission |
| `BINGX_SYMBOLS` | Existing setting, normally `BTC-USDT,ETH-USDT` |
| `BINGX_REQUEST_DELAY` | Existing pacing, normally `0.6` seconds |
| `COLLECTOR_TOKEN` | Preserve if used by the existing optional signal receiver |

Never inject engine `BINGX_API_KEY`/`BINGX_API_SECRET` into Dashboard. Its client
uses GET only; it has no trading engine imports or exchange order mutations. The
archive scheduler writes the existing analytics mirror, not exchange orders, and
uses a PostgreSQL advisory lock. Keep one Dashboard instance. Do not run the manual
GitHub backfill workflow as an integration test: it writes analytics production.

## Build and checks (without deploying)

```sh
docker build -t autotrade-v2 autotrade_v2
docker build -t autotrade-dashboard dashboard
```

The CI smoke test starts each image without external networking or production
credentials, execution off. On a later separately authorized deployment, verify
runtime status, Supabase reads and recent records, WebSocket freshness for engine,
and authenticated Dashboard history/Combo/PnL/TP/SL/trailing on a mobile browser.
PWA requires HTTPS outside localhost. Do not equate `/health` alone with verified
Supabase data or successful exchange preflight. A stale engine candle store can
return 503 without process failure; do not repeatedly restart it to bypass backoff.
