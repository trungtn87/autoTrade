"""Real native child trigger IDs must resolve split Combo positions without guesses."""
import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from dashboard.autotrade_live.results import load_results, result_payload


def _ms(iso):
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _engine():
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.execute(text("""CREATE TABLE signal_links (
            entry_order_id TEXT, symbol TEXT, combo TEXT, side TEXT, signal_time_ms BIGINT)"""))
        db.execute(text("""CREATE TABLE bingx_orders (
            order_id TEXT, symbol TEXT, side TEXT, position_side TEXT, type TEXT,
            status TEXT, filled_qty REAL, created_ms BIGINT, updated_ms BIGINT, raw TEXT)"""))
        db.execute(text("""CREATE TABLE bingx_fills (
            order_id TEXT, symbol TEXT, side TEXT, position_side TEXT,
            qty REAL, price REAL, realized_pnl REAL, fee REAL, raw TEXT)"""))
        db.execute(text("""CREATE TABLE processed_targets (
            event_id TEXT, target TEXT, payload TEXT, processed_at TIMESTAMP)"""))

        def add_entry(oid, combo, pid, quantity, price, fee, at, *, journal=True):
            when = _ms(at)
            db.execute(text("""INSERT INTO bingx_orders VALUES
                (:id,'BTC-USDT','BUY','LONG','MARKET','FILLED',:qty,:when,:when,:raw)"""),
                dict(id=oid,qty=quantity,when=when,
                     raw=json.dumps({'positionID':pid,'leverage':'50X','profit':'0'})))
            db.execute(text("""INSERT INTO bingx_fills VALUES
                (:id,'BTC-USDT','BUY','LONG',:qty,:price,0,:fee,:raw)"""),
                dict(id=oid,qty=quantity,price=price,fee=fee,
                     raw=json.dumps({'commissionAsset':'USDT'})))
            if not journal:
                return
            db.execute(text("""INSERT INTO signal_links VALUES
                (:id,'BTC-USDT',:combo,'BUY',:when)"""),
                dict(id=oid,combo=combo,when=when))
            numeric = {'C2':2,'C14':104,'C16':106}[combo]
            body = dict(entry_filled=True,order_id=oid,position_id=pid,
                        intent=dict(symbol='BTC-USDT',combo=numeric,
                                    side='BUY',close_time=when),
                        protection={'sl':{'order_id':'parent-'+oid}})
            db.execute(text("""INSERT INTO processed_targets VALUES
                (:event,'bingx_state',:payload,'2026-10-30 00:00:00')"""),
                dict(event='event-'+oid,payload=json.dumps(body)))

        def add_sl(oid, pid, parent, quantity, price, pnl, fee, at):
            when = _ms(at)
            db.execute(text("""INSERT INTO bingx_orders VALUES
                (:id,'BTC-USDT','SELL','LONG','STOP_MARKET','FILLED',
                 :qty,:when,:when,:raw)"""),
                dict(id=oid,qty=quantity,when=when,
                     raw=json.dumps({'positionID':pid,'leverage':'50X',
                                     'profit':str(pnl),'triggerOrderId':parent})))
            db.execute(text("""INSERT INTO bingx_fills VALUES
                (:id,'BTC-USDT','SELL','LONG',:qty,:price,:pnl,:fee,:raw)"""),
                dict(id=oid,qty=quantity,price=price,pnl=pnl,fee=fee,
                     raw=json.dumps({'commissionAsset':'USDT'})))

        # Prior Combo entry + SL on the same position ID as a later C2 entry.
        add_entry('99','C14','shared-old',0.0012,80860,-0.048516,
                  '2026-10-28T06:00:00+07:00',journal=False)
        add_entry('102','C2','shared-old',0.0012,81639.1,-0.048983,
                  '2026-10-29T03:00:00+07:00')
        add_sl('990','shared-old','parent-old',0.0006,80280.7,
               -0.71846,-0.024084,'2026-10-29T08:48:00+07:00')
        add_sl('1020','shared-old','parent-102',0.0012,80874.6,
               -0.72428,-0.048525,'2026-10-29T08:10:00+07:00')

        # New C14 and C16 entered same position; both stop orders did fill,
        # but each child identifies a different exact protected parent.
        add_entry('114','C14','shared-new',0.0012,81770.2,-0.049062,
                  '2026-10-29T23:00:00+07:00')
        add_entry('116','C16','shared-new',0.0012,81758.8,-0.049055,
                  '2026-10-29T23:00:20+07:00')
        add_sl('1140','shared-new','parent-114',0.0012,81174.9,
               -0.70752,-0.048705,'2026-10-29T23:33:00+07:00')
        add_sl('1160','shared-new','parent-116',0.0012,80985.3,
               -0.93504,-0.048591,'2026-10-30T00:08:00+07:00')
    return engine


def test_matching_trigger_parent_resolves_overlapping_combo_trades():
    engine = _engine()
    try:
        rows = load_results(engine)
        by_entry = {r['entry_order_id']:r for r in rows}
        assert set(by_entry) == {'102','114','116'}
        expected = {'102':-41.942, '114':-41.034, '116':-52.629}
        for entry, roi in expected.items():
            row = by_entry[entry]
            assert row['status'] == 'CLOSED'
            assert row['outcome'] == 'LOSS'
            assert row['exit_types'] == ['SL']
            assert row['source'] == 'bingx_db_roi_estimate'
            assert abs(row['roi'] - roi) < 0.002
        assert by_entry['102']['closed_ms'] == _ms('2026-10-29T08:10:00+07:00')
        assert by_entry['114']['closed_ms'] == _ms('2026-10-29T23:33:00+07:00')
        assert by_entry['116']['closed_ms'] == _ms('2026-10-30T00:08:00+07:00')
        history = result_payload(rows)['theory']['history']
        assert sum(x['status']=='closed' for x in history) == 3
        assert len(history) == 3
    finally:
        engine.dispose()


def test_missing_parent_evidence_never_splits_shared_position():
    engine = _engine()
    try:
        with engine.begin() as db:
            db.execute(text("DELETE FROM processed_targets WHERE event_id='event-114'"))
        by_entry = {r['entry_order_id']:r for r in load_results(engine)}
        assert by_entry['114']['status'] == 'UNRESOLVED'
        assert by_entry['114']['roi'] is None
        assert by_entry['116']['status'] == 'CLOSED'
    finally:
        engine.dispose()


def test_mismatched_trigger_child_cannot_fabricate_close():
    engine = _engine()
    try:
        with engine.begin() as db:
            db.execute(text("""UPDATE bingx_orders SET
                raw=:raw WHERE order_id='1160'"""),
                {'raw':json.dumps({'positionID':'shared-new',
                                   'triggerOrderId':'parent-999',
                                   'profit':'-0.93504','leverage':'50X'})})
        by_entry = {r['entry_order_id']:r for r in load_results(engine)}
        assert by_entry['116']['status'] == 'UNRESOLVED'
        assert by_entry['116']['roi'] is None
    finally:
        engine.dispose()


def test_two_trigger_children_for_one_parent_are_not_double_counted():
    engine = _engine()
    try:
        with engine.begin() as db:
            db.execute(text("""UPDATE bingx_orders SET
                raw=:raw WHERE order_id='1160'"""),
                {'raw':json.dumps({'positionID':'shared-new',
                                   'triggerOrderId':'parent-114',
                                   'profit':'-0.93504','leverage':'50X'})})
        by_entry = {r['entry_order_id']:r for r in load_results(engine)}
        assert by_entry['114']['status'] == 'UNRESOLVED'
        assert by_entry['114']['roi'] is None
        assert by_entry['116']['status'] == 'UNRESOLVED'
    finally:
        engine.dispose()
