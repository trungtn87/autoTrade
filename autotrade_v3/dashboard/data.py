"""Read-only loader for the V3 Live Terminal. Queries only the ``autotrade_v3`` schema (SELECT only)."""
from __future__ import annotations

import os
import time
from typing import Any

from sqlalchemy import bindparam, create_engine, text

SCHEMA = "autotrade_v3"
WINDOW_ORDERS = 500


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(url: str | None = None):
    target = url if url is not None else database_url()
    if not target:
        raise RuntimeError("DATABASE_URL is not configured")
    # Same connection settings as the live engine (autotrade_v3/live/store.py):
    # Supabase's transaction pooler rejects server-side prepared statements, and
    # psycopg3 prepares them by default, so queries fail with 503 in production.
    return create_engine(
        target,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        connect_args={
            "connect_timeout": 10,
            "prepare_threshold": None,
            "options": "-c statement_timeout=5000",
        },
    )


def _rows(conn, sql: str, **params: Any) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def load_snapshot(engine) -> dict:
    """Return the raw rows the payload builder needs. Bounded: at most WINDOW_ORDERS orders."""
    now_ms = int(time.time() * 1000)
    with engine.connect() as conn:
        orders = _rows(conn, f"""
            SELECT id, event_id, symbol, combo, side, executed_qty, avg_price, sl_price, tp_price,
                   (EXTRACT(EPOCH FROM filled_at) * 1000)::bigint AS filled_ms
            FROM {SCHEMA}.execution_orders
            WHERE entry_order_id IS NOT NULL AND executed_qty > 0
            ORDER BY id DESC LIMIT {WINDOW_ORDERS}""")
        ids = [int(o["id"]) for o in orders] or [0]
        stmt_results = text(f"""
            SELECT execution_order_id, close_type, realized_pnl, fee,
                   (EXTRACT(EPOCH FROM closed_at) * 1000)::bigint AS closed_ms
            FROM {SCHEMA}.execution_results WHERE execution_order_id IN :ids""").bindparams(
            bindparam("ids", expanding=True))
        results = [dict(r._mapping) for r in conn.execute(stmt_results, {"ids": ids})]
        stmt_fin = text(f"""
            SELECT execution_order_id, gross_pnl, net_pnl, roi_pct, is_fully_closed
            FROM {SCHEMA}.execution_finances WHERE execution_order_id IN :ids""").bindparams(
            bindparam("ids", expanding=True))
        finances = {int(r["execution_order_id"]): r
                    for r in (dict(x._mapping) for x in conn.execute(stmt_fin, {"ids": ids}))}
        signals = _rows(conn, f"""
            SELECT event_id, symbol, combo, side, entry, tp, sl,
                   (EXTRACT(EPOCH FROM signal_time) * 1000)::bigint AS signal_ms
            FROM {SCHEMA}.strategy_signals ORDER BY signal_time DESC LIMIT 500""")
        errors = _rows(conn, f"""
            SELECT layer, symbol, error_code, message, discord_status,
                   (EXTRACT(EPOCH FROM created_at) * 1000)::bigint AS time_ms
            FROM {SCHEMA}.control_logs
            WHERE created_at >= now() - interval '24 hours'
            ORDER BY id DESC LIMIT 200""")
        candle_rows = _rows(conn, f"""
            SELECT symbol, MAX(close_time) AS last_close_ms
            FROM {SCHEMA}.candles WHERE timeframe = '15m' GROUP BY symbol""")
    return {
        "now_ms": now_ms,
        "orders": orders,
        "results": results,
        "finances": finances,
        "signals": signals,
        "errors": errors,
        "candle_last_close": {str(r["symbol"]): int(r["last_close_ms"]) for r in candle_rows},
    }
