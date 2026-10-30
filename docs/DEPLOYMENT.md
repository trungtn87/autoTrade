# Autotrade one-service deployment (Engine V2 + Live Dashboard)

## One VPS/hosting application, same GitHub main

The *default* now requires only **one hosting application**. The separate
service directories remain supported but are optional.

| Setting | Correct value |
| --- | --- |
| Repository / branch | `trungtn87/autoTrade` / `main` |
| Project / root path | `/` (NOT `/autotrade_v2` or `/dashboard`) |
| Builder | Root `Dockerfile` preferred; root `railpack.json` also supported |
| Command | `python -m uvicorn unified_app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1` |
| Public port | `8000`, or the host-provided `PORT` |
| Dashboard | `/`, `/login`, `/mobile`, `/api/dashboard` |
| Dashboard health | `/health` |
| Engine status / health | `/engine/status`, `/engine/health` |

**Old incorrect build log:** If dependencies are only `flask` and
`requests` from the legacy root `requirements.txt`, this is not the combined
deployment. The unified root requirements must also install FastAPI, pandas,
SQLAlchemy, etc. Successful build alone does not prove the unified startup.

Do not configure two separate services on a hosting plan that allows only one.
Do not use the legacy `bingx_server.py` or `python bingx_server.py` to start
the new service.

## Environment for all required functionality

**These values belong in the hosting Environment, not in GitHub.** The single
service shares one Supabase database and needs the following secrets:

| Variable | Where to get the value | Feature lost if missing |
| --- | --- | --- |
| `DATABASE_URL` | Current Supabase project → Connect → PostgreSQL connection string | Persistent candles/history/state |
| `BINGX_API_KEY`, `BINGX_API_SECRET` | Existing BingX **trading** API credentials | Real order execution |
| `DISCORD_WEBHOOK_BTC` | Existing BTC Discord channel → Integrations → Webhooks | BTC trade reports |
| `DISCORD_WEBHOOK_ETH` | Existing ETH Discord channel → Integrations → Webhooks | ETH trade reports |
| `DISCORD_WEBHOOK_ERROR` | Existing error Discord channel → Integrations → Webhooks | Error notifications |
| `BINGX_READ_API_KEY`, `BINGX_READ_API_SECRET` | BingX API Management, **separate read-only key** | Dashboard historical archive updates |
| `DASHBOARD_PASSWORD` | Owner-selected private password of at least 12 characters | Dashboard private login |
| `APP_SESSION_SECRET` | Random 32+ character secret, e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"` | Signed Dashboard sessions |

**A total of 10 secret/value entries:** the shared `DATABASE_URL` is set once.
`DB_SCHEMA=autotrade_analytics` is automatically provided by the unified
entrypoint for Supabase; no manual schema variable is required. The app defaults
`PORT` to 8000 and HTTPS `COOKIE_SECURE=true`. The following extra variables
are needed only if that feature was in use on the previous host:

| Keep if enabled previously | Why |
| --- | --- |
| `DISCORD_RELAY_URL` + `DISCORD_RELAY_TOKEN` | Existing relay for outbound Discord traffic |
| `EXIT_CLEANUP_ENABLED=true` | Preserve safe orphan-order cleanup; code defaults false |
| `DASHBOARD_START_AT` | Preserve fixed historical reporting start |
| `COLLECTOR_TOKEN` | Optional webhook Combo tag receiver |
| `EMAIL_ALERTS_ENABLED` + `SMTP_PASSWORD` + corresponding SMTP settings | Optional email error alerts |

Do **not** add `EXECUTION_ENABLED`, `DRY_RUN`, `WEBSOCKET_ENABLED`,
`BINGX_SYMBOLS`, `BINGX_REQUEST_DELAY` or `DB_SCHEMA` manually for this
unified deployment. Live execution is automatically enabled with complete
Supabase + BingX trading credentials on the revised engine; missing credentials
leave execution disabled. The existing BingX credential preflight, candle
validation, order journaling and duplicate-event checks remain.

## Mandatory cutover and acceptance

1. Before entering trading keys into the new service, shut down and disable
   **all old BingX writers**, including sleeping hosts that may resume. A single
   Docker service is not a distributed single-writer lease; do not let rolling
   deployments run two live workers with the same account.
2. Deploy **one instance, one worker**, root build directory. Keep a record of
   the exact deployed SHA.
3. Open `/health`: Dashboard must return HTTP 200, `trade_api=false`.
   Open `/login`, log in, and verify `/mobile` / `/api/dashboard` data.
4. Open `/engine/status`: V2 runtime must be present. Open
   `/engine/health`: verify `execution_credentials_verified=true`,
   WebSocket/data freshness and `layer4` configuration. A 503 from V2
   denotes stale candles; it does **not** by itself mean the HTTP server crashed.
5. Verify all three `layer4.discord_*_configured` flags and a **received test
   message** for BTC/ETH/error. No webhook = incomplete functionality, even
   when trading works.
6. Verify active Dashboard archive times and TP/SL/trailing history in Supabase.
   Confirm existing `EXIT_CLEANUP_ENABLED` and optional relay behavior still
   match old production. Do not trigger manual GitHub backfill just to test
   deployment, because it writes analytics data.

Combined hosting means both applications run in one Python process and share
the hosting Environment. This is less isolated than two services, although
the Dashboard route handlers remain read-only against BingX. Private login
and the separate GET-only BingX API client are retained.

## Optional standalone service deployment

Original service roots are still available for other hosting plans:
`/autotrade_v2` with `app.main:app` and `/dashboard` with
`autotrade_live.mobile:app`. Standalone Dashboard still requires
`DB_SCHEMA=autotrade_analytics` on Supabase. Do not start both these
applications in addition to the unified service.
