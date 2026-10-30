"""Dashboard reads backfilled history without redirecting live collector writes."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autotrade_live.db import Base, Fill
from autotrade_live.mobile import _dashboard_fills


def test_dashboard_reads_unified_view_only_in_supabase_schema(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS autotrade_analytics")
        conn.exec_driver_sql(
            "CREATE TABLE autotrade_analytics.bingx_fills_dashboard ("
            "trade_id TEXT, order_id TEXT, symbol TEXT, side TEXT, position_side TEXT, "
            "qty REAL, price REAL, realized_pnl REAL, fee REAL, timestamp_ms BIGINT, raw TEXT)"
        )
        conn.exec_driver_sql(
            "INSERT INTO autotrade_analytics.bingx_fills_dashboard VALUES "
            "('backfill-1','entry-1','BTC-USDT','BUY','LONG',1,100,0,0,200,'{}')"
        )

    monkeypatch.setenv("DB_SCHEMA", "autotrade_analytics")
    with Session(engine) as session:
        fills = _dashboard_fills(session)
    assert len(fills) == 1
    assert fills[0].order_id == "entry-1"
    assert fills[0].price == 100

    monkeypatch.delenv("DB_SCHEMA")
    with Session(engine) as session:
        assert _dashboard_fills(session) == []


def test_collector_model_still_targets_original_fill_table():
    assert Fill.__tablename__ == "bingx_fills"
