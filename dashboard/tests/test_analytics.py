from types import SimpleNamespace as Obj
from autotrade_live.analytics import build_trades, summarize


def order(order_id, typ, pos='LONG'):
    return Obj(order_id=order_id, type=typ, position_side=pos)


def fill(trade_id, ts, order_id, side, qty, price, pnl, fee, symbol='ETH-USDT', position_side='LONG'):
    return Obj(trade_id=trade_id, timestamp_ms=ts, order_id=order_id, symbol=symbol,
               side=side, position_side=position_side, qty=qty, price=price,
               realized_pnl=pnl, fee=fee)


def test_partial_tp_trailing_one_trade():
    orders = [order('1','MARKET'), order('2','TAKE_PROFIT_MARKET'), order('3','TRAILING_STOP_MARKET')]
    rows = [fill('a',100,'1','BUY',1,1000,0,-.4),
            fill('b',200,'2','SELL',.5,1100,50,-.25),
            fill('c',300,'3','SELL',.5,1070,35,-.25)]
    completed, open_, issues = build_trades(rows, orders, [Obj(entry_order_id='1',combo='C5')], [])
    assert len(completed) == 1 and not open_ and not issues
    assert completed[0]['pnl'] == 84.1
    assert completed[0]['exit_price'] == 1085
    assert summarize(completed)[0]['Số lệnh'] == 1


def test_two_entries_must_be_mixed_not_assigned():
    orders = [order('1','MARKET'),order('2','MARKET'),order('3','STOP_MARKET')]
    rows = [fill('a',100,'1','BUY',1,1000,0,-1),fill('b',200,'2','BUY',1,1000,0,-1),
            fill('c',300,'3','SELL',2,980,-40,-2)]
    links = [Obj(entry_order_id='1',combo='C1'),Obj(entry_order_id='2',combo='C2')]
    completed, _, _ = build_trades(rows,orders,links,[])
    assert completed[0]['label'] == 'MIXED'
    assert completed[0]['combo'] is None
    assert not summarize(completed)


def test_liquidation_flag_comes_from_force_order():
    orders = [order('1','MARKET'),order('9','MARKET')]
    rows = [fill('a',100,'1','BUY',1,1000,0,-1),fill('b',300,'9','SELL',1,900,-100,-1)]
    forced = [Obj(order_id='9',reason='LIQUIDATION')]
    completed, _, _ = build_trades(rows,orders,[Obj(entry_order_id='1',combo='C1')],forced)
    assert completed[0]['liquidated']
    assert completed[0]['pnl'] == -102


def test_unmapped_entry_not_counted_as_combo():
    orders = [order('1','MARKET'),order('2','MARKET')]
    rows = [fill('a',100,'1','BUY',1,1000,0,0),fill('b',200,'2','SELL',1,1010,10,-1)]
    completed, _, _ = build_trades(rows,orders,[],[])
    assert completed[0]['label'] == 'UNMAPPED'
    assert not summarize(completed)


def test_roi_pct_on_estimated_entry_margin_includes_fill_fees():
    first=Obj(order_id='in',type='MARKET',position_side='LONG',
              raw={'leverage':'50X','positionID':'pos1'})
    last=Obj(order_id='out',type='TAKE_PROFIT_MARKET',position_side='LONG',
             raw={'leverage':'50X','positionID':'pos1'})
    rows=[fill('1',100,'in','BUY',1,100,0,-.1),
          fill('2',200,'out','SELL',1,110,10,-.1)]
    done,pending,issues=build_trades(rows,[first,last],[Obj(entry_order_id='in',combo='C1')],[])
    assert not pending and not issues
    assert len(done)==1
    assert done[0]['margin_used_usdt']==2.0
    assert round(done[0]['pnl'],2)==9.8
    assert done[0]['pnl_pct']==490.0
    assert summarize(done)[0]['PnL %']==490.0


def test_distinct_bingx_positions_same_symbol_side_are_not_mixed():
    orders=[Obj(order_id=oid,type=typ,position_side='LONG',
                raw={'leverage':'50X','positionID':pid})
            for oid,typ,pid in [
                ('in1','MARKET','pos1'),('in2','MARKET','pos2'),
                ('out1','STOP_MARKET','pos1'),('out2','TAKE_PROFIT_MARKET','pos2')]]
    rows=[fill('a',100,'in1','BUY',1,100,0,-.1),
          fill('b',110,'in2','BUY',1,100,0,-.1),
          fill('c',200,'out1','SELL',1,90,-10,-.1),
          fill('d',210,'out2','SELL',1,105,5,-.1)]
    links=[Obj(entry_order_id='in1',combo='C1'),Obj(entry_order_id='in2',combo='C2')]
    done,open_,issues=build_trades(rows,orders,links,[])
    assert not open_ and not issues
    assert {x['combo'] for x in done}=={'C1','C2'}
    assert {x['combo']:x['pnl_pct'] for x in done}=={'C1':-510.0,'C2':240.0}


def test_missing_leverage_does_not_invent_percent():
    rows=[fill('a',100,'in','BUY',1,100,0,0),fill('b',200,'out','SELL',1,110,10,-1)]
    done,_,_=build_trades(rows,[order('in','MARKET'),order('out','MARKET')],
                         [Obj(entry_order_id='in',combo='C3')],[])
    assert done[0]['pnl']==9
    assert done[0]['pnl_pct'] is None
    assert summarize(done)[0]['PnL %'] is None
