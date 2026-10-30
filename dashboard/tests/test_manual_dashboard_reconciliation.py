"""Regression: Excel only supplements an existing FILLED BingX order.

The dashboard never creates a pretend exchange fill or duplicate trade and
always prefers independently confirmed BingX exits.
"""
from types import SimpleNamespace as Obj
from autotrade_live.theory import build_theory
from autotrade_live.mobile import dashboard_payload


ENTRY_MS = 1791000000000


def state(order_id='777', combo=1):
    return {
        'intent': {'symbol': 'BTC-USDT', 'combo': combo, 'side': 'BUY', 'close_time': ENTRY_MS},
        'order_id': order_id, 'entry_filled': True, 'avg_price': 100,
        'protection': {'sl': {'order_id': '101'}, 'tp': {'order_id': '102'}},
    }


def order(order_id='777', status='FILLED'):
    return Obj(
        order_id=order_id, symbol='BTC-USDT', side='BUY', status=status,
        created_ms=ENTRY_MS + 4000, avg_price=100, type='MARKET',
        position_side='LONG', raw={'positionID': 'position1', 'leverage': '50X'},
    )


def manual(order_id='777', outcome='L', roi_pct=-0.8):
    return {
        'entry_order_id': order_id, 'symbol': 'BTC-USDT', 'combo': 'C1',
        'side': 'BUY', 'entry_time_ms': ENTRY_MS + 4000,
        'exit_ms': ENTRY_MS + 500000, 'outcome': outcome,
        'outcome_source': 'EXCEL', 'roi_source': 'EXCEL_ESTIMATE',
        'roi_pct': roi_pct,
    }


def test_excel_fallback_only_when_real_closed_bingx_entry():
    result = build_theory(
        [('one', state())], [], [order()], [], manual_results=[manual()]
    )
    assert result['summary']['closed'] == 1
    assert result['summary']['unresolved'] == 0
    assert result['summary']['loss'] == 1
    assert result['summary']['roi_total'] == -0.8
    row = result['history'][0]
    assert row['result'] == 'EXCEL_LOSS'
    assert row['broker_entry_verified'] is True
    assert row['outcome_source'] == 'EXCEL'
    assert row['exit_time_source'] == 'BINGX_POSITION'


def test_missing_or_cancelled_broker_order_never_creates_excel_trade():
    for orders in ([], [order(status='CANCELLED')]):
        result = build_theory(
            [('missing', state())], [], orders, [], manual_results=[manual()]
        )
        assert result['summary']['closed'] == 0
        assert result['history'][0]['status'] == 'unresolved'


def test_mismatched_combo_or_side_cannot_take_excel_outcome():
    for changes in ({'combo': 'C7'}, {'side': 'SELL'}, {'symbol': 'ETH-USDT'}):
        report = {**manual(), **changes}
        result = build_theory(
            [('wrong', state())], [], [order()], [], manual_results=[report]
        )
        assert result['summary']['closed'] == 0


def test_verified_exchange_exit_overrides_excel_and_is_counted_once():
    verified_sl = {
        'entry_order_id': '777', 'type': 'SL',
        'exit_price': 98, 'time_ms': ENTRY_MS + 500000, 'confidence': 'ORDER_ID',
    }
    result = build_theory(
        [('one', state())], [], [order()], [verified_sl],
        manual_results=[manual(outcome='W', roi_pct=2)]
    )
    assert result['summary']['closed'] == 1
    assert result['summary']['loss'] == 1
    assert result['summary']['win'] == 0
    assert result['history'][0]['result'] == 'SL'
    assert result['history'][0]['roi_pct'] == -2


def test_dashboard_journal_drops_ghosts_and_unresolved(monkeypatch):
    monkeypatch.delenv('DASHBOARD_START_AT', raising=False)
    records = {
        'orders': [order()], 'fills': [], 'links': [], 'forces': [],
        'incomes': [], 'runs': [], 'positions': [],
        'exit_specs': [], 'manual_results': [manual()],
        'v2_states': [('one', state()), ('ghost', state(order_id='missing'))],
    }
    data = dashboard_payload(records, days='all')
    journal = data['theory']['history']
    assert len(journal) == 1
    assert journal[0]['result'] == 'EXCEL_LOSS'
    assert data['theory']['summary']['closed'] == 1
    assert all(x['status'] == 'closed' and x['broker_entry_verified'] for x in journal)


def test_no_double_count_when_manual_entry_repeated():
    result = build_theory(
        [('one', state())], [], [order()], [], manual_results=[manual(), manual()]
    )
    assert result['summary']['closed'] == 1
    assert len(result['history']) == 1
