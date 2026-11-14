"""Isolated real PostgreSQL check for the V3 migration and V3 PostgresStore.

Requires dev-only `pip install pgserver`. No application DATABASE_URL, exchange
client, credentials or production data are used.
"""
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

import pandas as pd
import pgserver
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from autotrade_v3.live.store import CLAIMED, EXISTS, CandleStore, PostgresStore

MIGRATION = (ROOT / "supabase/migrations/20261114000000_v3_five_layer_tables.sql").read_text()
TABLES = {"candles", "strategy_signals", "execution_orders", "execution_results", "execution_finances", "control_logs"}

with TemporaryDirectory(prefix="v3-pg-") as tmp:
    server = pgserver.get_server(Path(tmp) / "pgdata", cleanup_mode="delete")
    uri = server.get_uri()
    try:
        with psycopg.connect(uri, autocommit=True) as con:
            # Simulate the legacy tables that must stay untouched.
            con.execute("create table public.candles(symbol text, timeframe text, open_time bigint, open double precision,"
                        " high double precision, low double precision, close double precision, volume double precision,"
                        " close_time bigint, primary key(symbol,timeframe,open_time))")
            con.execute("insert into public.candles values ('BTC-USDT','15m',1000,1,2,0.5,1.5,10,1999),"
                        "('ETH-USDT','15m',1000,1,2,0.5,1.5,10,1999)")
            con.execute("create table public.system_events(id serial primary key, payload text)")
            con.execute("insert into public.system_events(payload) values ('keep me')")
            con.execute("create schema autotrade_analytics")
            con.execute("create table autotrade_analytics.bingx_orders(order_id varchar(90) primary key, symbol text,"
                        " side text, position_side text, type text, status text, avg_price float, filled_qty float,"
                        " updated_ms bigint, raw json)")
            con.execute("create table autotrade_analytics.bingx_fills(trade_id varchar(90), order_id varchar(90),"
                        " symbol text, qty float, price float, realized_pnl float, fee float, timestamp_ms bigint,"
                        " primary key(trade_id, symbol))")
            for _ in range(2):   # idempotent: second run is a no-op
                con.execute(MIGRATION)
            got = {r[0] for r in con.execute(
                "select table_name from information_schema.tables where table_schema='autotrade_v3'").fetchall()}
            assert got == TABLES, got
            assert con.execute("select count(*) from autotrade_v3.candles").fetchone()[0] == 2
            assert con.execute("select payload from public.system_events").fetchone()[0] == "keep me"
            assert con.execute("select count(*) from public.candles").fetchone()[0] == 2
            cols = [r[0] for r in con.execute(
                "select column_name from information_schema.columns where table_schema='autotrade_v3'"
                " and table_name='strategy_signals' order by ordinal_position").fetchall()]
            assert cols == ["event_id", "symbol", "combo", "side", "signal_time", "entry", "tp", "sl", "created_at"], cols
            assert "DROP " not in MIGRATION.upper() and "ALTER TABLE PUBLIC" not in MIGRATION.upper()

        store = PostgresStore(uri)
        now = datetime.now(timezone.utc)
        store.insert_signal({"event_id": "E1", "symbol": "BTC-USDT", "combo": "C1", "side": "BUY", "signal_time": now,
                             "entry": 100000.0, "tp": 102000.0, "sl": 99200.0})
        store.insert_signal({"event_id": "E1", "symbol": "BTC-USDT", "combo": "C1", "side": "BUY", "signal_time": now,
                             "entry": 1.0, "tp": 2.0, "sl": 0.5})   # duplicate ignored, original kept
        row = {"event_id": "E1", "client_order_id": "v3e-abc", "symbol": "BTC-USDT", "combo": "C1", "side": "BUY",
               "leverage": 50, "requested_qty": 0.001}
        assert store.claim_execution(row) == CLAIMED
        assert store.claim_execution(row) == EXISTS
        assert store.claim_execution({**row, "event_id": "E2"}) == EXISTS   # same clientOrderId
        store.update_execution("E1", {"stage": "protected", "entry_order_id": "111", "entry_status": "FILLED",
                                      "executed_qty": "0.001", "avg_price": 100000.0, "filled_at": now,
                                      "sl_order_id": "222", "tp_order_id": "333", "trailing_order_id": "444",
                                      "raw_entry": {"post": {"apiKey": "secret"}}, "raw_protection": {"sl": {}},
                                      "error": None, "not_a_column": 1})
        store.append_evidence("E1", {"mechanism": 1, "signature": "zzz"})
        store.append_evidence("E1", {"mechanism": 2})
        log_id = store.insert_control_log({"layer": "L3", "step": "sl_place", "message": "x https://discord.com/api/webhooks/1/2",
                                           "details": {"token": "t"}, "event_id": "E1"})
        store.set_discord_status(log_id, "sent")
        execs = store.executions_for_sync(now - timedelta(days=7))
        assert len(execs) == 1 and execs[0]["entry_order_id"] == "111"
        from autotrade_v3.live.l4_control import ControlLog, project_results

        class History:   # BingX order-history GET (MOCK); V3 never reads the V2 archive
            def get_order_history(self, symbol, start_ms, end_ms, required_ids=None):
                return [{"orderId": "111", "symbol": symbol, "status": "FILLED", "commission": "-0.05"},
                        {"orderId": "333", "symbol": symbol, "status": "FILLED", "executedQty": "0.0005",
                         "avgPrice": "102000", "updateTime": 1763000000000, "profit": "1", "commission": "-0.03"}]
        client = History()
        counts = project_results(store, ControlLog(store), client)
        assert counts == {"executions": 1, "results": 1, "finances": 1}, counts
        project_results(store, ControlLog(store), client)   # upserts are idempotent
        with psycopg.connect(uri) as con:
            ex = con.execute("select stage, l4_evidence, raw_entry::text from autotrade_v3.execution_orders").fetchone()
            assert ex[0] == "protected" and len(ex[1]) == 2 and "secret" not in ex[2] and "zzz" not in str(ex[1])
            cl = con.execute("select discord_status, message, details::text from autotrade_v3.control_logs").fetchone()
            assert cl[0] == "sent" and "webhooks/1" not in cl[1] and '"t"' not in cl[2]
            assert con.execute("select count(*) from autotrade_v3.execution_results").fetchone()[0] == 1
            fin = con.execute("select leverage, gross_pnl, is_fully_closed from autotrade_v3.execution_finances").fetchone()
            assert float(fin[0]) == 50 and float(fin[1]) == 1.0 and fin[2] is False
            assert con.execute("select entry from autotrade_v3.strategy_signals").fetchone()[0] == 100000

        cs = CandleStore(uri)
        assert cs.table == "autotrade_v3.candles"
        frame = pd.DataFrame([{"open_time": 2000 + i, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 3,
                               "close_time": 2999 + i} for i in range(5)])
        cs.upsert("BTC-USDT", "15m", frame)
        cs.trim("BTC-USDT", "15m", 3)
        assert cs.stats("BTC-USDT", "15m")["count"] == 3
        assert list(cs.load("BTC-USDT", "15m")["open_time"]) == [2002, 2003, 2004]
        print("PASS: V3 migration (6 tables, idempotent, legacy untouched) + PostgresStore on isolated PostgreSQL",
              "")
    finally:
        server.cleanup()
