# BingX archive on the existing Render Free dashboard (wake-up script)

The **recommended free setup** uses the already-running dashboard web service and
your existing Google Apps Script/monitor that wakes that **dashboard** URL.
A separate paid Render Cron Job is **not required**.

## What runs where

- GitHub: source code + optional emergency manual backfill only, once cutover is verified.
- Render **dashboard**: FastAPI lifespan starts the optional
  `autotrade_live.web_scheduler.archive_scheduler_loop()`.
- Render **trading bot**: **unchanged** — no additional GET/POST BingX traffic.
- BingX: GET-only read-history client, storing orders, fills, fees and Combo mappings.
- Supabase: existing `autotrade_analytics` tables and `sync_runs`; no new schema.

The backend scheduler never blocks `GET /health` or the mobile UI:
its BingX collector runs on a separate worker thread.
The existing `/health` endpoint is still valid and continues returning HTTP 200.

## Enable on the EXISTING dashboard Render web service

Render Dashboard > `autotrade-live-dashboard` > Environment:
```
DASHBOARD_ARCHIVE_ENABLED=true
BINGX_READ_API_KEY=(read-only key, set in Render UI)
BINGX_READ_API_SECRET=(read-only secret, set in Render UI)
BINGX_REQUEST_DELAY=0.6
BINGX_SYMBOLS=BTC-USDT,ETH-USDT
DB_SCHEMA=autotrade_analytics
DATABASE_URL=(existing Supabase Postgres pooler URL)
```

**Do not copy a trading-enabled BingX key into the dashboard.** Create a
separate key with only historical read permission and no trade/withdraw access.
Never commit or paste these secrets into chat.

The Render service must deploy the updated `main` branch and launch:
```
uvicorn autotrade_live.mobile:app --host 0.0.0.0 --port $PORT
```
from the `dashboard/` root directory. If the dashboard service follows another
branch, update *that dashboard service only*, not the live trading bot.

## Wake-up URL and cadence

The existing Google Apps Script must ping the **dashboard** endpoint:
```text
https://<actual-dashboard-render-host>.onrender.com/health
```
The actual hostname comes from Render's dashboard web service settings.
The trading-bot `autotrade-v2-shadow.onrender.com/health` is a *different* app.
The wake-up script can ping every 5-10 minutes (or at least hourly after
:07), which keeps Render awake or wakes it promptly. Do not include secret
BingX values in the script.

- Scheduled slot is **HH:07 UTC = HH:07 Vietnam time**. A process awake at
  that moment starts an archive check.
- If Render is asleep at :07, it cannot run. On next wake-up after :07,
  the worker checks Supabase and **runs catch-up for missed records**.
- Normal lookback is 3 hours. A delayed run extends to at most 24 hours;
  a gap over 24 hours performs a bounded 3-day reconciliation.
- At/after 10:07 Vietnam time, the first check of the day also runs a
  3-day reconciliation for late fills and funding.
- Failed run retries wait at least 15 minutes (no tight BingX polling).
- Successful hourly checks are recognized via `sync_runs` and not repeated
  in the same hour. PostgreSQL advisory locking prevents simultaneous
  archive collectors from running concurrently.
- If both Render and GitHub collectors run, they share the same advisory lock,
  but sequential runs can still duplicate BingX GET work. After one verified
  Render sync, **disable GitHub's schedule**, retaining its manual workflow
  only if needed for emergency backfill.

## Verify before disabling GitHub schedule

1. Wake the **dashboard** `/health` endpoint and confirm the Render dashboard
   service deployed `main` with `DASHBOARD_ARCHIVE_ENABLED=true`.
2. Check a new completed Supabase row with this read-only query:
```sql
SELECT started_at AT TIME ZONE 'Asia/Ho_Chi_Minh' AS started_vn,
       finished_at AT TIME ZONE 'Asia/Ho_Chi_Minh' AS finished_vn,
       status, details
FROM autotrade_analytics.sync_runs
ORDER BY id DESC LIMIT 10;
```
3. Compare the latest BingX exit and dashboard result.
4. Then remove the GitHub schedule from
   `.github/workflows/daily-bingx-analytics.yml` — manual dispatch can remain.

## Important limits

Render Free sleeps after inactivity. A background Python timer is not a
guaranteed independent schedule when the web service is asleep. The wake-up
script is therefore **a required external trigger** for this free solution.
It is independent of GitHub Actions, but **not** independent of the wake-up
script. If you later need a hard 24/7 schedule with no external wake script,
a separate Render Cron Job is the paid alternative available in
`dashboard/render-archive.yaml` (not active by virtue of that file).
