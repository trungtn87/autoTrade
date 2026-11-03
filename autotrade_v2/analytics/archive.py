"""V2 entrypoint for the existing GET-only BingX -> Supabase archive.

Run at 7 * * * * UTC. One job handles hourly overlap and a daily 3-day
reconciliation at 03:07 UTC (10:07 Vietnam), without GitHub Actions.
Owned by the V2 lifecycle; no execution API calls.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from .collect import collect
from dashboard.autotrade_live.db import get_engine

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

    if last_success is not None:
        gap = (now - last_success.astimezone(timezone.utc)).total_seconds() / 86400
        if gap > 90:
            raise RuntimeError('Archive gap exceeds provider retention; explicit recovery review required')
        if gap > 3:
            return 'days', min(90, math.ceil(gap) + 1)

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


def _run_once(now: datetime | None = None):
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
                WHERE status = 'success' AND details NOT LIKE '%stage=raw_only%'
            """)).scalar()
            last_daily = conn.execute(text("""
                SELECT max(finished_at) FROM autotrade_analytics.sync_runs
                WHERE status = 'success' AND details LIKE '%days=3%'
            """)).scalar()
    finally:
        engine.dispose()

    kind, length = plan_archive(now, last_success, last_daily)
    if kind == 'skip':
        from sqlalchemy import inspect
        from .results_store import reconcile_database
        engine = get_engine()
        try:
            from .collect import LOCK_SQL
            with engine.connect() as conn:
                if not conn.execute(text(LOCK_SQL)).scalar():
                    return {'skipped': True, 'reason': 'archive_locked'}
                if inspect(engine).has_table('trade_results', schema='autotrade_analytics'):
                    reconcile_database(engine)
        finally:
            engine.dispose()
        print('archive: skipped, Supabase already synchronized this hour', flush=True)
        return {'skipped': True}
    print(f'archive: {kind}={length}, GET-only BingX sync', flush=True)
    return collect(hours=length) if kind == 'hours' else collect(days=length)


def run_once(now: datetime | None = None):
    """Audit the whole attempt, including failures before sync() is entered.

    If PostgreSQL is unavailable, only a redacted local error is possible.
    Never fall back to a local SQLite file in production archive mode.
    """
    import logging
    from dashboard.autotrade_live.db import SyncRun, get_sessionmaker
    now = now or datetime.now(timezone.utc)
    engine = None
    run_id = None
    try:
        if os.getenv('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')):
            engine = get_engine()
            Session = get_sessionmaker(engine)
            with Session.begin() as db:
                run = SyncRun(started_at=now, status='running', details='stage=archive_attempt')
                db.add(run)
                db.flush()
                run_id = run.id
        result = _run_once(now)
        status = 'skipped' if result.get('skipped') else 'success'
        details = 'stage=archive_attempt; ' + ('reason=' + str(result.get('reason', 'fresh')) if result.get('skipped') else 'completed')
    except Exception as exc:
        status, details = 'failed', 'stage=archive_attempt; error_class=' + type(exc).__name__
        logging.getLogger(__name__).error('V2_ARCHIVE_ATTEMPT_FAILED error_class=%s', type(exc).__name__)
        raise
    finally:
        if engine is not None:
            try:
                if run_id is not None:
                    with Session.begin() as db:
                        run = db.get(SyncRun, run_id)
                        run.status, run.details = status, details
                        run.finished_at = datetime.now(timezone.utc)
            except Exception as exc:
                logging.getLogger(__name__).error('V2_ARCHIVE_AUDIT_FAILED error_class=%s', type(exc).__name__)
            finally:
                engine.dispose()
    return result


if __name__ == '__main__':
    try:
        run_once()
    except Exception as exc:
        # Never print exception text: provider errors could carry account details.
        print(f'archive: failed ({type(exc).__name__}); inspect private sync_runs and logs', file=sys.stderr)
        raise SystemExit(1) from None
