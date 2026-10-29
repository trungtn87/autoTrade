"""Regression checks for read-only V2 journal timestamps and honest outcomes."""
from types import SimpleNamespace as Obj
from autotrade_live.theory import build_theory


def _state():
    return {
        'intent': {
            'symbol': 'BTC-USDT', 'combo': 1, 'side': 'BUY',
            'close_time': 1793000000000, 'entry': 100
        },
        'order_id': '111', 'entry_filled': True, 'avg_price': 100,
        'exit_mode': 'split_trailing',
        'protection': {
            'sl': {'order_id': '222'},
            'tp': {'order_id': '333'},
            'trailing': {'order_id': '444'},
        }
    }


def _event(kind, price, when, confidence='ORDER_ID'):
    return {
        'entry_order_id': '111', 'type': kind, 'exit_price': price,
        'time_ms': when, 'confidence': confidence
    }


def test_history_uses_verified_fill_and_last_verified_exit():
    signal = _state()
    result = build_theory(
        [('signal1', signal)], [], [Obj(order_id='111', created_ms=1793000004000, avg_price=100)],
        [
            _event('TP', 101, 1793000030000),
            _event('TRAILING', 102, 1793000090000, 'TRIGGER_ORDER_ID')
        ],
        fills=[Obj(order_id='111', timestamp_ms=1793000007000)]
    )
    assert result['summary']['closed'] == 1
    assert result['summary']['roi_total'] == 1.5
    assert len(result['history']) == 1
    trade = result['history'][0]
    assert trade['result'] == 'TP_TRAILING'
    assert trade['status'] == 'closed'
    assert trade['entry_ms'] == 1793000007000
    assert trade['entry_created_ms'] == 1793000004000
    assert trade['exit_ms'] == 1793000090000
    assert trade['roi_pct'] == 1.5
    assert trade['combo'] == 'C1'


def test_partial_exit_keeps_unknown_close_and_no_invented_roi():
    result = build_theory(
        [('signal2', _state())], [], [Obj(order_id='111', created_ms=1793000004000, avg_price=100)],
        [_event('TP', 101, 1793000030000)]
    )
    assert result['summary']['closed'] == 0
    assert result['summary']['unresolved'] == 1
    trade = result['history'][0]
    assert trade['result'] == 'TP_PARTIAL'
    assert trade['status'] == 'unresolved'
    assert trade['entry_ms'] is None
    assert trade['entry_created_ms'] == 1793000004000
    assert trade['exit_ms'] is None
    assert trade['roi_pct'] is None


def test_failed_entry_is_in_history_but_not_closed():
    state = _state()
    state.update(entry_filled=False, entry_accepted=False, terminal=True, order_id='')
    result = build_theory([('signal3', state)], [], [], [], fills=[])
    assert result['summary']['signals'] == 1
    assert result['summary']['failed'] == 1
    assert result['summary']['closed'] == 0
    assert result['history'][0]['status'] == 'entry_failed'
    assert result['history'][0]['exit_ms'] is None


def test_candidate_exit_never_closes_strategy_history():
    result = build_theory(
        [('signal4', _state())], [], [], [
            _event('SL', 98, 1793000030000, 'PRICE_TIME_CANDIDATE')
        ]
    )
    assert result['summary']['closed'] == 0
    assert result['history'][0]['status'] == 'unresolved'
