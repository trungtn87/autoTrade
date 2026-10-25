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
    from autotrade_live.db import Order, Fill, Income, PositionHistory, get_engine, get_sessionmaker
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
                return {'positionHistory':[{'positionId':'551', 'symbol':'BTC-USDT', 'closeTime':1000}]}
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
        assert len(db.scalars(select(PositionHistory)).all()) == 1


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


def test_explicit_success_with_null_data_archived_as_empty():
    from unittest.mock import Mock
    from autotrade_live.bingx import BingXReadClient
    client = BingXReadClient(key='read-only', secret='fake-secret')
    client.min_delay = 0
    response = Mock(status_code=200)
    response.json.return_value = {'code': 0, 'data': None}
    client.http.get = Mock(return_value=response)
    for path in ['/openApi/swap/v2/user/income',
                 '/openApi/swap/v2/trade/forceOrders',
                 '/openApi/swap/v1/trade/positionHistory',
                 '/openApi/swap/v2/trade/fillHistory',
                 '/openApi/swap/v2/trade/allOrders']:
        assert client.get(path, {'symbol': 'BTC-USDT'}) == []
        assert client.null_data_counts[path] == 1


def test_null_does_not_mask_errors_or_missing_fields():
    import pytest
    from unittest.mock import Mock
    from autotrade_live.bingx import BingXReadClient
    client = BingXReadClient(key='read-only', secret='fake-secret')
    client.min_delay = 0
    response = Mock(status_code=200)
    client.http.get = Mock(return_value=response)
    response.json.return_value = {'code': 109400, 'data': None, 'msg': 'private'}
    with pytest.raises(RuntimeError, match='code=109400') as err:
        client.get('/openApi/swap/v2/user/income', {})
    assert 'private' not in str(err.value)
    response.json.return_value = {'code': 0}
    with pytest.raises(RuntimeError, match='missing data field'):
        client.get('/openApi/swap/v2/user/income', {})
    response.json.return_value = {'code': 0, 'data': None}
    with pytest.raises(RuntimeError, match='Unexpected BingX null data'):
        client.get('/openApi/swap/v2/unknown', {})


def test_real_fills_saved_even_when_income_null(tmp_path, monkeypatch):
    import autotrade_live.sync as module
    import json
    from unittest.mock import Mock
    from autotrade_live.bingx import BingXReadClient
    from autotrade_live.db import Order, Fill, SyncRun, get_engine, get_sessionmaker
    from sqlalchemy import select
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'null-income.db'))
    monkeypatch.setenv('DB_SCHEMA', '')
    class FakeClient(BingXReadClient):
        def __init__(self):
            super().__init__(key='read-only', secret='fake-secret')
            self.min_delay = 0
            def fake_get(url, **kwargs):
                response = Mock(status_code=200)
                if url.endswith('allOrders'):
                    data = [{'orderId': '10001', 'symbol': 'BTC-USDT',
                             'side': 'BUY', 'positionSide': 'LONG', 'time': 1000}]
                elif url.endswith('fillHistory'):
                    data = {'fill_history_orders': [{
                        'tradeId': '11', 'orderId': '10001', 'symbol': 'BTC-USDT',
                        'side': 'BUY', 'positionSide': 'LONG',
                        'qty': '1', 'price': '100', 'fee': '-0.1', 'time': 1000}]}
                elif url.endswith(('income', 'forceOrders', 'positionHistory')):
                    data = None
                else:
                    raise AssertionError(url)
                response.json.return_value = {'code': 0, 'data': data}
                return response
            self.http.get = fake_get
    monkeypatch.setattr(module, 'BingXReadClient', FakeClient)
    stats = module.sync(days=1, symbols=['BTC-USDT'], now_ms=86402000)
    Session = get_sessionmaker(get_engine())
    with Session() as db:
        assert len(db.scalars(select(Order)).all()) == 1
        assert len(db.scalars(select(Fill)).all()) == 1
        run = db.scalars(select(SyncRun)).one()
        assert run.status == 'success'
        assert json.loads(run.details)['null_data_windows']['/openApi/swap/v2/user/income'] == 2
    assert stats['null_data_windows']['/openApi/swap/v2/trade/forceOrders'] == 4


def test_position_history_live_wrapper_and_documented_alias():
    # Actual 2026-10-25 BingX response is data.positionHistory rather than docs' data.list.
    row = {'positionId': '709', 'symbol': 'ETH-USDT', 'closeTime': 1792908000000}
    candidates = ('positionHistory', 'list')
    assert extract_list({'positionHistory': [row]}, candidates) == [row]
    assert extract_list({'positionHistory': []}, candidates) == []
    assert extract_list({'total': 1, 'list': [row]}, candidates) == [row]


def test_position_history_pagination_with_live_wrapper():
    row1 = {'positionId': '701', 'symbol': 'BTC-USDT'}
    row2 = {'positionId': '702', 'symbol': 'BTC-USDT'}
    class Fake:
        def get(self, path, params):
            assert path.endswith('/positionHistory')
            assert params['pageSize'] == 1
            return {'positionHistory': {1: [row1], 2: [row2], 3: []}[params['pageIndex']]}
    rows = paged(Fake(), '/openApi/swap/v1/trade/positionHistory',
                 {'symbol': 'BTC-USDT', 'startTs': 100, 'endTs': 200},
                 ('positionHistory', 'list'), 1)
    assert rows == [row1, row2]


def test_real_bingx_fill_iso_time_and_realised_pnl():
    from datetime import datetime
    from autotrade_live.sync import as_fill
    row = as_fill({'tradeId': '123', 'orderId': '456', 'symbol': 'BTC-USDT',
                   'side': 'SELL', 'positionSide': 'LONG', 'qty': '0.01',
                   'price': '100000', 'realisedPNL': '-0.89586000',
                   'commission': '-0.2', 'filledTime': '2026-10-25T10:06:12.000+08:00'})
    assert row['timestamp_ms'] == int(datetime.fromisoformat('2026-10-25T10:06:12+08:00').timestamp() * 1000)
    assert row['realized_pnl'] == -.89586
    assert row['fee'] == -.2


def test_bingx_fill_missing_timestamp_is_rejected():
    import pytest
    from autotrade_live.sync import as_fill
    with pytest.raises(ValueError,match='missing valid timestamp'):
        as_fill({'tradeId': '123', 'orderId': '456', 'qty': '1', 'price': '100', 'filledTime':'not valid'})


def test_real_fill_entry_and_exit_combo_attribution():
    from types import SimpleNamespace as Obj
    from autotrade_live.sync import as_fill
    from autotrade_live.analytics import build_trades
    entry = as_fill({'tradeId':'1','orderId':'100','symbol':'ETH-USDT','side':'BUY','positionSide':'LONG','qty':'1','price':'100','realisedPNL':'0','commission':'-0.1','filledTime':'2026-10-23T10:06:12.000+08:00'})
    exit_ = as_fill({'tradeId':'2','orderId':'200','symbol':'ETH-USDT','side':'SELL','positionSide':'LONG','qty':'1','price':'110','realisedPNL':'10','commission':'-0.1','filledTime':'2026-10-24T10:06:12.000+08:00'})
    rows=[Obj(**row) for row in (entry,exit_)]
    done,pending,issues=build_trades(rows,[Obj(order_id='100',type='MARKET',position_side='LONG'),Obj(order_id='200',type='TAKE_PROFIT_MARKET',position_side='LONG')],[Obj(entry_order_id='100',combo='C7')],[])
    assert len(done)==1 and not pending and not issues
    assert done[0]['combo']=='C7'
    assert round(done[0]['pnl'],4)==9.8
