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
    assert client.http.get.call_count == (1 if code == 429 else 5)
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


def test_execution_priority_defers_without_sending_request():
    import threading
    from autotrade_v2.analytics.admission import archive_policy, ArchiveDeferred
    client = BingXReadClient('test', 'test')
    client.http = Mock()
    stopped = threading.Event()
    stopped.set()
    with archive_policy(lambda:False, stopped):
        with pytest.raises(ArchiveDeferred): client.get('/openApi/swap/v2/trade/allOrders', {})
    client.http.get.assert_not_called()


def test_archive_signs_after_waiting_not_before(monkeypatch):
    from autotrade_v2.analytics import bingx
    from urllib.parse import parse_qs
    clock = [100.0]
    monkeypatch.setattr(bingx.time, 'monotonic', lambda:clock[0])
    monkeypatch.setattr(bingx.time, 'time', lambda:clock[0])
    monkeypatch.setattr(bingx.time, 'sleep', lambda delay:clock.__setitem__(0, clock[0]+delay))
    client = BingXReadClient('test', 'test')
    client.last_request = clock[0]
    client.http = Mock()
    client.http.get.return_value = Mock(status_code=200)
    client.http.get.return_value.json.return_value = {'code':0,'data':[]}
    client.get('/openApi/swap/v2/trade/allOrders', {})
    params = parse_qs(client.http.get.call_args.kwargs['params'])
    assert int(params['timestamp'][0]) == 106000
    assert client.min_delay >= 6


@pytest.mark.parametrize('failure', [RuntimeError, KeyboardInterrupt])
def test_restart_resumes_committed_window_and_uses_coverage_watermark(tmp_path, monkeypatch, failure):
    from datetime import datetime, timezone
    from autotrade_v2.analytics import sync as module
    from autotrade_v2.analytics.archive import history_watermarks
    from dashboard.autotrade_live.db import SyncRun, get_engine, get_sessionmaker
    from sqlalchemy import select
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path/'resume.db'))
    seen = []
    fail = [True]
    day = module.DAY_MS
    class Client:
        null_data_counts = {}
        def get(self, path, params):
            if path.endswith('/allOrders'):
                seen.append(params['startTime'])
                if fail[0] and params['startTime'] >= 4*day:
                    raise failure('synthetic interruption')
            return []
    monkeypatch.setattr(module, 'BingXReadClient', Client)
    with pytest.raises(failure): module.sync(days=2, symbols=['BTC-USDT'], now_ms=5*day)
    fail[0] = False
    seen.clear()
    totals = module.sync(days=2, symbols=['BTC-USDT'], now_ms=6*day)
    assert seen[0] == 4*day  # First committed day is not fetched again.
    assert totals['end_ms'] == 5*day  # Resume frozen range, don't claim newer coverage.
    engine = get_engine()
    Session = get_sessionmaker(engine)
    with Session() as db:
        runs = db.scalars(select(SyncRun).order_by(SyncRun.id)).all()
        assert runs[-1].status == 'success'
        assert runs[0].status == ('interrupted' if failure is KeyboardInterrupt else 'failed')
        watermark, _ = history_watermarks([(runs[-1].finished_at, runs[-1].details)])
        assert watermark == datetime.fromtimestamp(5*day/1000, timezone.utc)
    engine.dispose()
    seen.clear()
    fresh = module.sync(days=2, symbols=['BTC-USDT'], now_ms=7*day)
    assert fresh['end_ms'] == 7*day
    assert seen[0] == 5*day  # Consumed failed ancestor cannot freeze future runs.


def test_admission_waits_in_place_and_heartbeats_before_resuming():
    from autotrade_v2.analytics.admission import archive_policy, check_admission
    decisions = iter([False, False, True])
    class Stop:
        def is_set(self): return False
        def wait(self, seconds): assert seconds == 5
    heartbeat = Mock()
    with archive_policy(lambda:next(decisions), Stop()):
        check_admission(heartbeat)
    assert heartbeat.call_count == 2


def test_dashboard_freshness_uses_coverage_not_resume_completion():
    from datetime import datetime, timezone
    from dashboard.autotrade_live.results import sync_metadata
    import json
    completed = datetime(2026, 11, 3, tzinfo=timezone.utc)
    old = datetime(2026, 10, 30, tzinfo=timezone.utc)
    run = N(status='success', finished_at=completed,
            details=json.dumps({'stage':'raw_sync','end_ms':int(old.timestamp()*1000)}))
    meta = sync_metadata(run)
    assert meta['time'] == old.isoformat()
    assert meta['completed_at'] == completed.isoformat()


def test_unknown_combo_is_preserved_as_unresolved_not_guessed():
    from autotrade_v2.analytics.results_store import result_specs
    exact, ledger = result_specs([('unknown-intent', dict(entry_filled=True, order_id='1',
        intent=dict(combo='unknown', symbol='BTC-USDT', side='BUY')))])
    assert exact == [] and len(ledger) == 1 and ledger[0]['combo'] is None
    _, order, fill = fixture()
    row = reconcile(ledger, [order], [fill], [])[0]
    assert row['event_id'] == 'unknown-intent'
    assert row['status'] == 'UNRESOLVED' and row['source'] == 'V2_STATE_UNVERIFIED'


def test_combo_roi_uses_actual_margin_and_mixed_exit_counts_one_outcome():
    base = dict(status='CLOSED', symbol='BTC-USDT', combo='C1', side='BUY', opened_ms=1000,
                closed_ms=2000, outcome='WIN', exit_types=['TP','SL'])
    rows = [{**base, 'pnl':10, 'margin':100, 'roi':10},
            {**base, 'closed_ms':3000, 'pnl':10, 'margin':1000, 'roi':1}]
    combo = result_payload(rows)['theory']['combos'][0]
    assert combo['closed'] == 2 and combo['tp_count'] == 2 and combo['sl_count'] == 0
    assert combo['real_roi_total'] == round(100*20/1100, 4)
    rows[0]['outcome'] = None
    assert result_payload(rows)['theory']['combos'][0]['tp_count'] is None


def test_restart_marks_old_outer_audit_interrupted_without_losing_cursor(tmp_path, monkeypatch):
    import json
    from datetime import timedelta
    from autotrade_v2.analytics.collect import PROCESS_STARTED_AT, recover_interrupted_runs
    from dashboard.autotrade_live.db import SyncRun, get_engine, get_sessionmaker, init_db
    from sqlalchemy import select
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path/'audit.db'))
    engine = get_engine()
    init_db(engine)
    Session = get_sessionmaker(engine)
    with Session.begin() as db:
        db.add(SyncRun(status='running', started_at=PROCESS_STARTED_AT-timedelta(hours=1), details='stage=archive_attempt'))
        db.add(SyncRun(status='running', started_at=PROCESS_STARTED_AT-timedelta(hours=1), details=json.dumps({'stage':'raw_sync','checkpoints':{'BTC-USDT':123}})))
        db.add(SyncRun(status='running', started_at=PROCESS_STARTED_AT+timedelta(seconds=1), details='stage=archive_attempt'))
    assert recover_interrupted_runs(engine) == 2
    with Session() as db:
        rows = db.scalars(select(SyncRun).order_by(SyncRun.id)).all()
        assert [r.status for r in rows] == ['interrupted','interrupted','running']
        assert json.loads(rows[1].details)['checkpoints']['BTC-USDT'] == 123
    engine.dispose()


def test_configuration_failure_is_observable_by_existing_alert_callback(monkeypatch):
    from autotrade_v2.analytics import lifecycle
    from autotrade_v2.analytics.errors import ArchiveConfigurationError
    seen = []
    def notify(state):
        seen.append(state)
        if state['state'] == 'failed': runtime.request_stop()
    runtime = lifecycle.CollectorRuntime(notify=notify)
    monkeypatch.setattr(lifecycle, 'ready_slot', lambda now:now)
    monkeypatch.setattr(lifecycle, 'run_once', Mock(side_effect=ArchiveConfigurationError('missing_read_credentials')))
    runtime._run()
    assert [s['state'] for s in seen] == ['running','failed']
    assert seen[-1]['configuration_code'] == 'missing_read_credentials'


def test_alert_callback_failure_cannot_stop_collector():
    from autotrade_v2.analytics.lifecycle import CollectorRuntime
    runtime = CollectorRuntime(notify=Mock(side_effect=RuntimeError('fixture callback failure')))
    runtime.status = {'state':'failed', 'error_class':'FixtureFailure'}
    runtime._publish()
    assert runtime.status['error_class'] == 'FixtureFailure'
