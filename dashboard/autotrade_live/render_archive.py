"""Render Cron entrypoint for the existing GET-only BingX -> Supabase archive.

Run at 7 * * * * UTC. One job handles hourly overlap and a daily 3-day
reconciliation at 03:07 UTC (10:07 Vietnam), without GitHub Actions.
Never invoke this module from the trading service.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from .collect import collect
from .db import get_engine

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def plan_archive(now: datetime, last_success: datetime | None,
                 last_daily: datetime | None) -> tuple[str, int]:
    """Pure scheduler: ('skip', 0), ('hours', n) or ('days', 3)."""
    if now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    now = now.astimezone(timezone.utc)
    tick = now.replace(minute=7, second=0, microsecond=0)
    if now < tick:
        tick -= HOUR

    deep_tick = now.replace(hour=3, minute=7, second=0, microsecond=0)
    if now >= deep_tick and (
        last_daily is None or last_daily.astimezone(timezone.utc) < deep_tick
    ):
        return 'days', 3

    if last_success is not None:
        last_success = last_success.astimezone(timezone.utc)
        if last_success >= tick:
            return 'skip', 0
        elapsed_hours = max(0, (now - last_success).total_seconds() / 3600)
        if elapsed_hours > 24:
            # One bounded catch-up. Gaps older than 3 days need manual backfill.
            return 'days', 3
        return 'hours', min(24, max(3, math.ceil(elapsed_hours) + 1))
    return 'days', 3


def run_once(now: datetime | None = None):
    if not (os.getenv('BINGX_READ_API_KEY') and os.getenv('BINGX_READ_API_SECRET')):
        raise RuntimeError('History archive requires a separate read-only BingX API key/secret')
    if not os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')):
        raise RuntimeError('History archive requires persistent Supabase PostgreSQL DATABASE_URL')
    if os.getenv('DB_SCHEMA') != 'autotrade_analytics':
        raise RuntimeError('Set DB_SCHEMA=autotrade_analytics')

    now = now or datetime.now(timezone.utc)
    engine = get_engine()
    try:
        with engine.connect() as conn:
            last_success = conn.execute(text("""
                SELECT max(finished_at) FROM autotrade_analytics.sync_runs
                WHERE status = 'success'
            """)).scalar()
            last_daily = conn.execute(text("""
                SELECT max(finished_at) FROM autotrade_analytics.sync_runs
                WHERE status = 'success' AND details LIKE '%days=3%'
            """)).scalar()
    finally:
        engine.dispose()

    kind, length = plan_archive(now, last_success, last_daily)
    if kind == 'skip':
        print('archive: skipped, Supabase already synchronized this hour', flush=True)
        return {'skipped': True}
    print(f'archive: {kind}={length}, GET-only BingX sync', flush=True)
    return collect(hours=length) if kind == 'hours' else collect(days=length)


if __name__ == '__main__':
    try:
        run_once()
    except Exception as exc:
        # Never print exception text: provider errors could carry account details.
        print(f'archive: failed ({type(exc).__name__}); inspect private sync_runs and logs', file=sys.stderr)
        raise SystemExit(1) from None
