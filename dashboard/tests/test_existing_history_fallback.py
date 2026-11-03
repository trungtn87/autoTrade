"""Dashboard reads existing reconciled history before a new V2 migration."""
import json
from sqlalchemy import create_engine, text
from dashboard.autotrade_live.results import load_results, result_payload


def history_db():
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE history_rows (
            entry_order_id TEXT, symbol TEXT, combo TEXT, side TEXT,
            entry_time_ms BIGINT, exit_ms BIGINT, outcome TEXT,
            roi_pct REAL, status TEXT)"""))
        conn.execute(text("CREATE VIEW manual_dashboard_trade_results AS SELECT * FROM history_rows"))
        conn.execute(text("""INSERT INTO history_rows VALUES
            ('entry-1','BTC-USDT','C1','BUY',1000,2000,'W',1.25,'CLOSED'),
            ('entry-2','BTC-USDT','C1','SELL',3000,4000,'L',-0.75,'CLOSED'),
            ('entry-3','ETH-USDT','C7','BUY',5000,6000,'L',-0.50,'CLOSED')"""))
    return engine


def test_existing_history_is_visible_without_new_trade_results_table():
    engine = history_db()
    try:
        rows = load_results(engine)
        payload = result_payload(rows)
        assert len(rows) == 3
        assert payload['theory']['available'] is True
        assert payload['summary']['closed'] == 3
        combos = {(r['symbol'],r['combo']): r for r in payload['theory']['combos']}
        btc = combos['BTC-USDT','C1']
        assert btc['closed'] == 2 and btc['tp_count'] == 1 and btc['sl_count'] == 1
        assert btc['win_rate'] == 50 and btc['real_roi_total'] == 0.5
        assert [h['recorded_roi_pct'] for h in payload['theory']['history']] == [-0.5,-0.75,1.25]
    finally:
        engine.dispose()


def test_normalized_closed_trade_takes_precedence_without_duplicate():
    engine = history_db()
    try:
        real = dict(event_id='real',entry_order_id='entry-1',symbol='BTC-USDT',
                    combo='C1',side='BUY',status='CLOSED',opened_ms=1000,
                    closed_ms=2000,outcome='WIN',roi=2,pnl=2,margin=100,
                    exit_types=['TP'])
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE trade_results(event_id TEXT PRIMARY KEY,payload TEXT)"))
            conn.execute(text("INSERT INTO trade_results VALUES (:eid,:payload)"),
                         {'eid':'real','payload':json.dumps(real)})
        rows = load_results(engine)
        assert len(rows) == 3
        assert sum(r['entry_order_id']=='entry-1' for r in rows) == 1
        assert next(r for r in rows if r['entry_order_id']=='entry-1')['event_id']=='real'
    finally:
        engine.dispose()


def test_missing_both_sources_is_unavailable():
    engine=create_engine('sqlite://')
    try:
        assert load_results(engine) is None
        assert result_payload(None)['theory']['available'] is False
    finally:
        engine.dispose()
