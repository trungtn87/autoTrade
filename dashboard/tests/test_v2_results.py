from types import SimpleNamespace as N
from unittest.mock import Mock
from autotrade_v2.analytics.reconciler import reconcile
from dashboard.autotrade_live.results import result_payload
from autotrade_v2.analytics.bingx import BingXReadClient
import pytest


def fixture():
    spec = dict(event_id='intent1', entry_order_id='1', symbol='BTC-USDT', combo='C1', side='BUY')
    order = N(order_id='1', symbol='BTC-USDT', side='BUY', filled_qty=2, status='FILLED')
    fill = N(order_id='1', symbol='BTC-USDT', side='BUY', qty=2, timestamp_ms=1000)
    return spec, order, fill


@pytest.mark.parametrize('quantities,status', [([], 'OPEN'), ([1], 'PARTIAL'), ([1,1], 'CLOSED'), ([2], 'CLOSED'), ([2,1], 'UNRESOLVED')])
def test_quantity_closure_is_one_result(quantities, status):
    spec, order, fill = fixture()
    events = [dict(entry_order_id='1', confidence='ORDER_ID', qty=q, type='TP' if i==0 else 'TRAILING', time_ms=2000+i) for i,q in enumerate(quantities)]
    rows = reconcile([spec], [order], [fill], events)
    assert len(rows) == 1 and rows[0]['status'] == status
    assert rows[0]['roi'] is None and rows[0]['pnl'] is None
    assert reconcile([spec], [order], [fill], events) == rows


def test_tp_plus_sl_still_one_closed_intent():
    spec, order, fill = fixture()
    events = [dict(entry_order_id='1', confidence='ORDER_ID', qty=1, type=kind, time_ms=2000) for kind in ('TP','SL')]
    rows = reconcile([spec], [order], [fill], events)
    assert len(rows) == 1 and rows[0]['status'] == 'CLOSED'
    assert rows[0]['exit_types'] == ['SL', 'TP']


def test_candidate_exit_cannot_close_trade():
    spec, order, fill = fixture()
    ev = dict(entry_order_id='1', confidence='PRICE_TIME_CANDIDATE', qty=2, type='SL', time_ms=2000)
    assert reconcile([spec], [order], [fill], [ev])[0]['status'] == 'OPEN'


def test_duplicate_mapping_is_unresolved():
    spec, order, fill = fixture()
    assert all(r['status']=='UNRESOLVED' for r in reconcile([spec, {**spec,'event_id':'intent2'}], [order], [fill], []))


def test_no_entry_fill_is_unresolved():
    spec, order, _ = fixture()
    assert reconcile([spec], [order], [], [])[0]['status'] == 'UNRESOLVED'


def test_missing_read_key_never_falls_back_to_trading_key(monkeypatch):
    monkeypatch.delenv('BINGX_READ_API_KEY', raising=False)
    monkeypatch.delenv('BINGX_READ_API_SECRET', raising=False)
    monkeypatch.setenv('BINGX_API_KEY', 'trading-test-fixture')
    with pytest.raises(ValueError): BingXReadClient()


@pytest.mark.parametrize('path', ['/openApi/swap/v2/trade/order', '/openApi/swap/v2/trade/cancelAllOpenOrders'])
def test_collector_rejects_execution_paths(path):
    client = BingXReadClient('test', 'test')
    client.http = Mock()
    with pytest.raises(ValueError): client.get(path, {})
    client.http.get.assert_not_called()


def test_database_payload_preserves_unknown_outcome_and_roi():
    spec, order, fill = fixture()
    ev = dict(entry_order_id='1', confidence='ORDER_ID', qty=2, type='SL', time_ms=2000)
    result = result_payload(reconcile([spec], [order], [fill], [ev]))
    assert result['theory']['combos'][0]['closed'] == 1
    assert result['theory']['combos'][0]['win_rate'] is None
    assert result['theory']['combos'][0]['real_roi_total'] is None
    assert result['theory']['history'][0]['recorded_roi_pct'] is None


def test_entry_still_filling_cannot_be_final():
    spec, order, fill = fixture()
    order.status = 'PARTIALLY_FILLED'
    ev = dict(entry_order_id='1', confidence='ORDER_ID', qty=2, type='TP', time_ms=2000)
    assert reconcile([spec], [order], [fill], [ev])[0]['status'] == 'UNRESOLVED'


def test_lifecycle_starts_one_worker_and_joins_on_stop(monkeypatch):
    from autotrade_v2.analytics.lifecycle import CollectorRuntime
    import threading
    runtime = CollectorRuntime()
    entered = threading.Event()
    def work():
        entered.set()
        runtime._stop.wait(5)
    monkeypatch.setattr(runtime, '_run', work)
    runtime.start()
    assert entered.wait(1)
    first = runtime._thread
    runtime.start()
    assert runtime._thread is first
    runtime.stop()
    assert not first.is_alive()


@pytest.mark.parametrize('code', [429, 500, 503])
def test_transport_retry_is_bounded_and_never_uses_write_methods(monkeypatch, code):
    from autotrade_v2.analytics import bingx
    import requests
    client = BingXReadClient('test', 'test')
    client.min_delay = 0
    monkeypatch.setattr(bingx.time, 'sleep', lambda _:None)
    response = Mock(status_code=code)
    response.raise_for_status.side_effect = requests.HTTPError('test-error')
    client.http = Mock()
    client.http.get.return_value = response
    with pytest.raises(RuntimeError): client.get('/openApi/swap/v2/trade/allOrders', {})
    assert client.http.get.call_count == 5
    client.http.post.assert_not_called()
    client.http.delete.assert_not_called()


def test_lost_database_lock_aborts_before_http_request():
    client = BingXReadClient('test', 'test')
    client.http = Mock()
    client.before_request = Mock(side_effect=RuntimeError('lock connection lost'))
    with pytest.raises(RuntimeError): client.get('/openApi/swap/v2/trade/allOrders', {})
    client.http.get.assert_not_called()


def test_catch_up_does_not_silently_skip_week_old_gap():
    from datetime import datetime, timedelta, timezone
    from autotrade_v2.analytics.archive import plan_archive
    now = datetime(2026, 11, 3, 10, 7, tzinfo=timezone.utc)
    assert plan_archive(now, now-timedelta(days=7), now-timedelta(hours=1)) == ('days', 8)
    with pytest.raises(RuntimeError): plan_archive(now, now-timedelta(days=100), None)


def test_absent_result_schema_is_explicitly_unavailable():
    payload = result_payload(None)
    assert payload['theory']['available'] is False
    assert payload['theory']['history'] == []
    assert payload['theory']['combos'] == []
