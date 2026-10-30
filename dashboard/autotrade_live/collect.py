"""Standalone read-only periodic sync: BingX executions + V2 Combo link bridge."""
import argparse
import json
from .bridge import refresh_signal_links
from sqlalchemy import select, text
from .db import Fill, get_engine, get_sessionmaker, init_db
from .sync import as_fill, sync


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


def collect(days=2, hours=None):
    engine = get_engine()
    try:
        init_db(engine)
        with engine.connect() as conn:
            locked = True
            if engine.dialect.name == 'postgresql':
                locked = bool(conn.execute(text(LOCK_SQL)).scalar())
            if not locked:
                # Another collector is already reading BingX. No duplicate REST load.
                return {'skipped': True, 'reason': 'archive_locked'}
            # The transaction-scoped lock is held until this connection
            # closes; safe with both Supabase session and transaction poolers.
            mapped = refresh_signal_links(engine)
            repaired = repair_legacy_fills(engine)
            bingx = sync(hours=hours) if hours is not None else sync(days=days)
            return {'combo_links': mapped, 'repaired_historical_fills': repaired, 'bingx': bingx}
    finally:
        engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    window = parser.add_mutually_exclusive_group()
    window.add_argument('--days', type=int, default=2)
    window.add_argument('--hours', type=int, help='hourly overlap window (1..24)')
    args = parser.parse_args()
    print(json.dumps(collect(days=args.days, hours=args.hours), ensure_ascii=False))
