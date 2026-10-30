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

## Minimal Environment (no execution switch)

The Engine does **not** read `EXECUTION_ENABLED` or `DRY_RUN`.
It automatically activates live execution only when all three values below
are supplied; otherwise order submission remains disabled. BingX read-only
credential preflight, 3400-candle readiness and duplicate-event checks still apply.
**This is not a cross-host single-writer lease:** shut down and disable the
old BingX engine (including suspended auto-restarting hosts) **before** adding
these credentials to a new service. Do not configure credentials on two engines.

| Engine variable | Required value |
| --- | --- |
| `DATABASE_URL` | Current Supabase PostgreSQL URL (TLS enabled) |
| `BINGX_API_KEY` | Engine trade API key |
| `BINGX_API_SECRET` | Engine trade API secret |
| `DISCORD_WEBHOOK_BTC` | Secret webhook URL for BTC order notifications |
| `DISCORD_WEBHOOK_ETH` | Secret webhook URL for ETH order notifications |
| `DISCORD_WEBHOOK_ERROR` | Secret webhook URL for bot/errors alerts |

WebSocket starts automatically with `DATABASE_URL`. The code keeps existing
BTC/ETH symbols, native 50/50 TP/trailing, 0.5% activation and 0.5% callback.
Host PORT defaults to 8000; service path/Dockerfile settings are not Environment
variables. Keep all three Discord webhooks configured to preserve current trade and error
notifications. Missing Discord webhooks will not crash the bot, but their respective
notifications will not be delivered. Preserve `DISCORD_RELAY_URL` and
`DISCORD_RELAY_TOKEN` too if the existing host uses the relay to reach Discord.
Other legacy environment overrides may remain for compatibility, but are not
needed for the normal deployment. Keep production settings and data unchanged; no production
cutover is authorized by this documentation update.

## Dashboard environment

Keep Dashboard private by default. Required values:

| Variable | Value/purpose |
| --- | --- |
| `DATABASE_URL` | Same existing Supabase URL |
| `DB_SCHEMA` | `autotrade_analytics` |
| `DASHBOARD_PASSWORD` | Private login password, 12+ characters |
| `APP_SESSION_SECRET` | Random secret, 32+ characters |
| `BINGX_READ_API_KEY`, `BINGX_READ_API_SECRET` | Separate read-only BingX credentials for history sync |

Do not create `PORT`, `COOKIE_SECURE`, `DASHBOARD_PUBLIC_READONLY`,
`BINGX_SYMBOLS` or `BINGX_REQUEST_DELAY` manually: the app or host already
has production defaults. Retain `DASHBOARD_START_AT` only if the existing
history cutoff must be preserved. Existing optional `COLLECTOR_TOKEN` is
needed only for an enabled signal receiver.

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
