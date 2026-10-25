"""Build a conservative closed-position ledger from real fills.

NEVER attribute a session containing unidentified/multiple Combo entries to one Combo.
This is a provisional position ledger, not guaranteed broker-level accounting.
"""
from __future__ import annotations
from collections import defaultdict

EPS = 1e-9


def build_trades(fills, orders, links, forced):
    order_by_id = {o.order_id: o for o in orders}
    combo_by_entry = {s.entry_order_id: s.combo for s in links}
    force_by_oid = {x.order_id: x.reason for x in forced if x.order_id}
    sessions, completed, unresolved = {}, [], []

    def new_book(symbol, position_side, ts):
        return dict(symbol=symbol, side=position_side, entry_ms=ts, exit_ms=None,
                    open_qty=0.0, gross=0.0, fees=0.0, entry_notional=0.0, entered_qty=0.0,
                    closed_notional=0.0, closed_qty=0.0, entries=set(), combos=set(),
                    exit_types=set(), liquidated=False, fill_count=0)

    def flush(book):
        combos = book['combos']
        combo = next(iter(combos)) if len(combos) == 1 and None not in combos else None
        denom = book['closed_qty']
        return dict(symbol=book['symbol'], side=book['side'], combo=combo,
                    label='MIXED' if len(combos) > 1 else ('UNMAPPED' if combo is None else combo),
                    entry_ms=book['entry_ms'], exit_ms=book['exit_ms'],
                    entry_price=(book['entry_notional'] / max(book['entered_qty'], EPS)) if book['entered_qty'] else 0,
                    exit_price=(book['closed_notional'] / max(denom, EPS)) if denom else 0,
                    pnl=round(book['gross'] + book['fees'], 8), gross=round(book['gross'], 8),
                    fees=round(book['fees'], 8), liquidated=book['liquidated'],
                    exit_types=', '.join(sorted(book['exit_types'])),
                    entry_order_ids=', '.join(sorted(book['entries'])),
                    fills=book['fill_count'], status='CLOSED' if book['open_qty'] <= EPS else 'OPEN',
                    remaining_qty=book['open_qty'])

    for trade in sorted(fills, key=lambda x: (x.timestamp_ms, x.trade_id)):
        order = order_by_id.get(trade.order_id)
        position_side = trade.position_side or (order.position_side if order else '')
        position_side = position_side.upper()
        side = trade.side.upper()
        qty = float(trade.qty or 0)
        if qty <= 0 or side not in ('BUY', 'SELL'):
            unresolved.append({'trade_id': trade.trade_id, 'reason': 'Missing quantity or side'})
            continue
        if position_side not in ('LONG', 'SHORT'):
            candidates = [pos for pos in ('LONG', 'SHORT') if
                          sessions.get((trade.symbol, pos)) and sessions[(trade.symbol, pos)]['open_qty'] > EPS]
            closing_candidates = [pos for pos in candidates if (pos == 'LONG' and side == 'SELL') or
                                  (pos == 'SHORT' and side == 'BUY')]
            if len(closing_candidates) == 1:
                position_side = closing_candidates[0]
            elif not candidates and trade.order_id in combo_by_entry:
                position_side = 'LONG' if side == 'BUY' else 'SHORT'
            else:
                unresolved.append({'trade_id': trade.trade_id, 'reason': 'Ambiguous positionSide'})
                continue
        key = (trade.symbol, position_side)
        opening = (position_side == 'LONG' and side == 'BUY') or (position_side == 'SHORT' and side == 'SELL')
        if key not in sessions:
            sessions[key] = new_book(trade.symbol, position_side, trade.timestamp_ms)
        b = sessions[key]
        b['fill_count'] += 1
        b['gross'] += trade.realized_pnl
        b['fees'] += trade.fee  # BingX documents negative fees for costs.
        if opening:
            b['open_qty'] += qty
            b['entries'].add(trade.order_id)
            b['combos'].add(combo_by_entry.get(trade.order_id))
            b['entry_notional'] += qty * trade.price
            b['entered_qty'] += qty
        else:
            if b['open_qty'] <= EPS:
                unresolved.append({'trade_id': trade.trade_id, 'reason': 'Exit without matched entry; incomplete backfill?'})
                # Remove the fee/PnL-only book to avoid treating an exit as a trade.
                sessions.pop(key, None)
                continue
            if qty > b['open_qty'] + EPS:
                unresolved.append({'trade_id': trade.trade_id, 'reason': 'Close quantity larger than open quantity (PnL needs review)'})
            effective_qty = min(qty, b['open_qty'])
            b['closed_qty'] += effective_qty
            b['closed_notional'] += effective_qty * trade.price
            b['open_qty'] = max(0, b['open_qty'] - effective_qty)
            b['exit_types'].add(order.type if order else 'UNKNOWN')
            b['liquidated'] |= trade.order_id in force_by_oid
            b['exit_ms'] = trade.timestamp_ms
            if b['open_qty'] <= EPS:
                completed.append(flush(b))
                sessions.pop(key, None)

    open_trades = [flush(book) for book in sessions.values() if book['open_qty'] > EPS]
    return completed, open_trades, unresolved


def summarize(trades):
    grouped = defaultdict(list)
    for trade in trades:
        if trade['status'] == 'CLOSED' and trade['combo']:
            grouped[trade['combo']].append(trade)
    results = []
    for combo, rows in sorted(grouped.items()):
        gains = [r['pnl'] for r in rows if r['pnl'] > 0]
        losses = [r['pnl'] for r in rows if r['pnl'] < 0]
        results.append({'Combo': combo, 'Số lệnh': len(rows),
                        'Win rate %': round(100 * len(gains) / len(rows), 2),
                        'PnL ròng USDT': round(sum(x['pnl'] for x in rows), 4),
                        'Profit factor': round(sum(gains) / abs(sum(losses)), 2) if losses else None,
                        'Thanh lý': sum(x['liquidated'] for x in rows)})
    return results
