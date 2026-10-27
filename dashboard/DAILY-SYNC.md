# Hourly and daily BingX -> Supabase archive (read-only GitHub Actions)

This repository's default branch is `main`. Only this branch receives scheduled GitHub Actions workflows. The archive runs **every hour at minute 07 Vietnam time (00:07, 01:07, ... 23:07)**, reading a **3-hour overlapping window** of BingX history. The deep recheck still runs **daily at 10:17 Vietnam time (03:17 UTC)** and re-reads the previous **3 days** to catch late settlements. The cron `7 * * * *` works in UTC because Vietnam's offset is exactly 7 whole hours. Scheduled GitHub Actions can start late or be skipped; the daily job is retained for catch-up. This is not a clock-accurate exchange watcher. It collects historical BingX orders, fills, income, force close events, positions, and Combo IDs from the live bot's existing `public.processed_targets`. The collector is strictly BingX GET-only and has no order-placement methods. It does not modify the trading bot or its database tables.

## Required secrets (GitHub repo -> Settings -> Secrets and variables -> Actions -> New repository secret)
- `DATABASE_URL`: PostgreSQL connection string from **Supabase Connect -> Session Pooler (5432)**, with `?sslmode=require` (the same database as your live bot and Render dashboard).
- `BINGX_READ_API_KEY`: *separate* BingX API key with read-only permission.
- `BINGX_READ_API_SECRET`: secret associated with the read-only key.

Add values **inside GitHub Secrets only**, never in a public source file, issue, chat, or workflow logs. Existing BingX keys stored as env vars on the Render trading service are *not* automatically available to GitHub Actions. Never reuse a trading-enabled key for this archive.

`DB_SCHEMA=autotrade_analytics`, `BINGX_SYMBOLS=BTC-USDT,ETH-USDT`, and request pacing are defined as nonsecret workflow settings.

## Render mobile dashboard
The free Render web service `autotrade-live-dashboard` must separately have `DATABASE_URL` (same Supabase pooler string), `DB_SCHEMA=autotrade_analytics`, `DASHBOARD_PUBLIC_READONLY=true`, and `COOKIE_SECURE=true`. With public mode enabled, `DASHBOARD_PASSWORD` and `APP_SESSION_SECRET` are not used; setting the flag back to `false` restores password-based access (both secrets then required). **All trade metrics are viewable by anyone with the public Render URL**; no trading or BingX keys are returned. Its current settings may still point to branch `autotrade-v2-clean`; after copying dashboard code to `main`, change the branch in **that dashboard service only**, if desired. Do not alter the live `autotrade-v2-shadow` bot.

## First synchronization
1. In GitHub open **Actions -> Daily BingX trading archive -> Run workflow**; select **30 days** for initial backfill. The hourly schedule uses **3 hours**, and the daily recheck uses **3 days**, independently of this manual input. The manual run will fail clearly if any required secret is unset.
2. Check the Action's run result and `autotrade_analytics.sync_runs`; `status=success` plus stored `bingx_fills` and `bingx_orders` counts indicates collection. An empty dashboard does not mean the trade collection succeeded.
3. Verify 5-10 completed trades against the BingX account to validate fee signs, partial TP, trailing stop, liquidation and position matching before trusting PnL per Combo.

## Constraints
GitHub Actions schedules are best-effort: they can be delayed or missed. Public repository schedules can be disabled after 60 days without repository activity; review Actions periodically. The 3-hour overlapping hourly window and 3-day overlapping daily window allow reprocessing late records without duplicates, but neither is **an infinite archive catch-up**. The dashboard page itself refreshes from Supabase every 60 seconds and does not directly call BingX. To recover a longer gap, use the manual run with 7, 30, 60 or 90 days (subject to BingX API retention). Standard GitHub-hosted runners are free for public repositories; Render Cron Jobs are not used. If repo is made private, reassess GitHub Actions billing.

## PnL % and historical baseline

The dashboard reports estimated **entry-margin ROI**, NOT return on account equity:
`PnL % = 100 * (sum of realized fill PnL + signed fill fees) / sum(entry qty * entry price / entry leverage)`.
BingX order responses carry leverage such as `50X` or `100X`. For a closed position with
several entries, the denominator is the sum of their estimated entry margins; across
positions and Combos this is a **margin-weighted** ROI (never sum of individual percentages).
Funding, manually added margin, margin-mode differences and liquidations may change real
ROI; compare against BingX before relying on percentages. If entry leverage is unavailable,
the percentage displays `—` instead of inventing a denominator.

To **start fresh while keeping old exchange history for audit**, set
`DASHBOARD_START_AT=2026-10-25T17:40:50+07:00` in the separate Render dashboard service.
Only positions whose first entry fill is at or after the timestamp are included in
performance, open counts, charts and trade list. Old data is **not deleted** from Supabase;
the daily collector still rereads up to 3 days for idempotency. Reverting the flag to empty
allows historical, but potentially incomplete, positions to be shown again.
Changing this value never changes the live trading bot. The dashboard remains public
if DASHBOARD_PUBLIC_READONLY=true.
