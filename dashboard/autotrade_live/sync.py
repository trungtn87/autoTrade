"""Fetch real BingX history and persist without modifying Autotrade orders."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from .bingx import BingXReadClient, fetch_window_split, paged
from .db import Fill, ForceOrder, Income, Order, PositionHistory, SyncRun, get_engine, get_sessionmaker, init_db

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
        try:
            if row.get(key) is not None:
                return int(row[key])
        except (TypeError, ValueError):
            pass
    return 0


def upsert_rows(db, model, rows, make):
    for raw in rows:
        db.merge(model(**make(raw)))


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
    return dict(trade_id=trade_id, order_id=ident(a, 'orderID', 'orderId'),
                symbol=s(a.get('symbol')), side=s(a.get('side')).upper(),
                position_side=s(a.get('positionSide')).upper(),
                qty=f(a.get('qty', a.get('quantity', a.get('executedQty')))),
                price=f(a.get('price')), realized_pnl=f(a.get('realizedPnl', a.get('realizedProfit'))),
                fee=f(a.get('fee', a.get('commission'))),
                timestamp_ms=int_ms(a, 'time', 'filledTime', 'filledTm', 'fillTime', 'createTime', 'closeTime'), raw=a)


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


def sync(days=1, symbols=None, now_ms=None):
    if days <= 0 or days > 90:
        raise ValueError('days must be 1..90 (income availability is limited)')
    symbols = symbols or [x.strip().upper() for x in os.getenv('BINGX_SYMBOLS', 'BTC-USDT,ETH-USDT').split(',') if x.strip()]
    if not symbols or any('-' not in x for x in symbols):
        raise ValueError('BINGX_SYMBOLS must be comma-separated, e.g. BTC-USDT,ETH-USDT')
    end = now_ms or int(time.time() * 1000)
    begin = end - int(days * DAY_MS)
    engine = get_engine()
    init_db(engine)
    Session = get_sessionmaker(engine)
    client = BingXReadClient()
    totals = dict(orders=0, fills=0, income=0, forced=0, positions=0)
    with Session() as db:
        run = SyncRun(status='running', details=f'symbols={symbols}; days={days}')
        db.add(run)
        db.commit()
        run_id = run.id
    try:
        # Windows are <=1 day, safely within order-history's 7-day maximum.
        for symbol in symbols:
            for start in range(begin, end + 1, DAY_MS):
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
                with Session.begin() as db:
                    upsert_rows(db, Order, orders, as_order)
                    upsert_rows(db, Fill, fills, as_fill)
                    upsert_rows(db, Income, incomes, as_income)
                    for raw, reason in forces:
                        db.merge(ForceOrder(**as_force(raw, reason)))
                    upsert_rows(db, PositionHistory, positions, as_position)
                for key, values in [('orders', orders), ('fills', fills), ('income', incomes),
                                    ('forced', forces), ('positions', positions)]:
                    totals[key] += len(values)
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
            run.details = f'{type(exc).__name__}: {str(exc)[:450]}'
        raise
    return totals


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Read-only BingX history collector')
    p.add_argument('--days', type=int, default=1, help='backfill window; maximum 90')
    args = p.parse_args()
    print(json.dumps(sync(days=args.days), indent=2))
