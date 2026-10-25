# Autotrade Live dashboard + Render + Supabase + mobile PWA

**Production safety:** independent service. The trade engine code is not modified. The collector only calls BingX GET endpoints. An independent private schema stores analytics in the **existing** Supabase database. The bridge reads `public.processed_targets` (target `bingx_state`), which already contains the original Combo and exchange order ID. It does **not** write to the trade engine's tables.

## Status

Code and offline synthetic tests are ready. Production setup requires Render workspace confirmation, database connection string, a separate read-only BingX API key, and real BingX-vs-database reconciliation. A dashboard with zero live rows is not a synced dashboard.

## Supabase

Project: `FINAL14-Autotrade` (`frrlvmhblvqyjxbjspdq`). Use private schema `autotrade_analytics`, not `public`. SQL source: `supabase-schema.sql`. Do not add private schema to Supabase exposed schemas. Do not expose database password, Render environment variables or BingX keys to the PWA JavaScript.

Connection on Render: choose **Supabase session pooler (port 5432)** because Render may not support direct IPv6, as per official Supabase docs. Copy exact pooler host and credentials via Supabase **Connect** UI; don't guess. Set `DATABASE_URL=postgresql://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres?sslmode=require` and `DB_SCHEMA=autotrade_analytics`. PostgreSQL accesses existing `public.processed_targets` with a read-only SELECT; make sure the DB role used by the collector has SELECT rights there and write privileges only in analytics schema when feasible.

## Render

Use the existing **separate Render Free web service** for the PWA and API; keep the live trading bot unchanged. `render-dashboard.yaml` contains **only a free web service** (no paid Render Cron Job). The daily BingX collector instead runs in **GitHub Actions** from default branch `main`; see [`DAILY-SYNC.md`](DAILY-SYNC.md). Dashboard source and Action source are both under `dashboard/` on `main`. The already-created Render dashboard may still target `autotrade-v2-clean`; change only that dashboard service's branch to `main` in Render Settings if you want one source branch. Never switch the trading bot's branch.

PWA is reachable from `https://YOUR-DASHBOARD.onrender.com/mobile` after login; install from Chrome **Add to Home screen / Install app**, or iOS Safari **Share → Add to Home Screen**. The shell can be installed but **financial dashboard data is never offline cached**. This is not a native App Store/Play Store app. Mobile interface uses HTTPS, HttpOnly signed session cookie, no BingX or database keys in browser. In production use strong dashboard password and session secret, rotate credentials if exposed, and restrict public dashboard access if possible.

## Initial backfill and verification

1. Save the read-only BingX key and a Supabase Session Pooler `DATABASE_URL` as **GitHub Actions repository secrets**; secrets must never be committed. In Actions, manually run **Daily BingX trading archive** with `days=30` once (or up to 90, respecting BingX limits). Subsequent scheduled runs use `--days 3` for overlap and idempotent upserts, **once each day at 10:17 Vietnam time**. The Render web service separately needs the same `DATABASE_URL` configured in its Environment.
2. Check `sync_runs` for success and count orders/fills. Check `signal_links` mapping to `processed_targets`. Backfill may be incomplete if BingX no longer provides the oldest fills. The dashboard will label unmapped/incomplete history rather than infer attribution.
3. Reconcile 5-10 trades against BingX closed positions with TP 50%, trailing 50%, SL and liquidation cases. Validate fee sign convention and `fillHistory` response shape for the actual account before accepting PnL figures. **Trading bot stays unchanged throughout.**

## Commands for local offline smoke test

```
python -m pytest tests -q
COOKIE_SECURE=false DATABASE_URL=sqlite:///autotrade_live.db DB_SCHEMA= DASHBOARD_PASSWORD=local-password-long APP_SESSION_SECRET=local-session-secret-with-more-than-32-characters uvicorn autotrade_live.mobile:app --port 8000
```

The legacy Streamlit desktop app remains available via `streamlit run dashboard.py` against the same database, but the mobile PWA is the main deployment UI.

## Security and operating notes

- Never share trading-capable API keys. Separate BingX READ key only, if supported by current BingX settings.
- Request-level GET-only restrictions and rate limiting in `bingx.py`; no POST BingX trading endpoints.
- GitHub Actions collector and Render dashboard use the **same private Supabase schema**. Only Actions needs the BingX read-only credentials; no BingX key belongs in the dashboard web service.
- REST/GraphQL Data API does not expose private schema. RLS is enabled for defense in depth without anon/authenticated policies.
- PWA session cookie expires in 24h; server has basic in-memory login attempt limiting (use Render edge protections for stronger rate limits).
- All PnL is provisional until actual reconciliation. Funding, liquidations, complex simultaneous entries need broker-level checks.
