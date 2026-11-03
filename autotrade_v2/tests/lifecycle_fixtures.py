"""Synthetic provider-confirmed lifecycle fixtures; no credentials/network."""
from copy import deepcopy
from autotrade_v2.analytics.evidence import capture_entry


def opened(event_id='lifecycle-1', qty='2', *, terminal=True, entry_status='FILLED'):
    state = dict(terminal=terminal, entry_accepted=True, entry_filled=entry_status=='FILLED',
        order_id='entry-' + event_id, client_order_id='cid-' + event_id,
        executed_qty=qty, position_id='position-' + event_id,
        intent=dict(event_id=event_id,symbol='BTC-USDT',side='BUY',combo=1),
        protection={kind:{'order_id':kind + '-' + event_id} for kind in ('tp','sl','trailing')})
    capture_entry(state, dict(orderId=state['order_id'],clientOrderId=state['client_order_id'],
        symbol='BTC-USDT',side='BUY',status=entry_status,executedQty=qty,
        origQty='2',updateTime=1000), state['client_order_id'])
    return state


def exited(state, kind, qty, *, time_ms=2000, triggered=False, status='FILLED'):
    state = deepcopy(state)
    parent = state['protection'][kind]['order_id']
    entry_side = state['intent']['side']
    order = dict(orderId='child-' + parent if triggered else parent,
        symbol=state['intent']['symbol'],side='SELL' if entry_side == 'BUY' else 'BUY',
        positionSide='LONG' if entry_side == 'BUY' else 'SHORT',
        positionId=state['position_id'],
        type={'tp':'TAKE_PROFIT_MARKET', 'sl':'STOP_MARKET',
              'trailing':'TRAILING_STOP_MARKET'}[kind],
        executedQty=qty,origQty=qty,status=status,updateTime=time_ms)
    if triggered:
        order['triggerOrderId'] = parent
    state.setdefault('exit_cleanup', {}).setdefault('orders', {})[parent] = {'analytics_order':order}
    return state
