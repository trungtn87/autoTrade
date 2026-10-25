"""Conservative read-only execution ledger.

Fills are grouped by the exchange's stable positionID where available; old
incomplete or multi-Combo sessions never get arbitrarily assigned.
PnL percentage is *estimated initial margin ROI*, not account performance.
"""
from __future__ import annotations
import re
from collections import defaultdict

EPS = 1e-9


def _position_id(order):
    raw = (getattr(order, 'raw', None) or {}) if order is not None else {}
    if not isinstance(raw, dict):
        return ''
    value = str(raw.get('positionID') or raw.get('positionId') or '').strip()
    return '' if value in ('0', 'None') else value


def _leverage(order):
    raw = (getattr(order, 'raw', None) or {}) if order is not None else {}
    if not isinstance(raw, dict):
        return None
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*[xX]?\s*', str(raw.get('leverage', '')))
    if not match:
        return None
    level = float(match.group(1))
    return level if level > 0 else None


def build_trades(fills, orders, links, forced):
    order_by_id = {o.order_id: o for o in orders}
    combo_by_entry = {s.entry_order_id: s.combo for s in links}
    force_by_oid = {x.order_id: x.reason for x in forced if x.order_id}
    sessions, completed, unresolved = {}, [], []

    def new_book(symbol, position_side, ts):
        return dict(symbol=symbol, side=position_side, entry_ms=ts, exit_ms=None,
                    open_qty=0.0, gross=0.0, fees=0.0, entry_notional=0.0, entered_qty=0.0,
                    margin_used=0.0, margin_known=True,
                    closed_notional=0.0, closed_qty=0.0, entries=set(), combos=set(),
                    exit_types=set(), liquidated=False, fill_count=0)

    def flush(book):
        combos = book['combos']
        combo = next(iter(combos)) if len(combos) == 1 and None not in combos else None
        denom = book['closed_qty']
        pnl = round(book['gross'] + book['fees'], 8)
        margin = book['margin_used'] if book['margin_known'] and book['margin_used'] > EPS else None
        return dict(symbol=book['symbol'], side=book['side'], combo=combo,
                    label='MIXED' if len(combos) > 1 else ('UNMAPPED' if combo is None else combo),
                    entry_ms=book['entry_ms'], exit_ms=book['exit_ms'],
                    entry_price=(book['entry_notional'] / max(book['entered_qty'], EPS)) if book['entered_qty'] else 0,
                    exit_price=(book['closed_notional'] / max(denom, EPS)) if denom else 0,
                    entry_notional_usdt=round(book['entry_notional'], 8),
                    margin_used_usdt=round(margin, 8) if margin is not None else None,
                    pnl_pct=round(100 * pnl / margin, 2) if margin is not None else None,
                    pnl=pnl, gross=round(book['gross'], 8),
                    fees=round(book['fees'], 8), liquidated=book['liquidated'],
                    exit_types=', '.join(sorted(book['exit_types'])),
                    entry_order_ids=', '.join(sorted(book['entries'])),
                    fills=book['fill_count'], status='CLOSED' if book['open_qty'] <= EPS else 'OPEN',
                    remaining_qty=book['open_qty'])

    for trade in sorted(fills, key=lambda x: (x.timestamp_ms, x.trade_id)):
        order = order_by_id.get(trade.order_id)
        position_side = (trade.position_side or (order.position_side if order else '')).upper()
        side = trade.side.upper()
        qty = float(trade.qty or 0)
        if qty <= 0 or side not in ('BUY', 'SELL') or trade.timestamp_ms <= 0:
            unresolved.append({'trade_id': trade.trade_id, 'timestamp_ms': trade.timestamp_ms,
                               'reason': 'Invalid quantity, side or timestamp'})
            continue
        pid = _position_id(order)
        if position_side not in ('LONG', 'SHORT'):
            candidates = [pos for pos in ('LONG', 'SHORT') if
                          any(book['open_qty'] > EPS for (sym, p, _), book in sessions.items()
                              if sym == trade.symbol and p == pos)]
            closing_candidates = [pos for pos in candidates if
                                  (pos == 'LONG' and side == 'SELL') or
                                  (pos == 'SHORT' and side == 'BUY')]
            if len(closing_candidates) == 1:
                position_side = closing_candidates[0]
            elif not candidates and trade.order_id in combo_by_entry:
                position_side = 'LONG' if side == 'BUY' else 'SHORT'
            else:
                unresolved.append({'trade_id': trade.trade_id, 'timestamp_ms': trade.timestamp_ms,
                                   'reason': 'Ambiguous positionSide'})
                continue
        key = (trade.symbol, position_side, pid)
        opening = (position_side == 'LONG' and side == 'BUY') or (position_side == 'SHORT' and side == 'SELL')
        if key not in sessions:
            if not opening:
                unresolved.append({'trade_id': trade.trade_id, 'timestamp_ms': trade.timestamp_ms,
                                   'reason': 'Exit without matched entry; incomplete backfill?'})
                continue
            sessions[key] = new_book(trade.symbol, position_side, trade.timestamp_ms)
        b = sessions[key]
        if not opening and b['open_qty'] <= EPS:
            unresolved.append({'trade_id': trade.trade_id, 'timestamp_ms': trade.timestamp_ms,
                               'reason': 'Exit without matched entry'})
            sessions.pop(key, None)
            continue
        b['fill_count'] += 1
        b['gross'] += float(trade.realized_pnl or 0)
        b['fees'] += float(trade.fee or 0)  # BingX negative fee means cost
        if opening:
            b['open_qty'] += qty
            b['entries'].add(trade.order_id)
            b['combos'].add(combo_by_entry.get(trade.order_id))
            notional = qty * float(trade.price or 0)
            b['entry_notional'] += notional
            b['entered_qty'] += qty
            leverage = _leverage(order)
            if leverage and notional > EPS:
                b['margin_used'] += notional / leverage
            else:
                b['margin_known'] = False
        else:
            if qty > b['open_qty'] + EPS:
                unresolved.append({'trade_id': trade.trade_id, 'timestamp_ms': trade.timestamp_ms,
                                   'reason': 'Close quantity larger than open quantity'})
                b['combos'].add(None)  # unsafe to attribute PnL of an over-close
            effective_qty = min(qty, b['open_qty'])
            b['closed_qty'] += effective_qty
            b['closed_notional'] += effective_qty * float(trade.price or 0)
            b['open_qty'] = max(0, b['open_qty'] - effective_qty)
            b['exit_types'].add(order.type if order else 'UNKNOWN')
            b['liquidated'] |= trade.order_id in force_by_oid
            b['exit_ms'] = trade.timestamp_ms
            if b['open_qty'] <= EPS:
                completed.append(flush(b))
                sessions.pop(key, None)

    open_trades = [flush(book) for book in sessions.values() if book['open_qty'] > EPS]
    return completed, open_trades, unresolved


def margin_roi(trades):
    """Weighted realized ROI: sum of PnL / sum of estimated entry margins."""
    if not trades:
        return None, None
    margins = [x.get('margin_used_usdt') for x in trades]
    if any(m is None or m <= EPS for m in margins):
        return None, None
    total = sum(margins)
    return round(100 * sum(x['pnl'] for x in trades) / total, 2), round(total, 6)


def summarize(trades):
    grouped = defaultdict(list)
    for trade in trades:
        if trade['status'] == 'CLOSED' and trade['combo']:
            grouped[trade['combo']].append(trade)
    results = []
    for combo, rows in sorted(grouped.items()):
        gains = [r['pnl'] for r in rows if r['pnl'] > 0]
        losses = [r['pnl'] for r in rows if r['pnl'] < 0]
        roi_pct, margin = margin_roi(rows)
        results.append({'Combo': combo, 'Số lệnh': len(rows),
                        'Win rate %': round(100 * len(gains) / len(rows), 2),
                        'PnL ròng USDT': round(sum(x['pnl'] for x in rows), 4),
                        'PnL %': roi_pct, 'Ký quỹ ước tính USDT': margin,
                        'Profit factor': round(sum(gains) / abs(sum(losses)), 2) if losses else None,
                        'Thanh lý': sum(x['liquidated'] for x in rows)})
    return results
