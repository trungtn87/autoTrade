# Move BingX read-only archive from GitHub Actions to Render Cron

## What changes

The collector already lives in `dashboard/autotrade_live/collect.py`. Render runs the
same GET-only code with `python -m autotrade_live.render_archive`. The bot's live
BingX order pipeline (`autotrade-v2-shadow`) is **not** touched. The existing
dashboard web service can remain on Render Free and sleep normally; the separate
Render Cron Job does not need the dashboard to be awake.

**A Render Cron Job is paid** (minimum USD 1/month per cron service, plus usage).
Adding the Blueprint file to GitHub **does not create the paid service**.
Provision it in your Render workspace only after approving billing.

## Installation

1. In Render choose **New > Blueprint**, link repository
   `trungtn87/autoTrade`, branch `main`. Set Blueprint path to
   `dashboard/render-archive.yaml` (not the repository-root `render.yaml`,
   which belongs to another service).
2. Review the single service `autotrade-bingx-readonly-archive` before
   creating it. This is a **Cron Job**, not a new trading web service.
3. Set secrets **on this Cron service only**, never in GitHub, chat or source:
   - `DATABASE_URL`: existing Supabase Postgres session-pooler URL (5432)
   - `BINGX_READ_API_KEY`, `BINGX_READ_API_SECRET`: separate read-only BingX key
     **without trading/withdrawal permission**
   - `DB_SCHEMA=autotrade_analytics` is already set by Blueprint
4. Use **Trigger Run** once. Check the Cron run succeeded and Supabase
   `autotrade_analytics.sync_runs` has a new `status=success` row.
   Check `bingx_fills` and dashboard exit counts against the exchange.
5. Only **after** Render shows a successful sync, disable the `schedule:`
   section of `.github/workflows/daily-bingx-analytics.yml` (keep
   `workflow_dispatch:` if you want an emergency/manual backfill).
   This avoids unnecessary parallel BingX API requests.

## Schedule and safety

- Schedule: `7 * * * *` UTC, equivalent to **:07 every hour in Vietnam**.
- Normally fetches a 3-hour overlap; if the last successful sync was delayed,
  it extends the overlap up to 24 hours; for >24-hour downtime it does a
  bounded 3-day catch-up.
- Once a day, around **10:07 Vietnam time**, the same Cron Job fetches 3 days
  of history to reconcile delayed settlements (previous GitHub schedule used
  10:17). Only one paid Cron service is needed.
- Completed sync metadata lives in Supabase, not on Render ephemeral disk.
- Collector uses a Postgres advisory transaction lock to avoid overlap with
  updated GitHub collector. Render also guarantees only one active run per
  Cron service.
- Collector has **no order placement/cancellation methods** and does not alter
  the trading bot's TP/SL/trailing or cleanup loops.
- If the archive has not run for over 3 days, use a controlled manual
  backfill to restore older history. BingX's own retention limits still apply.

## Verification SQL (read-only)

```sql
SELECT started_at AT TIME ZONE 'Asia/Ho_Chi_Minh' AS started_vn,
       finished_at AT TIME ZONE 'Asia/Ho_Chi_Minh' AS finished_vn,
       status, details
FROM autotrade_analytics.sync_runs
ORDER BY id DESC
LIMIT 10;
```

## Why not a background timer inside Render Free?

Render Free web instances sleep after 15 minutes without inbound traffic.
A Python timer / APScheduler running inside a sleeping instance **does not run**,
even if the source code is correct. An external Google Apps Script ping can
wake the website for viewing, but should not be a prerequisite for collecting
trade history. A dedicated Render Cron Job is the independent solution.
