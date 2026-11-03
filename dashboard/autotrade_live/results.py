"""Read and format V2-normalized results. No exchange or execution imports."""
import json
from datetime import datetime, timezone
from collections import defaultdict
from sqlalchemy import text, inspect
from sqlalchemy.exc import SQLAlchemyError


def load_results(engine):
    # Dashboard-only read: an unapplied migration must not hide existing history.
    schema = 'autotrade_analytics' if engine.dialect.name == 'postgresql' else None
    inspector = inspect(engine)
    has_normalized = inspector.has_table('trade_results', schema=schema)
    has_history = inspector.has_view('manual_dashboard_trade_results', schema=schema)
    if not has_normalized and not has_history:
        return None
    rows = []
    if has_normalized:
        table = 'autotrade_analytics.trade_results' if schema else 'trade_results'
        with engine.connect() as conn:
            normalized = conn.execute(text('SELECT payload FROM ' + table)).scalars().all()
        rows = [json.loads(row) if isinstance(row, str) else row for row in normalized]
    if has_history:
        table = ('autotrade_analytics.manual_dashboard_trade_results' if schema
                 else 'manual_dashboard_trade_results')
        with engine.connect() as conn:
            history = conn.execute(text(
                "SELECT entry_order_id,symbol,combo,side,entry_time_ms,exit_ms,"
                "outcome,roi_pct FROM " + table + " WHERE status='CLOSED'"
            )).mappings().all()
        covered = {(r.get('symbol'), str(r['entry_order_id']))
                   for r in rows if r.get('status') == 'CLOSED' and r.get('entry_order_id')}
        for old in history:
            identity = (old['symbol'], str(old['entry_order_id']))
            if identity in covered or old['roi_pct'] is None or old['outcome'] not in ('W', 'L'):
                continue
            covered.add(identity)
            rows.append(dict(
                event_id='history:' + old['symbol'] + ':' + str(old['entry_order_id']),
                entry_order_id=str(old['entry_order_id']),
                symbol=old['symbol'], combo=old['combo'], side=old['side'],
                status='CLOSED', opened_ms=int(old['entry_time_ms']),
                closed_ms=int(old['exit_ms']),
                outcome='WIN' if old['outcome'] == 'W' else 'LOSS',
                roi=float(old['roi_pct']), pnl=None, margin=None,
                source='existing_history', exit_types=[]
            ))
    return rows


def sync_metadata(run):
    if run is None:
        return None
    try:
        details = json.loads(run.details)
    except (ValueError, TypeError):
        details = {}
    coverage = run.finished_at
    if isinstance(details, dict) and details.get('stage') == 'raw_sync' and details.get('end_ms') is not None:
        coverage = datetime.fromtimestamp(details['end_ms']/1000, timezone.utc)
    return dict(status=run.status, time=coverage.isoformat() if coverage else None,
                completed_at=run.finished_at.isoformat() if run.finished_at else None)


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
                            pnl=row.get('pnl'), margin=row.get('margin'),
                            source=row.get('source'), exit_types=row.get('exit_types', [])))
    combos = []
    for (sym, name), trades in sorted(groups.items()):
        wins = sum(t.get('outcome') == 'WIN' for t in trades)
        losses = sum(t.get('outcome') == 'LOSS' for t in trades)
        complete = all(t.get('outcome') in {'WIN', 'LOSS', 'BREAKEVEN'} for t in trades)
        money_complete = all(t.get('roi') is not None and t.get('pnl') is not None and
                             t.get('margin') is not None and t['margin'] > 0 for t in trades)
        actual_roi = (100*sum(t['pnl'] for t in trades)/sum(t['margin'] for t in trades)) if money_complete else None
        # Preserve existing historical ROI figures without recalculating them.
        if actual_roi is None and all(t.get('source') == 'existing_history' and
                                      t.get('roi') is not None for t in trades):
            actual_roi = sum(t['roi'] for t in trades)
        combos.append(dict(symbol=sym, combo=name, closed=len(trades), win=wins, loss=losses,
                           tp_count=wins if complete else None,
                           sl_count=losses if complete else None,
                           win_rate=round(100*wins/len(trades), 2) if complete else None,
                           real_roi_total=round(actual_roi, 4) if actual_roi is not None else None))
    return dict(theory=dict(available=available, reason=None if available else 'Normalized results schema requires review and migration', combos=combos, history=sorted(history, key=lambda t:t['exit_ms'], reverse=True)),
                last_sync=last_sync, trades=[], summary={'closed':len(history)}, unresolved=sum(r.get('status')=='UNRESOLVED' for r in rows))
