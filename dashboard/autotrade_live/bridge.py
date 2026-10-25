"""Read Combo ↔ BingX orderId from Autotrade V2's existing execution state.

No writes to public.processed_targets; no modifications to the trading bot.
"""
from __future__ import annotations
import json
import re
from sqlalchemy import text
from .db import SignalLink, get_sessionmaker


def case_name(value):
    n = int(value)
    if 101 <= n <= 106:
        return f'C{n - 90}'
    return 'TIER' if n == 11 else f'C{n}'


def extract_link(payload, event_id=''):
    try:
        state = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(state, dict):
            return None
        intent = state.get('intent') or {}
        oid = str(state.get('order_id') or '').strip()
        if (not re.fullmatch(r'\d+', oid)
                or not state.get('entry_filled')
                or not isinstance(intent, dict)):
            return None
        combo = case_name(intent['combo'])
        sym = str(intent['symbol']).upper()
        if not re.fullmatch(r'[A-Z0-9]+-[A-Z]+', sym):
            return None
        return dict(entry_order_id=oid, combo=combo, signal_id=str(event_id)[:120],
                    symbol=sym, side=str(intent.get('side', '')).upper()[:12],
                    signal_time_ms=int(intent.get('close_time') or 0), source='execution_state')
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def refresh_signal_links(engine):
    """Idempotently import V2 mappings; conflicting manual labels are not overwritten."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT event_id, payload FROM public.processed_targets
            WHERE target = 'bingx_state' AND payload IS NOT NULL
        """)).all()
    Session = get_sessionmaker(engine)
    summary = {'scanned': len(rows), 'new': 0, 'existing': 0, 'conflicts': 0, 'missing_order_id': 0}
    with Session.begin() as db:
        for event_id, payload in rows:
            data = extract_link(payload, event_id)
            if not data:
                summary['missing_order_id'] += 1
                continue
            prev = db.get(SignalLink, data['entry_order_id'])
            if prev:
                if prev.combo != data['combo'] or (prev.symbol and prev.symbol != data['symbol']):
                    summary['conflicts'] += 1
                else:
                    summary['existing'] += 1
                continue
            db.add(SignalLink(**data))
            summary['new'] += 1
    return summary
