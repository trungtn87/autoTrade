# AutoTrade Live Dashboard on Nhan Hoa Vibe Hosting

This configuration deploys **only the read-only FastAPI dashboard**, not
the trading engine. Supabase PostgreSQL remains the data source.

## Deploy from Vibe Hosting

- Git Repository: `https://github.com/trungtn87/autoTrade.git`
- Branch: `deploy/vibe-dashboard-20261030` (test branch)
- Build Type: `Dockerfile`, path `Dockerfile` at repository root
- Root/Working directory: repository root (leave unset if optional)
- Web container port: from `PORT`, defaults to `8000`
- One app instance and one uvicorn worker
- URL paths: `/health`, `/login`, `/mobile`

## Environment Settings

Enter the following as private environment variables in Vibe Hosting only:

```dotenv
DATABASE_URL=EXISTING_SUPABASE_POSTGRES_URL_WITH_SSLMODE_REQUIRE
DB_SCHEMA=autotrade_analytics
DASHBOARD_PUBLIC_READONLY=false
DASHBOARD_PASSWORD=YOUR_OWN_LONG_PASSWORD_AT_LEAST_12_CHARS
APP_SESSION_SECRET=YOUR_OWN_RANDOM_SECRET_AT_LEAST_32_CHARS
COOKIE_SECURE=true
BINGX_READ_API_KEY=SEPARATE_BINGX_READ_ONLY_KEY
BINGX_READ_API_SECRET=SEPARATE_READ_ONLY_SECRET
BINGX_SYMBOLS=BTC-USDT,ETH-USDT
BINGX_REQUEST_DELAY=0.6
```

To preserve the exact same historical reporting window, copy the existing
`DASHBOARD_START_AT` value from the old dashboard if configured.

Important:
- Never provide trading-enabled `BINGX_API_KEY` or `BINGX_API_SECRET` to this service.
- Keep `DASHBOARD_PUBLIC_READONLY=false` unless you explicitly want all
  account metrics publicly viewable by anyone with the URL.
- The existing app schedules GET-only BingX archive checks at UTC :07; its
  `pg_try_advisory_xact_lock` coordinates the shared Supabase analytics store.
  Run only one app instance. Before leaving both dashboard services running,
  ensure no unnecessary duplicate BingX archival traffic.
- Do not alter the running Autotrade V2 bot service, its orders, or its database.

## Verify

1. The Docker build must succeed. Deployment must report a running web service.
2. `/health` returns JSON with `service=autotrade-dashboard` and
   `trade_api=false`. A 502/Bad Gateway means the app is not listening,
   crashed, or the proxy targets the wrong port.
3. Open `/login` via HTTPS then `/mobile` and confirm current Supabase
   data, mobile fonts, and Combo results. `/health` alone does not prove the
   Supabase connection works; check the `/api` response through the UI.
4. Compare history and the last successful archive in Supabase with the
   original dashboard. Only after verification may the old dashboard be stopped.

No migration, deletion, BingX trade API calls, or production bot cutover.
