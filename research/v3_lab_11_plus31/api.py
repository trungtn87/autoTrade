from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = Path(__file__).resolve().parent / "backtests.sqlite3"
app = FastAPI(title="AutoTrade V3_LAB_11 + TV31 Research API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8501", "http://localhost:8501"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, "Backtest database is not available yet")
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok" if DB_PATH.exists() else "waiting", "database": str(DB_PATH)}


@app.get("/api/research/runs")
def list_runs() -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute(
            "SELECT run_id,study_group,profile,start_time,end_time,created_at,status,portfolio_valid,metrics_json "
            "FROM research_runs ORDER BY created_at DESC,study_group,profile"
        ))


@app.get("/api/research/overview")
def overview(run_id: str = Query(...)) -> dict[str, Any]:
    with connect() as conn:
        run = conn.execute("SELECT * FROM research_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(404, "Run not found")
        result = dict(run)
        result["metrics"] = json.loads(result.pop("metrics_json"))
        result["positive_strategies"] = conn.execute(
            "SELECT count(*) FROM strategy_results WHERE run_id=? AND net_pnl>0", (run_id,)
        ).fetchone()[0]
        result["strategy_count"] = conn.execute(
            "SELECT count(*) FROM strategy_results WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        return result


@app.get("/api/research/strategies")
def strategies(
    run_id: str = Query(...), symbol: str | None = None, timeframe: str | None = None,
    sign: str = "ALL", sort: str = "net_pnl",
) -> list[dict[str, Any]]:
    allowed_sort = {"net_pnl", "roi", "total_trades", "max_drawdown"}
    order = sort if sort in allowed_sort else "net_pnl"
    clauses, params = ["run_id=?"], [run_id]
    if symbol:
        clauses.append("symbol=?"); params.append(symbol)
    if timeframe:
        clauses.append("timeframe=?"); params.append(timeframe)
    if sign == "POSITIVE": clauses.append("net_pnl>0")
    if sign == "NEGATIVE": clauses.append("net_pnl<0")
    sql = f"SELECT * FROM strategy_results WHERE {' AND '.join(clauses)} ORDER BY {order} DESC"
    with connect() as conn:
        return rows(conn.execute(sql, params))


@app.get("/api/research/strategy")
def strategy_detail(strategy_id: str = Query(...), run_id: str = Query(...)) -> dict[str, Any]:
    with connect() as conn:
        item = conn.execute(
            "SELECT * FROM strategy_results WHERE run_id=? AND strategy_id=?", (run_id, strategy_id)
        ).fetchone()
        if item is None:
            raise HTTPException(404, "Strategy result not found")
        trades = rows(conn.execute(
            "SELECT * FROM trade_ledger WHERE run_id=? AND strategy_id=? ORDER BY entry_time_ms DESC LIMIT 1000",
            (run_id, strategy_id),
        ))
        monthly = rows(conn.execute(
            "SELECT month,net_pnl FROM monthly_pnl WHERE run_id=? AND strategy_id=? ORDER BY month",
            (run_id, strategy_id),
        ))
        return {"summary": dict(item), "trades": trades, "monthly": monthly}


@app.get("/api/research/equity")
def equity(run_id: str = Query(...)) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute(
            "SELECT timestamp_ms,equity,balance,drawdown_dollars,drawdown_pct FROM equity_curve "
            "WHERE run_id=? ORDER BY timestamp_ms", (run_id,)
        ))
