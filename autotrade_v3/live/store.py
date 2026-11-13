"""Persistence for the six V3 tables (schema ``autotrade_v3``).

Every write here is best-effort from the trading point of view: callers catch
exceptions, record them in control_logs/ERROR, and keep trading. The one
exception is the execution claim, which is the single agreed dedupe
(``client_order_id`` UNIQUE): the same event never gets a second Entry POST.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .sanitize import clean, dumps

SCHEMA = "autotrade_v3"

CLAIMED = "claimed"
EXISTS = "exists"

EXECUTION_COLUMNS = (
    "leverage", "requested_qty", "entry_order_id", "entry_status", "executed_qty", "avg_price", "filled_at",
    "exit_mode", "sl_order_id", "sl_price", "sl_qty", "sl_status", "tp_order_id", "tp_price", "tp_qty",
    "tp_status", "trailing_order_id", "trailing_activation_price", "trailing_callback_rate", "trailing_qty",
    "trailing_status", "protection_status", "stage", "error", "raw_entry", "raw_protection",
)
JSON_COLUMNS = {"raw_entry", "raw_protection", "l4_evidence", "details", "raw"}


def _pg_connect(url: str):
    import psycopg
    # prepare_threshold=None keeps the Supabase transaction pooler happy.
    return psycopg.connect(url, connect_timeout=10, prepare_threshold=None,
                           options="-c statement_timeout=5000")


def ms_to_dt(ms: int | float | None) -> datetime | None:
    if not ms:
        return None
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)


class CandleStore:
    """L1 candle table. Postgres in production; SQLite only for offline tests."""

    def __init__(self, database_url: str = "", sqlite_path: str = ":memory:"):
        self.database_url = (database_url or "").strip()
        self.is_postgres = self.database_url.startswith(("postgres://", "postgresql://"))
        self.table = f"{SCHEMA}.candles" if self.is_postgres else "candles"
        self._lock = threading.RLock()
        self._sqlite = None
        if not self.is_postgres:
            self._sqlite = sqlite3.connect(sqlite_path if sqlite_path else ":memory:", check_same_thread=False)
            self._sqlite.execute(
                "CREATE TABLE IF NOT EXISTS candles(symbol TEXT NOT NULL, timeframe TEXT NOT NULL,"
                " open_time INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,"
                " close REAL NOT NULL, volume REAL NOT NULL, close_time INTEGER NOT NULL,"
                " PRIMARY KEY(symbol,timeframe,open_time))")
            self._sqlite.commit()

    @property
    def backend(self) -> str:
        return "supabase_postgres" if self.is_postgres else "sqlite_test_only"

    def _run(self, sql: str, params=(), many: bool = False, fetch: bool = False):
        with self._lock:
            if self.is_postgres:
                with _pg_connect(self.database_url) as con, con.cursor() as cur:
                    sql = sql.replace("?", "%s")
                    if many:
                        cur.executemany(sql, params)
                    else:
                        cur.execute(sql, params)
                    rows = cur.fetchall() if fetch else None
                    con.commit()
                    return rows
            cur = self._sqlite.cursor()
            if many:
                cur.executemany(sql, params)
            else:
                cur.execute(sql, params)
            rows = cur.fetchall() if fetch else None
            self._sqlite.commit()
            return rows

    def upsert(self, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        rows = [(symbol, timeframe, int(r.open_time), float(r.open), float(r.high), float(r.low),
                 float(r.close), float(r.volume), int(r.close_time)) for r in df.itertuples(index=False)]
        self._run(
            f"INSERT INTO {self.table}(symbol,timeframe,open_time,open,high,low,close,volume,close_time)"
            " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,timeframe,open_time) DO UPDATE SET"
            " open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,"
            " volume=excluded.volume, close_time=excluded.close_time", rows, many=True)
        return len(rows)

    def load(self, symbol: str, timeframe: str = "15m", limit: int | None = None) -> pd.DataFrame:
        sql = (f"SELECT open_time,open,high,low,close,volume,close_time FROM {self.table}"
               " WHERE symbol=? AND timeframe=? ORDER BY open_time DESC")
        params: list[Any] = [symbol, timeframe]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = list(self._run(sql, params, fetch=True) or [])
        rows.reverse()
        return pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])

    def stats(self, symbol: str, timeframe: str = "15m") -> dict:
        rows = self._run(f"SELECT COUNT(*),MAX(open_time),MAX(close_time) FROM {self.table}"
                         " WHERE symbol=? AND timeframe=?", (symbol, timeframe), fetch=True) or [(0, None, None)]
        row = rows[0]
        return {"count": int(row[0] or 0),
                "latest_open_time": int(row[1]) if row[1] is not None else None,
                "latest_close_time": int(row[2]) if row[2] is not None else None}

    def trim(self, symbol: str, timeframe: str, keep: int) -> None:
        self._run(f"DELETE FROM {self.table} WHERE symbol=? AND timeframe=? AND open_time NOT IN("
                  f"SELECT open_time FROM {self.table} WHERE symbol=? AND timeframe=?"
                  " ORDER BY open_time DESC LIMIT ?)", (symbol, timeframe, symbol, timeframe, int(keep)))


class MemoryStore:
    """Offline store with the same API as PostgresStore (tests and no-DB mode)."""

    def __init__(self):
        self._lock = threading.RLock()
        self.signals: dict[str, dict] = {}
        self.executions: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.finances: dict[int, dict] = {}
        self.control_logs: list[dict] = []
        self.archive_orders: list[dict] = []
        self.archive_fills: list[dict] = []
        self.fail: set[str] = set()   # names of methods that should raise (fault injection)
        self._next = 1

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail or "*" in self.fail:
            raise RuntimeError(f"simulated database failure in {name}")

    def insert_signal(self, row: dict) -> None:
        self._maybe_fail("insert_signal")
        with self._lock:
            self.signals.setdefault(row["event_id"], clean(row))

    def claim_execution(self, row: dict) -> str:
        self._maybe_fail("claim_execution")
        with self._lock:
            if row["event_id"] in self.executions or any(
                    e["client_order_id"] == row["client_order_id"] for e in self.executions.values()):
                return EXISTS
            self.executions[row["event_id"]] = {"id": self._next, "l4_evidence": [], "stage": "claimed",
                                                **clean(row)}
            self._next += 1
            return CLAIMED

    def update_execution(self, event_id: str, fields: dict) -> None:
        self._maybe_fail("update_execution")
        with self._lock:
            row = self.executions.get(event_id)
            if row is None:   # same as Postgres UPDATE: no row, nothing written
                return
            row.update(clean({k: v for k, v in fields.items() if k in EXECUTION_COLUMNS}))

    def append_evidence(self, event_id: str, evidence: dict) -> None:
        self._maybe_fail("append_evidence")
        with self._lock:
            row = self.executions.get(event_id)
            if row is None:
                return
            row.setdefault("l4_evidence", []).append(clean(evidence))

    def insert_control_log(self, row: dict) -> int:
        self._maybe_fail("insert_control_log")
        with self._lock:
            rec = {"id": len(self.control_logs) + 1, "created_at": datetime.now(timezone.utc).isoformat(),
                   "discord_status": "pending", **clean(row)}
            self.control_logs.append(rec)
            return rec["id"]

    def set_discord_status(self, log_id: int, status: str) -> None:
        self._maybe_fail("set_discord_status")
        with self._lock:
            for rec in self.control_logs:
                if rec["id"] == log_id:
                    rec["discord_status"] = status

    def executions_for_sync(self, since: datetime) -> list[dict]:
        self._maybe_fail("executions_for_sync")
        with self._lock:
            return [dict(r) for r in self.executions.values() if r.get("entry_order_id") and r.get("executed_qty")]

    def archive_rows(self, order_ids: list[str]) -> tuple[list[dict], list[dict]]:
        self._maybe_fail("archive_rows")
        ids = {str(x) for x in order_ids}
        orders = [o for o in self.archive_orders if str(o.get("order_id")) in ids
                  or str((o.get("raw") or {}).get("triggerOrderId") or "") in ids]
        all_ids = ids | {str(o.get("order_id")) for o in orders}
        return orders, [f for f in self.archive_fills if str(f.get("order_id")) in all_ids]

    def upsert_result(self, row: dict) -> None:
        self._maybe_fail("upsert_result")
        with self._lock:
            self.results[row["close_order_id"]] = clean(row)

    def upsert_finance(self, row: dict) -> None:
        self._maybe_fail("upsert_finance")
        with self._lock:
            self.finances[row["execution_order_id"]] = clean(row)


class PostgresStore:
    """Supabase Postgres implementation. One short connection per operation."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def _exec(self, sql: str, params=(), fetch: str | None = None):
        with _pg_connect(self.database_url) as con, con.cursor() as cur:
            cur.execute(sql, params)
            out = None
            if fetch == "one":
                out = cur.fetchone()
            elif fetch == "all":
                cols = [d.name for d in cur.description]
                out = [dict(zip(cols, r)) for r in cur.fetchall()]
            con.commit()
            return out

    def insert_signal(self, row: dict) -> None:
        self._exec(
            f"INSERT INTO {SCHEMA}.strategy_signals(event_id,symbol,combo,side,signal_time,entry,tp,sl)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING",
            (row["event_id"], row["symbol"], row["combo"], row["side"], row["signal_time"],
             row["entry"], row["tp"], row["sl"]))

    def claim_execution(self, row: dict) -> str:
        got = self._exec(
            f"INSERT INTO {SCHEMA}.execution_orders(event_id,client_order_id,symbol,combo,side,leverage,requested_qty,stage)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,'claimed') ON CONFLICT DO NOTHING RETURNING id",
            (row["event_id"], row["client_order_id"], row["symbol"], row["combo"], row["side"],
             row.get("leverage"), row.get("requested_qty")), fetch="one")
        return CLAIMED if got else EXISTS

    def update_execution(self, event_id: str, fields: dict) -> None:
        cols = [k for k in fields if k in EXECUTION_COLUMNS]
        if not cols:
            return
        values = []
        for k in cols:
            v = fields[k]
            values.append(dumps(v) if k in JSON_COLUMNS else (clean(v) if isinstance(v, str) else v))
        sets = ", ".join(f"{k}=%s" + ("::jsonb" if k in JSON_COLUMNS else "") for k in cols)
        self._exec(f"UPDATE {SCHEMA}.execution_orders SET {sets}, updated_at=now() WHERE event_id=%s",
                   (*values, event_id))

    def append_evidence(self, event_id: str, evidence: dict) -> None:
        self._exec(f"UPDATE {SCHEMA}.execution_orders SET l4_evidence = l4_evidence || jsonb_build_array(%s::jsonb),"
                   " updated_at=now() WHERE event_id=%s", (dumps(evidence), event_id))

    def insert_control_log(self, row: dict) -> int:
        got = self._exec(
            f"INSERT INTO {SCHEMA}.control_logs(layer,symbol,event_id,order_id,step,error_code,message,details,discord_status)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING id",
            (row["layer"], row.get("symbol"), row.get("event_id"), row.get("order_id"), row.get("step"),
             row.get("error_code"), clean(row["message"]), dumps(row.get("details") or {}),
             row.get("discord_status") or "pending"), fetch="one")
        return int(got[0])

    def set_discord_status(self, log_id: int, status: str) -> None:
        self._exec(f"UPDATE {SCHEMA}.control_logs SET discord_status=%s WHERE id=%s", (status, int(log_id)))

    def executions_for_sync(self, since: datetime) -> list[dict]:
        return self._exec(
            f"SELECT e.* FROM {SCHEMA}.execution_orders e LEFT JOIN {SCHEMA}.execution_finances f"
            " ON f.execution_order_id=e.id WHERE e.entry_order_id IS NOT NULL AND e.executed_qty > 0"
            " AND e.created_at >= %s AND COALESCE(f.is_fully_closed,false)=false", (since,), fetch="all") or []

    def archive_rows(self, order_ids: list[str]) -> tuple[list[dict], list[dict]]:
        """Read-only view of the hourly BingX archive (kept existing :07 function).

        A triggered SL/TP/trailing fills as a child order whose raw
        ``triggerOrderId`` points to the protection order; both are returned.
        """
        if not order_ids:
            return [], []
        ids = [str(x) for x in order_ids]
        orders = self._exec(
            "SELECT order_id,symbol,side,position_side,type,status,avg_price,filled_qty,updated_ms,raw"
            " FROM autotrade_analytics.bingx_orders WHERE order_id = ANY(%s)"
            " OR (raw::jsonb->>'triggerOrderId') = ANY(%s) OR (raw::jsonb->>'triggerOrderID') = ANY(%s)",
            (ids, ids, ids), fetch="all") or []
        all_ids = sorted(set(ids) | {str(o["order_id"]) for o in orders})
        fills = self._exec(
            "SELECT trade_id,order_id,symbol,qty,price,realized_pnl,fee,timestamp_ms"
            " FROM autotrade_analytics.bingx_fills WHERE order_id = ANY(%s)", (all_ids,), fetch="all") or []
        return orders, fills

    def upsert_result(self, row: dict) -> None:
        self._exec(
            f"INSERT INTO {SCHEMA}.execution_results(execution_order_id,event_id,close_order_id,close_type,closed_qty,"
            "close_price,realized_pnl,fee,closed_at,raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)"
            " ON CONFLICT (close_order_id) DO UPDATE SET close_type=excluded.close_type, closed_qty=excluded.closed_qty,"
            " close_price=excluded.close_price, realized_pnl=excluded.realized_pnl, fee=excluded.fee,"
            " closed_at=excluded.closed_at, raw=excluded.raw, recorded_at=now()",
            (row["execution_order_id"], row["event_id"], row["close_order_id"], row.get("close_type"),
             row.get("closed_qty"), row.get("close_price"), row.get("realized_pnl"), row.get("fee"),
             row.get("closed_at"), dumps(row.get("raw") or {})))

    def upsert_finance(self, row: dict) -> None:
        keys = ("margin", "leverage", "position_value", "open_fee", "close_fee", "funding", "gross_pnl",
                "net_pnl", "roi_pct", "closed_qty", "is_fully_closed")
        self._exec(
            f"INSERT INTO {SCHEMA}.execution_finances(execution_order_id,event_id,{','.join(keys)},raw)"
            f" VALUES (%s,%s,{','.join(['%s'] * len(keys))},%s::jsonb) ON CONFLICT (execution_order_id) DO UPDATE SET "
            + ", ".join(f"{k}=excluded.{k}" for k in keys) + ", raw=excluded.raw, updated_at=now()",
            (row["execution_order_id"], row["event_id"], *[row.get(k) for k in keys], dumps(row.get("raw") or {})))


def make_store(database_url: str):
    url = (database_url or "").strip()
    if url.startswith(("postgres://", "postgresql://")):
        return PostgresStore(url)
    return MemoryStore()


def to_json(value: Any) -> str:
    return json.dumps(clean(value), ensure_ascii=False, default=str)

