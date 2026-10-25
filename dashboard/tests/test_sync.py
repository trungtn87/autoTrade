from autotrade_live.sync import as_order, as_fill, as_income, as_force
from autotrade_live.bingx import extract_list, fetch_window_split, paged


def test_large_order_id_preserved_as_string():
    value='1934500199328829001'
    row=as_order({'orderID':value,'symbol':'BTC-USDT','side':'BUY','positionSide':'LONG','time':1234})
    assert row['order_id'] == value


def test_api_fill_fields():
    row=as_fill({'tradeId':'123','orderId':'77','symbol':'BTC-USDT','qty':'0.002',
                 'price':'150000','realizedPnl':'2.53','fee':'-0.12','time':1000})
    assert row['qty'] == .002
    assert row['realized_pnl'] == 2.53
    assert row['fee'] == -.12


def test_forced_key_uniqueness():
    a=as_force({'orderId':'88','symbol':'BTC-USDT'},'ADL')
    b=as_force({'orderId':'88','symbol':'BTC-USDT'},'LIQUIDATION')
    assert a['force_key'] != b['force_key']


def test_split_saturated_response():
    class Fake:
        def get(self, path, params):
            lo,hi=params['startTime'],params['endTime']
            return [{'id':n} for n in range(lo,hi+1)][:5]
    rows=fetch_window_split(Fake(),'/x',{'startTime':0,'endTime':19},('list',),5,'startTime','endTime',minimum_ms=0)
    assert [x['id'] for x in rows] == list(range(20))


def test_idempotent_sync_into_sqlite(tmp_path, monkeypatch):
    import autotrade_live.sync as module
    from autotrade_live.db import Order, Fill, Income, get_engine, get_sessionmaker
    from sqlalchemy import select
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'db.sqlite'))
    monkeypatch.setenv('BINGX_SYMBOLS', 'BTC-USDT')
    class FakeClient:
        def __init__(self):
            pass
        def get(self, path, params):
            if path.endswith('/allOrders'):
                return [{'orderID':'199000000000000001','symbol':'BTC-USDT','side':'BUY','positionSide':'LONG',
                         'type':'MARKET','status':'FILLED','time':1000,'executedQty':'1'}]
            if path.endswith('/fillHistory'):
                return {'fill_orders':[{'tradeId':'501','orderId':'199000000000000001','symbol':'BTC-USDT',
                                        'side':'BUY','positionSide':'LONG','qty':'1','price':'50','fee':'-0.1',
                                        'realizedPnl':'0','time':1000}]}
            if path.endswith('/income'):
                return [{'tranId':'771','symbol':'BTC-USDT','incomeType':'TRADING_FEE','asset':'USDT','income':'-0.1','time':1000}]
            if path.endswith('/forceOrders'):
                return []
            if path.endswith('/positionHistory'):
                return {'list':[]}
            raise AssertionError(path)
    monkeypatch.setattr(module, 'BingXReadClient', FakeClient)
    module.sync(days=1,symbols=['BTC-USDT'],now_ms=86402000)
    module.sync(days=1,symbols=['BTC-USDT'],now_ms=86402000)
    engine=get_engine()
    Session=get_sessionmaker(engine)
    with Session() as db:
        assert len(db.scalars(select(Order)).all()) == 1
        assert len(db.scalars(select(Fill)).all()) == 1
        assert len(db.scalars(select(Income)).all()) == 1


def test_bingx_fillhistory_sdk_key_and_empty_pages():
    # BingX SDK calls the field fill_history_orders, not fill_orders.
    one = {'tradeId': '12345', 'orderId': '77', 'symbol': 'BTC-USDT',
           'side': 'SELL', 'positionSide': 'LONG', 'qty': '0.003',
           'price': '110000', 'commission': '-0.1',
           'filledTime': '1792917899000'}
    result = extract_list({'total': 1, 'fill_history_orders': [one]},
                          ('fill_history_orders', 'fill_orders'))
    assert result == [one]
    assert extract_list({'total': 0, 'fill_history_orders': None},
                        ('fill_history_orders',)) == []
    row = as_fill(result[0])
    assert row['timestamp_ms'] == 1792917899000
    assert row['fee'] == -.1


def test_paged_fill_history_sdk_response_not_truncated():
    class Fake:
        def get(self, path, params):
            if params['pageIndex'] == 1:
                return {'total': 3, 'fill_history_orders': [{'tradeId': '1'}, {'tradeId': '2'}]}
            return {'total': 3, 'fill_history_orders': [{'tradeId': '3'}]}
    rows = paged(Fake(), '/openApi/swap/v2/trade/fillHistory',
                 {'symbol': 'BTC-USDT', 'startTs': 100, 'endTs': 200},
                 ('fill_history_orders', 'fill_orders'), 2)
    assert [r['tradeId'] for r in rows] == ['1', '2', '3']


def test_unknown_bingx_response_keys_only_no_account_data_leaks():
    import pytest
    with pytest.raises(ValueError, match='keys=') as exc:
        extract_list({'unexpected_schema': [{'apiSecret': 'must-not-print'}]},
                     ('fill_history_orders',))
    assert 'must-not-print' not in str(exc.value)
    with pytest.raises(ValueError, match='type=NoneType'):
        extract_list(None, ('fill_history_orders',))


def test_empty_response_not_assumed_when_no_total():
    import pytest
    with pytest.raises(ValueError):
        extract_list({'fill_history_orders': None}, ('fill_history_orders',))
