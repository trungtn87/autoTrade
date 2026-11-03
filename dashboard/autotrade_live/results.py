"""Read and format V2-normalized results. No exchange or execution imports."""
import json
from collections import defaultdict
from sqlalchemy import text, inspect
from sqlalchemy.exc import SQLAlchemyError


def load_results(engine):
    # Schema is fixed server-side; no user-controlled SQL identifier.
    schema = 'autotrade_analytics' if engine.dialect.name == 'postgresql' else None
    if not inspect(engine).has_table('trade_results', schema=schema):
        return None
    table = 'autotrade_analytics.trade_results' if schema else 'trade_results'
    with engine.connect() as conn:
        rows = conn.execute(text('SELECT payload FROM ' + table)).scalars().all()
    return [json.loads(row) if isinstance(row, str) else row for row in rows]


def result_payload(rows, last_sync=None, symbol='', combo='', start_ms=0):
    available = rows is not None
    rows = rows or []
    history = []
    groups = defaultdict(list)
    for row in rows:
        if row.get('status') != 'CLOSED' or not row.get('closed_ms'):
            continue
        if (row.get('opened_ms') or 0) < start_ms:
            continue
        if symbol and row.get('symbol') != symbol or combo and row.get('combo') != combo:
            continue
        groups[(row['symbol'], row['combo'])].append(row)
        history.append(dict(symbol=row['symbol'], combo=row['combo'], side=row['side'],
                            status='closed', entry_ms=row.get('opened_ms'), exit_ms=row['closed_ms'],
                            outcome=row.get('outcome'), result=row.get('outcome') or 'UNRESOLVED',
                            roi_pct=row.get('roi'), recorded_roi_pct=row.get('roi'),
                            source=row.get('source'), exit_types=row.get('exit_types', [])))
    combos = []
    for (sym, name), trades in sorted(groups.items()):
        wins = sum(t.get('outcome') == 'WIN' for t in trades)
        losses = sum(t.get('outcome') == 'LOSS' for t in trades)
        complete = all(t.get('outcome') in {'WIN', 'LOSS', 'BREAKEVEN'} for t in trades)
        rois = [t.get('roi') for t in trades]
        combos.append(dict(symbol=sym, combo=name, closed=len(trades), win=wins, loss=losses,
                           tp_count=sum('TP' in t.get('exit_types', []) for t in trades),
                           sl_count=sum('SL' in t.get('exit_types', []) for t in trades),
                           win_rate=round(100*wins/len(trades), 2) if complete else None,
                           real_roi_total=sum(rois) if all(r is not None for r in rois) else None))
    return dict(theory=dict(available=available, reason=None if available else 'Normalized results schema requires review and migration', combos=combos, history=sorted(history, key=lambda t:t['exit_ms'], reverse=True)),
                last_sync=last_sync, trades=[], summary={'closed':len(history)}, unresolved=sum(r.get('status')=='UNRESOLVED' for r in rows))
