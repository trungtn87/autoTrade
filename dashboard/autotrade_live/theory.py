"""Read-only theoretical strategy report and persisted V2 execution coverage.

No exchange calls, no trading mutations. Unmatched exits remain unknown.
"""
import json
from collections import defaultdict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from .bridge import case_name

CONFIRMED = {'ORDER_ID', 'TRIGGER_ORDER_ID', 'LEGACY_SL_CLIENT_ID'}

def _number(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0

def _pct(entry, exit_price, side):
    if entry <= 0 or exit_price <= 0: return None
    return 100.0 * (exit_price / entry - 1.0) * (1 if side == 'BUY' else -1)

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

def build_theory(states, specs, orders, exit_events, start_ms=0, symbol='', combo='', since_ms=0):
    if states is None:
        return {'available': False, 'reason': 'Chưa truy cập được nhật ký tín hiệu V2'}
    order_map = {str(o.order_id): o for o in orders}
    events = defaultdict(list)
    for e in exit_events:
        if e.get('confidence') in CONFIRMED and e.get('entry_order_id'):
            events[str(e['entry_order_id'])].append(e)
    stat = defaultdict(lambda: {'combo':'','symbol':'','signals':0,'confirmed':0,'failed':0,'pending':0,'unprotected':0,
                                'closed':0,'win':0,'loss':0,'flat':0,'roi_total':0.0,'wins_sum':0.0,'loss_sum':0.0,
                                'tp_hits':0,'trailing':0,'unresolved':0})
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
        filled = bool(state.get('entry_filled')) and bool(entry_id)
        if filled: s['confirmed'] += 1
        elif state.get('terminal') and not state.get('entry_accepted'):
            s['failed'] += 1
            incidents.append({'time_ms':ts,'combo':name,'symbol':sym,'reason':'ENTRY_FAILED'})
        else:
            s['pending'] += 1
            incidents.append({'time_ms':ts,'combo':name,'symbol':sym,'reason':'ENTRY_UNKNOWN'})
        if not filled: continue
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
        roi = None
        if sl and not tp and not trail:
            roi = _pct(entry, sl['exit_price'], intent.get('side'))
        elif tp and trail:
            a = _pct(entry,tp['exit_price'],intent.get('side'))
            b = _pct(entry,trail['exit_price'],intent.get('side'))
            if a is not None and b is not None: roi = (a+b)/2
        elif tp and sl and sl['time_ms'] >= tp['time_ms']:
            a = _pct(entry,tp['exit_price'],intent.get('side'))
            b = _pct(entry,sl['exit_price'],intent.get('side'))
            if a is not None and b is not None: roi = (a+b)/2
        if roi is None:
            s['unresolved'] += 1
            continue
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
    return {'available':True,'coverage':'Các tín hiệu đã được V2 ghi vào processed_targets; chưa chứng minh tín hiệu bị mất trước khi ghi',
            'summary':totals,'combos':rows,'incidents':sorted(incidents,key=lambda e:e['time_ms'],reverse=True)[:40]}
