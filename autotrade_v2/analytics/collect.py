"""Standalone read-only periodic sync: BingX executions + V2 Combo link bridge."""
import argparse
import json
from datetime import datetime, timezone
from .bridge import refresh_signal_links
from sqlalchemy import select, text
from dashboard.autotrade_live.db import Fill, SyncRun, get_engine, get_sessionmaker, init_db
from .sync import as_fill, sync
from .results_store import reconcile_database


def repair_legacy_fills(engine):
    """Normalize previously archived fills without re-fetching BingX history."""
    Session = get_sessionmaker(engine)
    repaired = 0
    with Session.begin() as db:
        for old in db.scalars(select(Fill).where(Fill.timestamp_ms <= 0)).all():
            normalized = as_fill(old.raw)
            if normalized['trade_id'] != old.trade_id or normalized['symbol'] != old.symbol:
                raise ValueError('Archived BingX fill identity mismatch; manual reconciliation required')
            old.timestamp_ms = normalized['timestamp_ms']
            old.realized_pnl = normalized['realized_pnl']
            repaired += 1
    return repaired


# Advisory lock coordinates are reserved for the analytics collector only.
# The built-in archive scheduler and optional manual GitHub backfill share this lock.
LOCK_SQL = 'SELECT pg_try_advisory_xact_lock(2707, 20261027)'


PROCESS_STARTED_AT = datetime.now(timezone.utc)


def recover_interrupted_runs(engine):
    """Call only while owning the collector advisory transaction.

    A surviving collector would still own that lock. Older running records
    therefore belong to an interrupted process; preserve their JSON cursors.
    """
    Session = get_sessionmaker(engine)
    recovered = 0
    with Session.begin() as db:
        for run in db.scalars(select(SyncRun).where(
            SyncRun.status == 'running', SyncRun.started_at < PROCESS_STARTED_AT
        )).all():
            try:
                data = json.loads(run.details)
            except (ValueError, TypeError):
                data = None
            if isinstance(data, dict):
                run.details = json.dumps({**data, 'error_class':'InterruptedProcess'})
            else:
                run.details = 'stage=archive_attempt; error_class=InterruptedProcess'
            run.status = 'interrupted'
            run.finished_at = datetime.now(timezone.utc)
            recovered += 1
    return recovered


def acquire_archive_lock(conn, dialect_name):
    if dialect_name != 'postgresql':
        return True
    # This dedicated connection holds the archive transaction, not any engine
    # transaction. Do not let server idle timeout silently release the lock
    # while paced HTTP/window work uses separate connections.
    conn.execute(text("SET LOCAL idle_in_transaction_session_timeout = '0'"))
    return bool(conn.execute(text(LOCK_SQL)).scalar())


def collect(days=2, hours=None, resume_any_window=False):
    engine = get_engine()
    try:
        init_db(engine)
        with engine.connect() as conn:
            locked = acquire_archive_lock(conn, engine.dialect.name)
            if not locked:
                # Another collector is already reading BingX. No duplicate REST load.
                return {'skipped': True, 'reason': 'archive_locked'}
            # The transaction-scoped lock is held until this connection
            # closes; safe with both Supabase session and transaction poolers.
            interrupted = recover_interrupted_runs(engine)
            mapped = refresh_signal_links(engine)
            repaired = repair_legacy_fills(engine)
            # Heartbeat the lock-owning transaction before EACH HTTP attempt.
            # Connection loss aborts before further archive network requests.
            def lock_check():
                conn.execute(text('SELECT 1')).scalar()
            bingx = sync(hours=hours, before_request=lock_check, resume_any_window=resume_any_window) if hours is not None else sync(days=days, before_request=lock_check, resume_any_window=resume_any_window)
            from sqlalchemy import inspect
            reconciled = None
            if inspect(engine).has_table('trade_results', schema='autotrade_analytics' if engine.dialect.name == 'postgresql' else None):
                reconciled = reconcile_database(engine)
            return {'interrupted_runs': interrupted, 'trade_results': reconciled, 'combo_links': mapped, 'repaired_historical_fills': repaired, 'bingx': bingx}
    finally:
        engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    window = parser.add_mutually_exclusive_group()
    window.add_argument('--days', type=int, default=2)
    window.add_argument('--hours', type=int, help='hourly overlap window (1..24)')
    args = parser.parse_args()
    print(json.dumps(collect(days=args.days, hours=args.hours), ensure_ascii=False))
