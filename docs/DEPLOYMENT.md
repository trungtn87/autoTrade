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

WebSocket starts automatically with `DATABASE_URL`. The code keeps existing
BTC/ETH symbols, native 50/50 TP/trailing, 0.5% activation and 0.5% callback.
Host PORT defaults to 8000; service path/Dockerfile settings are not Environment
variables. Existing Discord webhook values remain optional and unchanged. Other
legacy environment overrides may remain for compatibility, but are not needed for
the normal deployment. Keep production settings and data unchanged; no production
cutover is authorized by this documentation update.

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
