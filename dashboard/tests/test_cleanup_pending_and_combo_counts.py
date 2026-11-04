"""Dashboard reconciles saved V2 cleanup proof without creating ROI or exchange calls."""
import json

from sqlalchemy import create_engine, text

from dashboard.autotrade_live.results import load_results, result_payload


ENTRY = "2117723600702361600"
POSITION = "2117723600719138818"
ENTRY_MS = 1793740826000
CLOSE_MS = 1793751527000
PARENTS = {
    "sl": "2117723623334187008",
    "tp": "2117723651540881408",
    "trailing": "2117723668552978432",
}


def _new_db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE signal_links (
            entry_order_id TEXT, symbol TEXT, combo TEXT, side TEXT, signal_time_ms BIGINT)"""))
        conn.execute(text("""CREATE TABLE bingx_orders (
            order_id TEXT, symbol TEXT, side TEXT, position_side TEXT,
            type TEXT, status TEXT, filled_qty REAL, created_ms BIGINT,
            updated_ms BIGINT, raw TEXT)"""))
        conn.execute(text("""CREATE TABLE bingx_fills (
            order_id TEXT, symbol TEXT, side TEXT, position_side TEXT,
            qty REAL, price REAL, realized_pnl REAL, fee REAL, raw TEXT)"""))
        conn.execute(text("""CREATE TABLE processed_targets (
            event_id TEXT, target TEXT, payload TEXT, processed_at TIMESTAMP)"""))
        conn.execute(text("""INSERT INTO signal_links VALUES
            (:entry,'ETH-USDT','C12','BUY',:time)"""),
            {"entry":ENTRY, "time":ENTRY_MS-26000})
        conn.execute(text("""INSERT INTO bingx_orders VALUES
            (:entry,'ETH-USDT','BUY','LONG','MARKET','FILLED',0.04,
             :time,:time,:raw)"""),
            {"entry":ENTRY, "time":ENTRY_MS,
             "raw":json.dumps({"positionID":POSITION,"leverage":"50X"})})
        conn.execute(text("""INSERT INTO bingx_fills VALUES
            (:entry,'ETH-USDT','BUY','LONG',0.04,2289.59,0,-0.045792,'{}')"""),
            {"entry":ENTRY})
        payload = _journal()
        conn.execute(text("""INSERT INTO processed_targets VALUES
            ('event-eth','bingx_state',:payload,'2026-11-04 00:18:58')"""),
            {"payload":json.dumps(payload)})
    return engine


def _journal():
    def cancelled(parent, kind):
        return {"status":"CANCELLED", "orig_qty":"0.02",
                "executed_qty":"0", "analytics_order":{
                    "triggerOrderId":parent, "status":"CANCELLED",
                    "type":kind}}
    return {
        "order_id":ENTRY, "entry_filled":True,"position_id":POSITION,
        "intent":{"combo":102,"symbol":"ETH-USDT","side":"BUY",
                  "close_time":ENTRY_MS-26000},
        "protection":{k:{"order_id":v} for k,v in PARENTS.items()},
        "exit_cleanup":{
            "status":"done",
            "orders":{
                PARENTS["sl"]:{
                    "status":"FILLED", "orig_qty":"0.04",
                    "executed_qty":"0.04",
                    "analytics_order":{
                        "side":"SELL", "type":"STOP_MARKET",
                        "status":"FILLED", "symbol":"ETH-USDT",
                        "orderId":"2117773513477804032",
                        "positionID":POSITION,"updateTime":CLOSE_MS,
                        "executedQty":"0.04", "positionSide":"LONG",
                        "triggerOrderId":PARENTS["sl"]}},
                PARENTS["tp"]:cancelled(PARENTS["tp"],"TAKE_PROFIT_MARKET"),
                PARENTS["trailing"]:cancelled(PARENTS["trailing"],"TRAILING_STOP_MARKET")
            }
        }
    }


def _replace_journal(engine, mutate):
    with engine.begin() as conn:
        payload = json.loads(conn.execute(text(
            "SELECT payload FROM processed_targets WHERE event_id='event-eth'"
        )).scalar_one())
        mutate(payload)
        conn.execute(text(
            "UPDATE processed_targets SET payload=:payload WHERE event_id='event-eth'"
        ),{"payload":json.dumps(payload)})


def test_nov4_eth_cleanup_filled_before_archive_is_closed_pending_not_open():
    db = _new_db()
    try:
        row = load_results(db)[0]
        assert row["status"] == "CLOSED_PENDING"
        assert row["source"] == "v2_cleanup_pending_archive"
        assert row["reason"] == "archive_pending"
        assert row["closed_ms"] == CLOSE_MS
        assert row["exit_types"] == ["SL"]
        assert row["outcome"] is None and row["roi"] is None
        history = result_payload([row])
        assert history["summary"]["total"] == 1
        assert history["summary"]["closed"] == 0
        assert history["theory"]["history"][0]["exit_ms"] == CLOSE_MS
        assert history["theory"]["history"][0]["status"] == "closed_pending"
        c = history["theory"]["combos"][0]
        assert (c["total"],c["classified"],c["tp_count"],c["sl_count"]) == (1,0,0,0)
    finally:
        db.dispose()


def test_archive_catches_up_and_provides_fee_adjusted_realized_roi():
    db = _new_db()
    try:
        with db.begin() as conn:
            conn.execute(text("""INSERT INTO bingx_orders VALUES
                ('2117773513477804032','ETH-USDT','SELL','LONG',
                'STOP_MARKET','FILLED',0.04,:time,:time,:raw)"""),
                {"time":CLOSE_MS,"raw":json.dumps({
                    "positionID":POSITION,"triggerOrderId":PARENTS["sl"],
                    "profit":"-1.20","leverage":"50X"})})
            conn.execute(text("""INSERT INTO bingx_fills VALUES
                ('2117773513477804032','ETH-USDT','SELL','LONG',
                 0.04,2260,-1.20,-0.0452,'{}')"""))
        row = load_results(db)[0]
        assert row["status"] == "CLOSED"
        assert row["outcome"] == "LOSS"
        assert row["source"] == "bingx_db_roi_estimate"
        assert row["roi"] is not None and row["roi"] < 0
        assert row["closed_ms"] == CLOSE_MS
        c = result_payload([row])["theory"]["combos"][0]
        assert (c["total"],c["classified"],c["tp_count"],c["sl_count"]) == (1,1,0,1)
        assert c["win_rate"] == 0
    finally:
        db.dispose()


def test_cleanup_status_or_child_mismatch_does_not_invent_verified_close():
    for mutate in [
        lambda p: p["exit_cleanup"].update(status="not_done"),
        lambda p: p["exit_cleanup"]["orders"][PARENTS["sl"]].pop("analytics_order"),
        lambda p: p["exit_cleanup"]["orders"][PARENTS["sl"]].update(executed_qty="0.03"),
        lambda p: p["exit_cleanup"]["orders"][PARENTS["sl"]]["analytics_order"].update(
            triggerOrderId="different-parent"),
        lambda p: p["exit_cleanup"]["orders"][PARENTS["sl"]]["analytics_order"].update(
            positionID="different-position"),
    ]:
        db = _new_db()
        try:
            _replace_journal(db, mutate)
            row = load_results(db)[0]
            assert row["status"] == "UNRESOLVED"
            assert row["closed_ms"] is None
            assert row["roi"] is None
        finally:
            db.dispose()


def test_combo_count_reports_classified_out_of_total_without_fake_tp_sl():
    rows = [
        dict(symbol="BTC-USDT",combo="C10",side="BUY",status="CLOSED",
             opened_ms=1000,closed_ms=2000,outcome="LOSS",roi=None,pnl=None,margin=None),
        dict(symbol="BTC-USDT",combo="C10",side="SELL",status="UNRESOLVED",
             opened_ms=3000,closed_ms=None,outcome=None,roi=None,pnl=None,margin=None),
        dict(symbol="BTC-USDT",combo="C14",side="BUY",status="CLOSED",
             opened_ms=4000,closed_ms=5000,outcome="WIN",roi=6,pnl=1.2,margin=20),
        dict(symbol="BTC-USDT",combo="C14",side="SELL",status="CLOSED",
             opened_ms=6000,closed_ms=7000,outcome="LOSS",roi=-5,pnl=-1,margin=20),
        dict(symbol="BTC-USDT",combo="C14",side="SELL",status="CLOSED_PENDING",
             opened_ms=8000,closed_ms=9000,outcome=None,roi=None,pnl=None,margin=None),
    ]
    c = {r["combo"]:r for r in result_payload(rows)["theory"]["combos"]}
    assert (c["C10"]["total"],c["C10"]["classified"],c["C10"]["tp_count"],
            c["C10"]["sl_count"],c["C10"]["win_rate"]) == (2,1,0,1,0)
    assert (c["C14"]["total"],c["C14"]["classified"],c["C14"]["tp_count"],
            c["C14"]["sl_count"],c["C14"]["win_rate"]) == (3,2,1,1,50)
