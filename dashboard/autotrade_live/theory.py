"""Read-only theoretical strategy report and persisted V2 execution coverage.

No exchange calls, no trading mutations. Unmatched exits remain unknown.
"""
import json
import logging
import re
from collections import defaultdict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from .bridge import case_name

CONFIRMED = {'ORDER_ID', 'TRIGGER_ORDER_ID', 'LEGACY_SL_CLIENT_ID'}
LOG = logging.getLogger(__name__)

def _number(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0

def _pct(entry, exit_price, side):
    if entry <= 0 or exit_price <= 0: return None
    return 100.0 * (exit_price / entry - 1.0) * (1 if side == 'BUY' else -1)


def _recorded_roi(entry_order, entry_price, exit_rows, entry_fee=0.0):
    """Return net exchange-exit ROI on estimated initial margin, or unknown.

    Only use completely linked, quantity-balanced exit fills; position-wide
    events are not assigned across unrelated Combo entries.
    """
    raw = (getattr(entry_order, 'raw', None) or {}) if entry_order else {}
    leverage_text = str(raw.get('leverage') or '') if isinstance(raw, dict) else ''
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*[xX]?\s*', leverage_text)
    leverage = float(match.group(1)) if match else 0
    qty = _number(getattr(entry_order, 'filled_qty', None))
    if leverage <= 0 or qty <= 0 or entry_price <= 0 or not exit_rows:
        return None
    if any('qty' not in ev or 'bingx_realized_pnl' not in ev or 'exit_fees' not in ev
           for ev in exit_rows):
        return None
    exited_qty = sum(_number(ev['qty']) for ev in exit_rows)
    if abs(exited_qty - qty) > max(1e-9, qty * 1e-6):
        return None
    net = sum(_number(ev['bingx_realized_pnl']) + _number(ev['exit_fees'])
              for ev in exit_rows) + entry_fee
    return 100.0 * net / (qty * entry_price / leverage)


def load_states(engine):
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT event_id, payload FROM public.processed_targets WHERE target = 'bingx_state' AND payload IS NOT NULL")).all()
    except SQLAlchemyError:
        return None
    result = []
    for event_id, payload in rows:
        try:
            state = json.loads(payload) if isinstance(payload, str) else payload
            intent = state.get('intent') or {}
            if isinstance(intent, dict) and intent.get('symbol') and intent.get('combo') is not None:
                result.append((str(event_id), state))
        except (TypeError, ValueError, AttributeError):
            continue
    return result

def load_manual_results(engine):
    """Read only reconciled, fully closed BingX positions with an Excel outcome.

    The private SQL view verifies exact BingX entry ID, position closure, and
    unique source mapping. These outcomes are never represented as BingX fills
    or per-combo USDT PnL.
    """
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT v.entry_order_id, v.symbol, v.combo, v.side, v.entry_time_ms, v.exit_ms, "
                "v.roi_pct, v.roi_source, v.outcome, v.outcome_source, "
                "m.reported_roi_pct "
                "FROM autotrade_analytics.manual_dashboard_trade_results v "
                "JOIN autotrade_analytics.manual_trade_results m "
                "ON m.entry_order_id = v.entry_order_id AND m.symbol = v.symbol "
                "WHERE v.status = 'CLOSED'"
            )).mappings().all()
    except SQLAlchemyError as exc:
        LOG.warning("Manual dashboard reconciliation unavailable: %s", type(exc).__name__)
        return []
    return [dict(row) for row in rows]


def build_theory(states, specs, orders, exit_events, start_ms=0, symbol='', combo='', since_ms=0, fills=(), manual_results=()):
    if states is None:
        return {'available': False, 'reason': 'Chưa truy cập được nhật ký tín hiệu V2'}
    order_map = {str(o.order_id): o for o in orders}
    manual_by_entry = {str(r['entry_order_id']): r for r in (manual_results or []) if r.get('entry_order_id')}
    # Entry timestamp is an actual BingX fill; order creation is a separate fallback
    # field, never passed off as a verified fill time.
    entry_fill_times = {}
    entry_fees = defaultdict(float)
    for fill in fills:
        oid = str(getattr(fill, 'order_id', '') or '')
        when = int(getattr(fill, 'timestamp_ms', 0) or 0)
        if oid and when > 0:
            entry_fill_times[oid] = min(when, entry_fill_times.get(oid, when))
            entry_fees[oid] += _number(getattr(fill, 'fee', 0))
    history = []
    events = defaultdict(list)
    for e in exit_events:
        if e.get('confidence') in CONFIRMED and e.get('entry_order_id'):
            events[str(e['entry_order_id'])].append(e)
    stat = defaultdict(lambda: {'combo':'','symbol':'','signals':0,'confirmed':0,'failed':0,'pending':0,'unprotected':0,
                                'closed':0,'win':0,'loss':0,'flat':0,'roi_total':0.0,'wins_sum':0.0,'loss_sum':0.0,
                                'tp_hits':0,'trailing':0,'unresolved':0,
                                'recorded_roi_sum':0.0,'recorded_roi_count':0})
    incidents = []
    for event_id, state in states:
        intent = state.get('intent') or {}
        sym = str(intent.get('symbol') or '').upper()
        try: name = case_name(intent.get('combo'))
        except (ValueError, TypeError): continue
        ts = int(intent.get('close_time') or 0)
        if ts < max(start_ms, since_ms) or (symbol and symbol != sym) or (combo and combo != name):
            continue
        s = stat[(sym,name)]
        s['combo'], s['symbol'] = name,sym
        s['signals'] += 1
        entry_id = str(state.get('order_id') or '')
        entry_order = order_map.get(entry_id)
        created = int(getattr(entry_order, 'created_ms', 0) or 0)
        record = {
            'symbol': sym, 'combo': name, 'side': str(intent.get('side') or '').upper(),
            'signal_ms': ts or None,
            'entry_ms': entry_fill_times.get(entry_id),
            'entry_created_ms': created if created > 0 else None,
            'exit_ms': None, 'roi_pct': None,
            'status': 'unresolved', 'result': 'UNRESOLVED',
            'broker_entry_verified': bool(entry_order is not None and
                str(getattr(entry_order, 'status', 'FILLED')).upper() == 'FILLED' and
                str(getattr(entry_order, 'symbol', sym)).upper() == sym),
        }
        history.append(record)
        filled = bool(state.get('entry_filled')) and bool(entry_id)
        if filled: s['confirmed'] += 1
        elif state.get('terminal') and not state.get('entry_accepted'):
            s['failed'] += 1
            incidents.append({'time_ms':ts,'combo':name,'symbol':sym,'reason':'ENTRY_FAILED'})
        else:
            s['pending'] += 1
            incidents.append({'time_ms':ts,'combo':name,'symbol':sym,'reason':'ENTRY_UNKNOWN'})
        if not filled:
            record['status'] = 'entry_failed' if state.get('terminal') and not state.get('entry_accepted') else 'entry_unknown'
            record['result'] = 'ENTRY_FAILED' if record['status'] == 'entry_failed' else 'ENTRY_UNKNOWN'
            continue
        protection = state.get('protection') or {}
        expected = ('sl', 'tp', 'trailing') if state.get('exit_mode') == 'split_trailing' else ('sl','tp')
        if any(not (protection.get(k) or {}).get('order_id') for k in expected):
            s['unprotected'] += 1
            incidents.append({'time_ms':ts,'combo':name,'symbol':sym,'reason':'PROTECTION_UNVERIFIED'})
        entry = _number(state.get('avg_price')) or _number(getattr(order_map.get(entry_id),'avg_price',0)) or _number(intent.get('entry'))
        # Only a verified entry and verified linked exit fills count toward strategy outcome.
        completed = sorted(events.get(entry_id, []), key=lambda e:e['time_ms'])
        outcomes = {}
        for ev in completed:
            kind = ev['type']
            if kind in ('TP','SL','TRAILING') and kind not in outcomes:
                outcomes[kind] = ev
        tp = outcomes.get('TP')
        sl = outcomes.get('SL')
        trail = outcomes.get('TRAILING')
        entry_qty = _number(getattr(entry_order, 'filled_qty', None)) or _number(state.get('executed_qty'))
        def full_exit(ev):
            # Only a proven fully executed solo exit closes a split position.
            if entry_qty > 0:
                qty = _number(ev.get('qty')) if ev else 0
                return qty > 0 and abs(qty - entry_qty) <= max(1e-9, entry_qty * 1e-6)
            # Legacy SL test states without quantity retain their prior behavior;
            # a TP/trailing without quantity is never assumed to be complete.
            return bool(ev and ev.get('type') == 'SL')

        roi = None
        if sl and not tp and not trail and full_exit(sl):
            roi = _pct(entry, sl['exit_price'], intent.get('side'))
        elif tp and not sl and not trail and full_exit(tp):
            roi = _pct(entry, tp['exit_price'], intent.get('side'))
        elif trail and not tp and not sl and full_exit(trail):
            roi = _pct(entry, trail['exit_price'], intent.get('side'))
        elif tp and trail:
            a = _pct(entry,tp['exit_price'],intent.get('side'))
            b = _pct(entry,trail['exit_price'],intent.get('side'))
            if a is not None and b is not None: roi = (a+b)/2
        elif tp and sl and sl['time_ms'] >= tp['time_ms']:
            a = _pct(entry,tp['exit_price'],intent.get('side'))
            b = _pct(entry,sl['exit_price'],intent.get('side'))
            if a is not None and b is not None: roi = (a+b)/2
        elif trail and sl and sl['time_ms'] >= trail['time_ms'] and not tp:
            # The trailing exit closed the first theoretical half; the
            # remaining half later exited at SL. Treat as one complete
            # signal, not two independent trades.
            a = _pct(entry,trail['exit_price'],intent.get('side'))
            b = _pct(entry,sl['exit_price'],intent.get('side'))
            if a is not None and b is not None: roi = (a+b)/2
        if roi is None:
            # The Excel supplement is allowed only for this exact, exchange-
            # FILLED entry and a fully closed BingX position. No price/time
            # guessing, no duplicate trade, and no fabricated exchange PnL.
            manual = manual_by_entry.get(entry_id)
            manual_roi = _number(manual.get('roi_pct')) if manual else 0.0
            manual_outcome = manual.get('outcome') if manual else None
            manual_exit_ms = int(manual.get('exit_ms') or 0) if manual else 0
            if (manual and entry_order is not None
                and str(getattr(entry_order, 'status', '')).upper() == 'FILLED'
                and str(getattr(entry_order, 'symbol', '')).upper() == sym
                and str(getattr(entry_order, 'side', '')).upper() == str(intent.get('side') or '').upper()
                and str(manual.get('symbol')) == sym
                and str(manual.get('combo')) == name
                and str(manual.get('side')) == str(intent.get('side') or '').upper()
                and int(manual.get('entry_time_ms') or 0) >= max(start_ms, since_ms)
                and manual_exit_ms >= int(manual.get('entry_time_ms') or 0) > 0
                and ((manual_outcome == 'W' and manual_roi > 0) or
                     (manual_outcome == 'L' and manual_roi < 0))):
                s['closed'] += 1
                s['roi_total'] += manual_roi
                reported_roi = manual.get('reported_roi_pct')
                if reported_roi is not None:
                    recorded_roi = float(reported_roi)
                    s['recorded_roi_sum'] += recorded_roi
                    s['recorded_roi_count'] += 1
                    record['recorded_roi_pct'] = round(recorded_roi, 4)
                if manual_roi > 0:
                    s['win'] += 1; s['wins_sum'] += manual_roi
                else:
                    s['loss'] += 1; s['loss_sum'] += abs(manual_roi)
                record.update(
                    status='closed',
                    result=('BINGX_WIN' if manual_outcome == 'W' else 'BINGX_LOSS')
                           if manual.get('outcome_source') == 'BINGX'
                           else ('EXCEL_WIN' if manual_outcome == 'W' else 'EXCEL_LOSS'),
                    roi_pct=round(manual_roi, 4), exit_ms=manual_exit_ms,
                    outcome_source=str(manual.get('outcome_source') or 'EXCEL'),
                    roi_source=str(manual.get('roi_source') or 'EXCEL_ESTIMATE'),
                    exit_time_source='BINGX_POSITION'
                )
                continue
            s['unresolved'] += 1
            record['result'] = (
                'TP_PARTIAL' if tp and not trail and not sl else
                'TRAILING_PARTIAL' if trail and not tp and not sl else 'UNRESOLVED'
            )
            continue
        if sl and not tp and not trail:
            result, used = 'SL', [sl]
        elif tp and not sl and not trail:
            result, used = 'TP', [tp]
        elif trail and not sl and not tp:
            result, used = 'TRAILING', [trail]
        elif tp and trail:
            result, used = 'TP_TRAILING', [tp, trail]
        elif tp and sl:
            result, used = 'TP_SL', [tp, sl]
        else:
            result, used = 'TRAILING_SL', [trail, sl]
        record.update(status='closed', result=result, roi_pct=round(roi, 4),
                      exit_ms=max(int(x['time_ms']) for x in used))
        recorded_roi = _recorded_roi(entry_order, entry, used,
                                     entry_fees.get(entry_id, 0.0))
        if recorded_roi is not None:
            s['recorded_roi_sum'] += recorded_roi
            s['recorded_roi_count'] += 1
            record['recorded_roi_pct'] = round(recorded_roi, 4)
        s['closed'] += 1
        s['roi_total'] += roi
        s['tp_hits'] += int(bool(tp))
        s['trailing'] += int(bool(trail))
        if roi > 1e-9: s['win'] += 1; s['wins_sum'] += roi
        elif roi < -1e-9: s['loss'] += 1; s['loss_sum'] += abs(roi)
        else: s['flat'] += 1
    total_win_sum = sum(s['wins_sum'] for s in stat.values())
    total_loss_sum = sum(s['loss_sum'] for s in stat.values())
    rows = []
    for s in stat.values():
        n = s['closed']; w=s['win']; l=s['loss']
        s['win_rate'] = round(100*w/n,2) if n else None
        s['entry_rate'] = round(100*s['confirmed']/s['signals'],2) if s['signals'] else None
        s['rr'] = round((s['wins_sum']/w)/(s['loss_sum']/l),2) if w and l else None
        s['roi_total'] = round(s['roi_total'],4)
        s['roi_avg'] = round(s['roi_total']/n,4) if n else None
        s['tp_count'], s['sl_count'] = w, l
        s['real_roi_total'] = (
            round(s['recorded_roi_sum'], 4)
            if n > 0 and s['recorded_roi_count'] == n else None
        )
        s.pop('recorded_roi_sum');s.pop('recorded_roi_count')
        s.pop('wins_sum');s.pop('loss_sum')
        rows.append(s)
    rows.sort(key=lambda r:(-r['roi_total'],r['symbol'],r['combo']))
    totals = {k:sum(r[k] for r in rows) for k in ('signals','confirmed','failed','pending','unprotected','closed','win','loss','flat','roi_total','unresolved')}
    totals['roi_total'] = round(totals['roi_total'],4)
    totals['win_rate'] = round(100*totals['win']/totals['closed'],2) if totals['closed'] else None
    totals['entry_rate'] = round(100*totals['confirmed']/totals['signals'],2) if totals['signals'] else None
    totals['roi_avg'] = round(totals['roi_total']/totals['closed'],4) if totals['closed'] else None
    totals['rr'] = (round((total_win_sum/totals['win']) / (total_loss_sum/totals['loss']),2)
                    if totals['win'] and totals['loss'] else None)
    history.sort(key=lambda r: (r['exit_ms'] or r['entry_ms'] or r['signal_ms'] or 0), reverse=True)
    return {'available':True,'coverage':'Các tín hiệu đã được V2 ghi vào processed_targets; chưa chứng minh tín hiệu bị mất trước khi ghi',
            'summary':totals,'combos':rows,'history':history,
            'incidents':sorted(incidents,key=lambda e:e['time_ms'],reverse=True)[:40]}
