"""Regression: compact Combo performance and full-size exit handling."""
from types import SimpleNamespace as Obj

from autotrade_live.theory import build_theory


T = 1793000000000


def state(oid='entry', mode='split_trailing'):
    return {
        'intent': {'combo': 1, 'symbol': 'BTC-USDT', 'side': 'BUY', 'close_time': T},
        'order_id': oid, 'entry_filled': True, 'avg_price': 100,
        'executed_qty': 1.0, 'exit_mode': mode,
        'protection': {'sl': {'order_id': 'sl'}, 'tp': {'order_id': 'tp'},
                       'trailing': {'order_id': 'trail'}},
    }


def entry(oid='entry'):
    return Obj(order_id=oid, symbol='BTC-USDT', status='FILLED', side='BUY',
               avg_price=100, filled_qty=1.0, created_ms=T + 1000,
               raw={'leverage': '50X', 'positionID': 'P1'})


def event(kind, price, qty=1.0, gross=0, fee=0, when=T + 5000):
    return {'entry_order_id': 'entry', 'type': kind, 'exit_price': price, 'qty': qty,
            'bingx_realized_pnl': gross, 'exit_fees': fee, 'time_ms': when,
            'confidence': 'TRIGGER_ORDER_ID'}


def result(events=(), manual=(), fills=()):
    return build_theory(
        [('id', state())], [], [entry()], list(events),
        manual_results=list(manual), fills=list(fills),
    )


def test_fully_closed_tp_counts_one_winner_and_realized_roi():
    entry_fill = Obj(order_id='entry', timestamp_ms=T + 1500, fee=-0.05)
    report = result(events=[event('TP', 102, gross=2, fee=-0.1)], fills=[entry_fill])
    s = report['combos'][0]
    assert s['closed'] == s['tp_count'] == 1
    assert s['sl_count'] == 0
    assert s['real_roi_total'] == 92.5  # (2 - 0.1 - 0.05) / (100 / 50)
    assert report['history'][0]['result'] == 'TP'


def test_half_tp_does_not_close_or_contribute_to_roi():
    report = result(events=[event('TP', 102, qty=.5, gross=1, fee=-0.05)])
    s = report['combos'][0]
    assert s['closed'] == 0
    assert s['tp_count'] == s['sl_count'] == 0
    assert s['real_roi_total'] is None


def test_half_trailing_does_not_close():
    report = result(events=[event('TRAILING', 101, qty=.5, gross=.5, fee=-0.05)])
    assert report['summary']['closed'] == 0


def test_sl_counts_loss_and_fees_in_actual_return():
    report = result(events=[event('SL', 99, gross=-1, fee=-0.1)])
    s = report['combos'][0]
    assert s['closed'] == s['sl_count'] == 1
    assert s['tp_count'] == 0
    assert s['real_roi_total'] == -55.0


def test_manual_observed_roi_not_confused_with_price_change():
    manual = {'entry_order_id': 'entry', 'symbol': 'BTC-USDT', 'combo': 'C1',
              'side': 'BUY', 'entry_time_ms': T + 1000, 'exit_ms': T + 10000,
              'outcome': 'W', 'outcome_source': 'EXCEL', 'roi_source': 'EXCEL_ESTIMATE',
              'roi_pct': 2.069, 'reported_roi_pct': 206.9}
    report = result(manual=[manual])
    s = report['combos'][0]
    assert s['closed'] == s['tp_count'] == 1
    assert s['real_roi_total'] == 206.9
    assert report['history'][0]['roi_pct'] == 2.069


def test_unverified_exit_cannot_fake_tp():
    report = result(events=[{**event('TP', 102, gross=2), 'confidence': 'PRICE_TIME_CANDIDATE'}])
    s = report['combos'][0]
    assert s['closed'] == 0
    assert s['tp_count'] == 0


def test_quantity_mismatch_never_becomes_actual_roi():
    tp = event('TP', 102, qty=.5, gross=1)
    sl = event('SL', 99, qty=1.0, gross=-1, when=T + 6000)
    report = result(events=[tp, sl])
    assert report['summary']['closed'] == 1  # Legacy price outcome kept
    assert report['combos'][0]['real_roi_total'] is None
