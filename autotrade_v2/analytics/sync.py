"""Fetch real BingX history and persist without modifying Autotrade orders."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from sqlalchemy import select
from .bingx import BingXReadClient, fetch_window_split, paged
from dashboard.autotrade_live.db import Fill, ForceOrder, Income, Order, PositionHistory, SyncRun, get_engine, get_sessionmaker, init_db

DAY_MS = 86_400_000


def f(x):
    try:
        return float(x or 0)
    except (ValueError, TypeError):
        return 0.0


def s(x):
    return str(x) if x is not None else ''


def ident(row, *fields):
    for field in fields:
        if row.get(field) is not None and str(row.get(field)):
            return s(row[field])
    return ''


def digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=True).encode()).hexdigest()[:30]


def int_ms(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is None or value == '':
            continue
        try:
            return int(value)
        except (ValueError, TypeError):
            try:
                dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    raise ValueError('BingX timestamp missing timezone')
                return int(dt.timestamp() * 1000)
            except (ValueError, TypeError, OverflowError):
                pass
    return 0


def upsert_rows(db, model, rows, make):
    counts = dict(inserted=0, updated=0, unchanged=0)
    for raw in rows:
        data = make(raw)
        identity = tuple(data[col.key] for col in model.__mapper__.primary_key)
        old = db.get(model, identity)
        if old is None:
            db.add(model(**data))
            counts['inserted'] += 1
        elif all(getattr(old, key) == value for key, value in data.items()):
            counts['unchanged'] += 1
        else:
            for key, value in data.items():
                setattr(old, key, value)
            counts['updated'] += 1
    return counts


def as_order(a):
    oid = ident(a, 'orderID', 'orderId')
    if not oid:
        raise ValueError('BingX order record without orderId')
    return dict(order_id=oid, symbol=s(a.get('symbol')), side=s(a.get('side')).upper(),
                position_side=s(a.get('positionSide')).upper(), type=s(a.get('type')).upper(),
                status=s(a.get('status')), price=f(a.get('price')), avg_price=f(a.get('avgPrice')),
                quantity=f(a.get('origQty', a.get('quantity'))), filled_qty=f(a.get('executedQty')),
                created_ms=int_ms(a, 'time', 'createTime'), updated_ms=int_ms(a, 'updateTime', 'time'), raw=a)


def as_fill(a):
    trade_id = ident(a, 'tradeId', 'fillId', 'id')
    if not trade_id:
        raise ValueError('BingX fill record without tradeId/fillId')
    timestamp = int_ms(a, 'time', 'filledTime', 'filledTm', 'fillTime', 'createTime', 'closeTime')
    if timestamp <= 0:
        raise ValueError('BingX fill missing valid timestamp')
    return dict(trade_id=trade_id, order_id=ident(a, 'orderID', 'orderId'),
                symbol=s(a.get('symbol')), side=s(a.get('side')).upper(),
                position_side=s(a.get('positionSide')).upper(),
                qty=f(a.get('qty', a.get('quantity', a.get('executedQty')))),
                price=f(a.get('price')), realized_pnl=f(a.get('realizedPnl', a.get('realisedPNL', a.get('realizedProfit')))),
                fee=f(a.get('fee', a.get('commission'))),
                timestamp_ms=timestamp, raw=a)


def as_income(a):
    transaction_id = ident(a, 'tranId', 'tradeId', 'id') or digest(a)
    return dict(income_key='|'.join([transaction_id, s(a.get('incomeType')), s(a.get('asset')), s(a.get('symbol'))]),
                symbol=s(a.get('symbol')), income_type=s(a.get('incomeType')),
                amount=f(a.get('income')), asset=s(a.get('asset')), timestamp_ms=int_ms(a, 'time'),
                trade_id=ident(a, 'tradeId'), raw=a)


def as_force(a, reason):
    oid = ident(a, 'orderID', 'orderId')
    key = reason + '|' + (oid or digest(a))
    return dict(force_key=key, order_id=oid, symbol=s(a.get('symbol')),
                reason=reason, timestamp_ms=int_ms(a, 'time', 'updateTime'), raw=a)


def as_position(a):
    pid = ident(a, 'positionId', 'id') or digest(a)
    return dict(position_key=s(a.get('symbol')) + '|' + pid, symbol=s(a.get('symbol')),
                timestamp_ms=int_ms(a, 'closeTime', 'updateTime', 'time'), raw=a)


def sync(days=1, symbols=None, now_ms=None, hours=None, before_request=None, resume=True, resume_any_window=False):
    if hours is None:
        if days <= 0 or days > 90:
            raise ValueError('days must be 1..90 (income availability is limited)')
        lookback_ms = int(days * DAY_MS)
        lookback_label = f'days={days}'
    else:
        if not isinstance(hours, int) or not 1 <= hours <= 24:
            raise ValueError('hours must be 1..24 for hourly incremental sync')
        lookback_ms = hours * 60 * 60 * 1000
        lookback_label = f'hours={hours}'
    symbols = symbols or [x.strip().upper() for x in os.getenv('BINGX_SYMBOLS', 'BTC-USDT,ETH-USDT').split(',') if x.strip()]
    if not symbols or any('-' not in x for x in symbols):
        raise ValueError('BINGX_SYMBOLS must be comma-separated, e.g. BTC-USDT,ETH-USDT')
    end = now_ms or int(time.time() * 1000)
    begin = end - lookback_ms
    engine = get_engine()
    init_db(engine)
    Session = get_sessionmaker(engine)
    totals = dict(stage='raw_sync', lookback=lookback_label, symbols=symbols,
                  begin_ms=begin, end_ms=end, checkpoints={}, changes={},
                  orders=0, fills=0, income=0, forced=0, positions=0)
    with Session.begin() as db:
        parent_run = None
        if resume:
            previous = db.scalars(select(SyncRun).where(
                SyncRun.status.in_(['failed', 'running', 'interrupted']),
                SyncRun.details.like('%"stage": "raw_sync"%')
            ).order_by(SyncRun.id.desc()).limit(1000)).all()
            for old in previous:
                try:
                    saved = json.loads(old.details)
                except (TypeError, ValueError):
                    continue
                if (not isinstance(saved, dict) or saved.get('resumed_by') or saved.get('stage') != 'raw_sync'
                    or (not resume_any_window and saved.get('lookback') != lookback_label) or saved.get('symbols') != symbols):
                    continue
                if saved['begin_ms'] < end - 90*DAY_MS:
                    raise RuntimeError('Interrupted archive exceeds retention; manual review required')
                totals = dict(saved)
                totals['resumed_from'] = old.id
                parent_run = old
                totals.pop('error_class', None)
                begin, end = int(saved['begin_ms']), int(saved['end_ms'])
                if old.status == 'running':
                    old.status = 'interrupted'
                    old.finished_at = datetime.now(timezone.utc)
                break
        run = SyncRun(status='running', details=json.dumps(totals))
        db.add(run)
        db.flush()
        run_id = run.id
        if parent_run is not None:
            parent_data = json.loads(parent_run.details)
            parent_data['resumed_by'] = run_id
            parent_run.details = json.dumps(parent_data)
    try:
        client = BingXReadClient()
        client.before_request = before_request
        # Windows are <=1 day, safely within order-history's 7-day maximum.
        for symbol in symbols:
            cursor = int(totals['checkpoints'].get(symbol, begin - 1))
            if not begin - 1 <= cursor <= end:
                raise ValueError('Invalid persisted archive cursor')
            for start in range(cursor + 1, end + 1, DAY_MS):
                stop = min(end, start + DAY_MS - 1)
                if stop < start:
                    continue
                orders = fetch_window_split(
                    client, '/openApi/swap/v2/trade/allOrders',
                    {'symbol': symbol, 'startTime': start, 'endTime': stop, 'limit': 1000},
                    ('orders', 'list'), 1000, 'startTime', 'endTime')
                # The paginated API avoids silent fill truncation at 1000 rows.
                fills = paged(client, '/openApi/swap/v2/trade/fillHistory',
                              {'symbol': symbol, 'startTs': start, 'endTs': stop},
                              ('fill_history_orders', 'fill_orders', 'list', 'fills'), 500)
                incomes = fetch_window_split(
                    client, '/openApi/swap/v2/user/income',
                    {'symbol': symbol, 'startTime': start, 'endTime': stop, 'limit': 1000},
                    ('list', 'rows', 'incomes'), 1000, 'startTime', 'endTime')
                forces = []
                for reason in ('LIQUIDATION', 'ADL'):
                    items = fetch_window_split(
                        client, '/openApi/swap/v2/trade/forceOrders',
                        {'symbol': symbol, 'autoCloseType': reason, 'startTime': start,
                         'endTime': stop, 'limit': 100},
                        ('orders', 'list'), 100, 'startTime', 'endTime')
                    forces += [(item, reason) for item in items]
                positions = paged(client, '/openApi/swap/v1/trade/positionHistory',
                                   {'symbol': symbol, 'startTs': start, 'endTs': stop},
                                   ('positionHistory', 'list'), 100)
                candidate = json.loads(json.dumps(totals))
                with Session.begin() as db:
                    for key, model, values, make in (
                        ('orders', Order, orders, as_order),
                        ('fills', Fill, fills, as_fill),
                        ('income', Income, incomes, as_income),
                        ('forced', ForceOrder, forces, lambda row: as_force(*row)),
                        ('positions', PositionHistory, positions, as_position),
                    ):
                        changes = upsert_rows(db, model, values, make)
                        bucket = candidate['changes'].setdefault(key, dict(inserted=0, updated=0, unchanged=0))
                        for kind, count in changes.items():
                            bucket[kind] += count
                        candidate[key] += len(values)
                    candidate['checkpoints'][symbol] = stop
                    # Archive writes and progress commit atomically. A crash
                    # either replays this window safely or resumes after it.
                    db.get(SyncRun, run_id).details = json.dumps(candidate)
                totals = candidate
        # Explicit code=0, data=null windows are counted for reconciliation.
        totals['null_data_windows'] = dict(getattr(client, 'null_data_counts', {}))
        with Session.begin() as db:
            run = db.get(SyncRun, run_id)
            run.finished_at = datetime.now(timezone.utc)
            run.status = 'success'
            run.details = json.dumps(totals)
    except Exception as exc:
        with Session.begin() as db:
            run = db.get(SyncRun, run_id)
            run.finished_at = datetime.now(timezone.utc)
            run.status = 'failed'
            run.details = json.dumps({**totals, 'error_class':type(exc).__name__})
        raise
    finally:
        engine.dispose()
    return totals


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Read-only BingX history collector')
    window = p.add_mutually_exclusive_group()
    window.add_argument('--days', type=int, default=1, help='backfill window; maximum 90')
    window.add_argument('--hours', type=int, help='incremental window; 1..24 hours')
    args = p.parse_args()
    from .collect import collect
    print(json.dumps(collect(days=args.days, hours=args.hours), indent=2))
