"""Historical report compatibility for offline regression only; never served by Dashboard."""
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from dashboard.autotrade_live.analytics import build_trades, margin_roi, summarize
from dashboard.autotrade_live.exits import build_exit_events
from dashboard.autotrade_live.theory import build_theory
from dashboard.autotrade_live.mobile import _start_ms

def dashboard_payload(records, symbol=None, combo=None, days='all'):
    start_ms = _start_ms()
    completed, pending, unresolved = build_trades(records['fills'], records['orders'], records['links'], records['forces'])
    all_combos = sorted({x.combo for x in records['links']} |
                        {x['combo'] for x in records.get('exit_specs', [])})
    all_symbols = sorted({getattr(x,'symbol','') for x in records['orders'] + records['fills'] if getattr(x,'symbol','')})
    # Count only whole positions whose first ENTRY is at/after the baseline.
    # An old position closed today never becomes a new-period profit.
    completed = [x for x in completed if x['entry_ms'] >= start_ms and
                 (not symbol or x['symbol'] == symbol)]
    pending = [x for x in pending if x['entry_ms'] >= start_ms and
               (not symbol or x['symbol'] == symbol)]
    visible = [x for x in completed if not combo or x['combo'] == combo]
    mapped = [x for x in visible if x['combo']]
    total = round(sum(x['pnl'] for x in mapped), 6)
    roi_pct, margin = margin_roi(mapped)
    chart, running = [], 0.0
    for trade in sorted(mapped, key=lambda x:x['exit_ms'] or 0):
        running += trade['pnl']
        chart.append({'t': trade['exit_ms'], 'v': round(running, 6)})
    liqs = sum(1 for x in visible if x['liquidated'])

    exit_events = [
        x for x in build_exit_events(records['orders'], records['fills'],
                                     records.get('exit_specs', []), records['forces'])
        if x['time_ms'] >= start_ms and (not symbol or x['symbol'] == symbol)
        and (not combo or x['combo'] == combo)
    ]
    # BingX exit PnL may be based on a shared average-price position.
    confirmed_exit_events = [x for x in exit_events if x['confidence'] in ('ORDER_ID','TRIGGER_ORDER_ID','LEGACY_SL_CLIENT_ID')]
    confirmed_by_combo = defaultdict(lambda: {'combo':'','count':0,'TP':0,'SL':0,'TRAILING':0,
                                              'realized_pnl':0.0,'exit_fees':0.0})
    for row in confirmed_exit_events:
        if not row['combo']:
            continue
        item = confirmed_by_combo[row['combo']]
        item['combo'] = row['combo']
        item['count'] += 1
        if row['type'] in ('TP','SL','TRAILING'):
            item[row['type']] += 1
        item['realized_pnl'] += row['bingx_realized_pnl']
        item['exit_fees'] += row['exit_fees']
    combo_results = defaultdict(lambda: {'wins': 0, 'losses': 0, 'net': 0.0})
    running_exit_net = 0.0
    exit_curve = []
    wins = losses = breakeven = 0
    gross_winners = gross_losers = 0.0
    for event in sorted(confirmed_exit_events, key=lambda x: (x['time_ms'], x['order_id'])):
        # This is ONLY realizedPnL from a BingX exit and its associated
        # exit-order fee. Entry fees and funding are not attributed here.
        value = event['bingx_realized_pnl'] + event['exit_fees']
        if value > 1e-9:
            wins += 1
            gross_winners += value
        elif value < -1e-9:
            losses += 1
            gross_losers += abs(value)
        else:
            breakeven += 1
        row = combo_results[event['combo']]
        row['net'] += value
        row['wins'] += int(value > 1e-9)
        row['losses'] += int(value < -1e-9)
        running_exit_net += value
        exit_curve.append({'t': event['time_ms'], 'v': round(running_exit_net, 6)})
    exit_performance = {
        'count': len(confirmed_exit_events),
        'wins': wins,
        'losses': losses,
        'breakeven': breakeven,
        'win_rate': round(100 * wins / len(confirmed_exit_events), 2) if confirmed_exit_events else None,
        'pnl_usdt': round(running_exit_net, 6),
        # Null instead of infinite when there are no confirmed losing exits.
        'profit_factor': round(gross_winners / gross_losers, 2) if gross_losers > 1e-9 else None,
        'curve': exit_curve,
    }
    combo_exits = []
    for item in sorted(confirmed_by_combo.values(), key=lambda x: (
            -combo_results[x['combo']]['net'], x['combo'])):
        item['realized_pnl'] = round(item['realized_pnl'], 6)
        item['exit_fees'] = round(item['exit_fees'], 6)
        item['net_after_exit_fee'] = round(combo_results[item['combo']]['net'], 6)
        item['win_rate'] = round(100 * combo_results[item['combo']]['wins'] / item['count'], 2)
        combo_exits.append(item)
    exit_summary = {
        'matched_by_id': len(confirmed_exit_events),
        'matched_by_trigger': sum(x['confidence'] == 'TRIGGER_ORDER_ID' for x in confirmed_exit_events),
        'matched_by_legacy_client': sum(x['confidence'] == 'LEGACY_SL_CLIENT_ID' for x in confirmed_exit_events),
        'price_time_candidates': sum(x['confidence'] == 'PRICE_TIME_CANDIDATE' for x in exit_events),
        'unmapped_or_ambiguous': sum(x['confidence'] in ('UNMAPPED','AMBIGUOUS','CONFLICT') for x in exit_events),
        'bingx_exit_realized_usdt': round(sum(x['bingx_realized_pnl'] for x in confirmed_exit_events), 6),
        'bingx_exit_fees_usdt': round(sum(x['exit_fees'] for x in confirmed_exit_events), 6),
    }
    # Persisted execution warnings only: dashboard never reads BingX directly.
    # Keep unresolved legacy SL replacements visible even when old cleanup
    # state was marked "done" under an earlier implementation.
    cleanup_risks = []
    for spec in records.get('exit_specs', []):
        if (spec['signal_time_ms'] < start_ms or
                (symbol and spec['symbol'] != symbol) or
                (combo and spec['combo'] != combo)):
            continue
        flags = list(spec.get('cleanup_warnings') or [])
        if spec.get('legacy_sl_pending') and 'LEGACY_SL_REPLACEMENT_UNVERIFIED' not in flags:
            flags.append('LEGACY_SL_REPLACEMENT_UNVERIFIED')
        for flag in flags:
            if flag == 'LEGACY_SL_REPLACEMENT_UNVERIFIED' or spec.get('cleanup_status') != 'done':
                cleanup_risks.append({
                    'combo': spec['combo'],
                    'symbol': spec['symbol'],
                    'reason': flag,
                    'cleanup_status': spec.get('cleanup_status') or 'not_checked',
                })
    cleanup_risks = cleanup_risks[:30]
    fill_net = sum(float(f.realized_pnl) + float(f.fee)
                   for f in records['fills'] if f.timestamp_ms >= start_ms and
                   (not symbol or f.symbol == symbol))
    try:
        period_days = int(days) if days != 'all' else 0
    except (ValueError, TypeError):
        period_days = 0
    period_days = period_days if period_days in (1, 7, 30, 90) else 0
    since_ms = int(time.time() * 1000) - period_days * 86400000 if period_days else 0
    theory = build_theory(records.get('v2_states'), records.get('exit_specs', []),
                          records['orders'], exit_events, start_ms=start_ms,
                          symbol=symbol or '', combo=combo or '', since_ms=since_ms,
                          fills=records['fills'],
                          manual_results=records.get('manual_results', []))
    # Display only completed trades backed by a real BingX FILLED entry.
    # Unresolved legacy signals are not trade history, and are not shown.
    if theory.get('available'):
        theory['history'] = [
            row for row in theory['history']
            if row['status'] == 'closed' and row.get('broker_entry_verified')
            and row.get('exit_ms') and row.get('roi_pct') is not None
        ]
        theory['combos'] = [row for row in theory['combos'] if row['closed'] > 0]
    return {
        'theory': theory,
        'theory_since': datetime.fromtimestamp(max(start_ms, since_ms)/1000, timezone.utc).strftime('%d/%m/%Y') if max(start_ms, since_ms) else 'đầu dữ liệu',
        'summary': {
            'pnl_usdt': total,
            'pnl_pct': roi_pct,
            'margin_used_usdt': margin,
            'win_rate': round(100 * sum(x['pnl'] > 0 for x in mapped) / len(mapped), 2) if mapped else 0.0,
            'closed': len(mapped),
            'unmapped': sum(not x['combo'] for x in completed),
            'liquidations': liqs,
            'open_positions': len(pending),
            'fill_net_usdt': round(fill_net, 6),
            'unresolved_fills': sum(1 for x in unresolved if x.get('timestamp_ms', 0) >= start_ms),
        },
        'combos': summarize(mapped),
        'trades': sorted(visible, key=lambda x:x['exit_ms'] or 0, reverse=True)[:100],
        'exit_events': exit_events[:100],
        'exit_summary': exit_summary,
        'combo_exits': combo_exits,
        'exit_performance': exit_performance,
        'cleanup_risks': cleanup_risks,
        'equity': chart,
        'filters': {'combos': all_combos, 'symbols': all_symbols},
        'start_at': datetime.fromtimestamp(start_ms/1000, timezone.utc).isoformat() if start_ms else None,
        'last_sync': ({'status':records['runs'][0].status,
                       'time':records['runs'][0].finished_at.isoformat() if records['runs'][0].finished_at else None,
                       'details':records['runs'][0].details} if records['runs'] else None),
        'disclaimer': 'PnL gồm realisedPNL và phí fills, chưa phân bổ funding. PnL % = PnL ròng / tổng (giá trị Entry / đòn bẩy). Đây là ROI ký quỹ ước tính, không phải lợi nhuận tài khoản; có thể khác ký quỹ thực do thêm margin và cơ chế thanh lý.'
    }

