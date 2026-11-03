"""Conservative, idempotent V2 results from archived exchange identities.

Never estimates PnL from strategy prices or allocates a shared position's PnL.
"""
from collections import Counter, defaultdict
from decimal import Decimal


def reconcile(specs, orders, fills, exit_events):
    orders = {str(o.order_id): o for o in orders}
    by_order = defaultdict(list)
    events = defaultdict(list)
    for fill in fills:
        by_order[str(fill.order_id)].append(fill)
    for event in exit_events:
        if event.get('confidence') in {'ORDER_ID', 'TRIGGER_ORDER_ID', 'LEGACY_SL_CLIENT_ID'}:
            events[str(event.get('entry_order_id'))].append(event)
    counts = Counter(str(s['entry_order_id']) for s in specs)
    results = []
    for spec in specs:
        oid = str(spec['entry_order_id'])
        entry = orders.get(oid)
        entry_fills = by_order[oid]
        rows = events[oid]
        qty = sum((Decimal(str(f.qty)) for f in entry_fills), Decimal(0))
        closed = sum((Decimal(str(e['qty'])) for e in rows), Decimal(0))
        tolerance = max(Decimal('0.000000001'), qty * Decimal('0.000001'))
        verified = bool(entry and str(getattr(entry, 'status', '')).upper() == 'FILLED' and entry_fills and counts[oid] == 1 and
                        entry.symbol == spec['symbol'] and entry.side == spec['side'] and
                        all(f.symbol == spec['symbol'] and f.side == spec['side'] for f in entry_fills) and
                        qty > 0 and abs(qty - Decimal(str(entry.filled_qty))) <= tolerance)
        status = 'UNRESOLVED'
        if verified and closed <= qty + tolerance:
            status = 'CLOSED' if abs(qty - closed) <= tolerance else ('PARTIAL' if closed > 0 else 'OPEN')
        # Actual initial margin, funding and per-Combo PnL allocation are not
        # established by quantity closure. Preserve NULL instead of estimation.
        results.append(dict(
            event_id=spec['event_id'], entry_order_id=oid, symbol=spec['symbol'],
            combo=spec['combo'], side=spec['side'], status=status, outcome=None,
            opened_ms=min((f.timestamp_ms for f in entry_fills), default=None),
            closed_ms=max((e['time_ms'] for e in rows), default=None) if status == 'CLOSED' else None,
            opened_qty=float(qty), closed_qty=float(closed),
            exit_types=sorted({e['type'] for e in rows}),
            pnl=None, fees=None, margin=None, roi=None,
            source='BINGX_EXACT_ORDER_IDS',
            verification='MONEY_UNVERIFIED' if verified else 'ENTRY_OR_MAPPING_UNVERIFIED',
        ))
    return results
