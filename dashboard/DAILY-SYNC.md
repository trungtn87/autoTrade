# Daily BingX -> Supabase archive (GitHub Actions Free for standard runners on public repos)

This repository's default branch is `main`. Only this branch receives scheduled GitHub Actions workflows. This job runs **daily at 10:17 Asia/Bangkok (03:17 UTC)** and re-reads the prior **3 days** to avoid missing late settlements. It collects historical BingX orders, fills, income, force close events, positions, and Combo IDs from the live bot's existing `public.processed_targets`. The collector is strictly BingX GET-only and has no order-placement methods. It does not modify the trading bot or its database tables.

## Required secrets (GitHub repo -> Settings -> Secrets and variables -> Actions -> New repository secret)
- `DATABASE_URL`: PostgreSQL connection string from **Supabase Connect -> Session Pooler (5432)**, with `?sslmode=require` (the same database as your live bot and Render dashboard).
- `BINGX_READ_API_KEY`: *separate* BingX API key with read-only permission.
- `BINGX_READ_API_SECRET`: secret associated with the read-only key.

Add values **inside GitHub Secrets only**, never in a public source file, issue, chat, or workflow logs. Existing BingX keys stored as env vars on the Render trading service are *not* automatically available to GitHub Actions. Never reuse a trading-enabled key for this archive.

`DB_SCHEMA=autotrade_analytics`, `BINGX_SYMBOLS=BTC-USDT,ETH-USDT`, and request pacing are defined as nonsecret workflow settings.

## Render mobile dashboard
The free Render web service `autotrade-live-dashboard` must separately have `DATABASE_URL` (same Supabase pooler string), `DB_SCHEMA=autotrade_analytics`, `DASHBOARD_PASSWORD`, `APP_SESSION_SECRET` and `COOKIE_SECURE=true`. Its current settings may still point to branch `autotrade-v2-clean`; after copying dashboard code to `main`, change the branch in **that dashboard service only**, if desired. Do not alter the live `autotrade-v2-shadow` bot.

## First synchronization
1. In GitHub open **Actions -> Daily BingX trading archive -> Run workflow**; select **30 days** for initial backfill. The schedule uses **3 days** independently of this input. The manual run will fail clearly if any required secret is unset.
2. Check the Action's run result and `autotrade_analytics.sync_runs`; `status=success` plus stored `bingx_fills` and `bingx_orders` counts indicates collection. An empty dashboard does not mean the trade collection succeeded.
3. Verify 5-10 completed trades against the BingX account to validate fee signs, partial TP, trailing stop, liquidation and position matching before trusting PnL per Combo.

## Constraints
GitHub Actions schedules are best-effort: they can be delayed or missed. Public repository schedules can be disabled after 60 days without repository activity; review Actions periodically. The 3-day overlap allows reprocessing late records without duplicates, but it is **not an infinite archive catch-up**. To recover a longer gap, use the manual run with 7, 30, 60 or 90 days (subject to BingX API retention). Standard GitHub-hosted runners are free for public repositories; Render Cron Jobs are not used. If repo is made private, reassess GitHub Actions billing.
