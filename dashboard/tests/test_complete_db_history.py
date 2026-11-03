"""Dashboard reads every linked order already stored in Supabase, with no BingX calls."""
import json

from sqlalchemy import create_engine, text

from dashboard.autotrade_live.results import load_results, result_payload


def _engine(with_history=False):
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.execute(text("""CREATE TABLE signal_links (
            entry_order_id TEXT, symbol TEXT, combo TEXT, side TEXT, signal_time_ms BIGINT)"""))
        db.execute(text("""CREATE TABLE bingx_orders (
            order_id TEXT, symbol TEXT, side TEXT, position_side TEXT, type TEXT,
            status TEXT, filled_qty REAL, created_ms BIGINT, updated_ms BIGINT, raw TEXT)"""))
        db.execute(text("""INSERT INTO signal_links VALUES
            ('entry-a','BTC-USDT','C1','BUY',1000),
            ('entry-b','ETH-USDT','C7','BUY',3000),
            ('entry-c','ETH-USDT','C4','BUY',3100)"""))
        data = [
            ('entry-a','BTC-USDT','BUY','LONG','MARKET','FILLED',1,1000,1000,'P1',None),
            ('exit-a','BTC-USDT','SELL','LONG','STOP_MARKET','FILLED',1,2000,2000,'P1','-2.3'),
            ('entry-b','ETH-USDT','BUY','LONG','MARKET','FILLED',1,3000,3000,'P2',None),
            ('entry-c','ETH-USDT','BUY','LONG','MARKET','FILLED',1,3100,3100,'P2',None),
            ('exit-bc','ETH-USDT','SELL','LONG','STOP_MARKET','FILLED',2,4000,4000,'P2','-3.4'),
        ]
        for oid, sym, side, pside, kind, state, qty, opened, updated, pid, profit in data:
            raw = {'positionID':pid}
            if profit is not None:
                raw['profit'] = profit
            db.execute(text("""INSERT INTO bingx_orders VALUES
                (:oid,:sym,:side,:pside,:kind,:state,:qty,:opened,:updated,:raw)"""),
                dict(oid=oid,sym=sym,side=side,pside=pside,kind=kind,
                     state=state,qty=qty,opened=opened,updated=updated,
                     raw=json.dumps(raw)))
        if with_history:
            db.execute(text("""CREATE TABLE excel (
                entry_order_id TEXT, symbol TEXT, combo TEXT, side TEXT,
                entry_time_ms BIGINT, exit_ms BIGINT, outcome TEXT, roi_pct REAL,
                status TEXT)"""))
            db.execute(text("""CREATE VIEW manual_dashboard_trade_results
                AS SELECT * FROM excel"""))
            db.execute(text("""INSERT INTO excel VALUES
                ('entry-a','BTC-USDT','C1','BUY',1000,2000,'L',-0.8,'CLOSED')"""))
    return engine


def test_db_only_reads_three_real_entries_without_faking_shared_position_result():
    engine = _engine()
    try:
        rows = load_results(engine)
        assert len(rows) == 3
        by_id = {r['entry_order_id']:r for r in rows}
        assert by_id['entry-a']['status'] == 'CLOSED'
        assert by_id['entry-a']['outcome'] == 'LOSS'
        assert by_id['entry-a']['roi'] is None  # gross profit is not fee-adjusted ROI
        assert by_id['entry-b']['status'] == 'UNRESOLVED'
        assert by_id['entry-c']['status'] == 'UNRESOLVED'
        payload = result_payload(rows)
        assert payload['summary'] == {'closed':1, 'total':3}
        assert payload['unresolved'] == 2
        assert len(payload['theory']['history']) == 3
        assert [v['entry_ms'] for v in payload['theory']['history']] == [3100,3000,1000]
        btc = next(r for r in payload['theory']['combos'] if r['symbol']=='BTC-USDT')
        assert btc['total'] == btc['closed'] == 1
        eth = [r for r in payload['theory']['combos'] if r['symbol']=='ETH-USDT']
        assert sum(r['total'] for r in eth) == 2
        assert all(r['closed'] == 0 and r['real_roi_total'] is None for r in eth)
    finally:
        engine.dispose()


def test_manual_history_precedence_and_no_duplicate_entry():
    engine = _engine(with_history=True)
    try:
        rows = load_results(engine)
        assert len(rows) == 3
        item = next(r for r in rows if r['entry_order_id']=='entry-a')
        assert item['source']=='existing_history'
        assert item['roi']==-0.8
        payload = result_payload(rows)
        assert payload['summary']['total'] == 3
        assert payload['summary']['closed'] == 1
        btc = next(r for r in payload['theory']['combos'] if r['symbol']=='BTC-USDT')
        assert btc['real_roi_total'] == -0.8
    finally:
        engine.dispose()


def test_without_verified_exit_no_false_closed_or_roi():
    engine = _engine()
    try:
        with engine.begin() as db:
            db.execute(text("DELETE FROM bingx_orders WHERE order_id='exit-a'"))
        rows = load_results(engine)
        result = next(r for r in rows if r['entry_order_id']=='entry-a')
        assert result['status'] == 'UNRESOLVED'
        assert result['closed_ms'] is None
        assert result['roi'] is None
        assert result['outcome'] is None
    finally:
        engine.dispose()


def test_v2_state_is_visible_even_when_archive_has_not_copied_new_orders():
    """The live V2 database is the source of 3-Nov orders absent from BingX mirror."""
    engine = _engine()
    try:
        with engine.begin() as db:
            db.execute(text("""CREATE TABLE processed_targets (
                event_id TEXT, target TEXT, payload TEXT, processed_at TIMESTAMP)"""))
            db.execute(text("""CREATE TABLE system_events (
                event_id TEXT, event_key TEXT, created_at TIMESTAMP)"""))
            base = {'entry_filled':True, 'order_id':'entry-a',
                    'intent':{'symbol':'BTC-USDT','side':'BUY','combo':1,'close_time':1000}}
            new = {'entry_filled':True, 'order_id':'new-v2-123'.replace('new-v2-',''),
                   'intent':{'symbol':'ETH-USDT','side':'SELL','combo':102,
                             'close_time':1793642399999}}
            db.execute(text("""INSERT INTO processed_targets VALUES
                ('event-old','bingx_state',:old,'2026-11-03 06:00:00'),
                ('event-new','bingx_state',:new,'2026-11-03 06:49:00')"""),
                {'old':json.dumps(base),'new':json.dumps(new)})
            db.execute(text("""INSERT INTO system_events VALUES
                ('event-new','L3.EXEC.ORDER_FILLED','2026-11-02 18:00:45+00:00')"""))
        rows = load_results(engine)
        assert len(rows) == 4  # 3 mirrored entries + 1 live-only V2 order
        assert sum(r['entry_order_id']=='entry-a' for r in rows) == 1
        latest = next(r for r in rows if r['entry_order_id']=='123')
        assert latest['combo']=='C12' and latest['symbol']=='ETH-USDT'
        assert latest['source']=='v2_state_db'
        assert latest['status']=='UNRESOLVED'
        assert latest['roi'] is None and latest['outcome'] is None
        from datetime import datetime, timezone
        expected = int(datetime.fromisoformat('2026-11-02T18:00:45+00:00').timestamp()*1000)
        assert latest['opened_ms']==expected
        assert result_payload(rows)['summary']['total']==4
    finally:
        engine.dispose()
