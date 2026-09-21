from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class SignalState:
    """Persistent candle cache and per-target signal idempotency store.

    Uses Render/Postgres when DATABASE_URL is configured. Falls back to local
    SQLite for development/self-test.
    """

    def __init__(self, db_path: str, database_url: str = ""):
        self.database_url = (database_url or "").strip()
        self.is_postgres = bool(self.database_url)
        self.path = Path(db_path)
        self._lock = threading.Lock()
        self._init()

    @property
    def backend(self) -> str:
        return "postgres" if self.is_postgres else "sqlite"

    def _connect(self):
        if self.is_postgres:
            try:
                import psycopg
            except ImportError as exc:
                raise RuntimeError(
                    "DATABASE_URL is configured but psycopg is not installed"
                ) from exc
            return psycopg.connect(self.database_url)
        return sqlite3.connect(self.path)

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _init(self):
        with self._connect() as con:
            cur = con.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_targets (
                    event_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    payload TEXT,
                    PRIMARY KEY (event_id, target)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_time BIGINT NOT NULL,
                    open DOUBLE PRECISION NOT NULL,
                    high DOUBLE PRECISION NOT NULL,
                    low DOUBLE PRECISION NOT NULL,
                    close DOUBLE PRECISION NOT NULL,
                    volume DOUBLE PRECISION NOT NULL,
                    close_time BIGINT NOT NULL,
                    PRIMARY KEY (symbol, timeframe, open_time)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_time
                ON candles(symbol, timeframe, open_time)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS final14_orders (
                    event_id TEXT PRIMARY KEY, case_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS final14_one_active_case
                ON final14_orders(case_id) WHERE status NOT IN ('closed', 'rejected')
            """)
            # Upgrade existing FINAL14 positions without reopening closed events.
            cur.execute("SELECT value FROM runtime_state WHERE key='final14_active_cases_v1'")
            legacy = cur.fetchone()
            if legacy:
                rows = json.loads(legacy[0])
                if not isinstance(rows, list):
                    raise ValueError("Invalid legacy FINAL14 state")
                for rec in rows:
                    cur.execute(self._sql("""
                        INSERT INTO final14_orders(event_id,case_id,symbol,status,payload)
                        VALUES (?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING
                    """), (rec['event_id'],rec['case_id'],rec['symbol'],'active',json.dumps(rec)))
            con.commit()

    def seen(self, event_id: str, target: str) -> bool:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql(
                    "SELECT 1 FROM processed_targets WHERE event_id = ? AND target = ?"
                ),
                (event_id, target),
            )
            return cur.fetchone() is not None

    def mark(self, event_id: str, target: str, payload: str = ""):
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql(
                    """
                    INSERT INTO processed_targets(event_id, target, payload)
                    VALUES (?, ?, ?)
                    ON CONFLICT(event_id, target) DO NOTHING
                    """
                ),
                (event_id, target, payload),
            )
            con.commit()

    def get_runtime_value(self, key: str, default: str = "") -> str:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql("SELECT value FROM runtime_state WHERE key = ?"),
                (key,),
            )
            row = cur.fetchone()
        return str(row[0]) if row and row[0] is not None else default

    def set_runtime_value(self, key: str, value: str) -> None:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql(
                    """
                    INSERT INTO runtime_state(key, value, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=CURRENT_TIMESTAMP
                    """
                ),
                (key, str(value)),
            )
            con.commit()

    def upsert_candles(self, symbol: str, timeframe: str, df) -> int:
        if df is None or len(df) == 0:
            return 0
        rows = [
            (
                symbol,
                timeframe,
                int(row["open_time"]),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
                int(row["close_time"]),
            )
            for _, row in df.iterrows()
        ]
        sql = self._sql(
            """
            INSERT INTO candles(
                symbol,timeframe,open_time,open,high,low,close,volume,close_time
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol,timeframe,open_time) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                close_time=excluded.close_time
            """
        )
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.executemany(sql, rows)
            con.commit()
        return len(rows)

    def load_candles(self, symbol: str, timeframe: str, limit: int | None = None):
        import pandas as pd

        sql = """
            SELECT open_time,open,high,low,close,volume,close_time
            FROM candles
            WHERE symbol = ? AND timeframe = ?
            ORDER BY open_time DESC
        """
        params: list = [symbol, timeframe]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(self._sql(sql), params)
            rows = cur.fetchall()

        rows = list(rows)
        rows.reverse()
        return pd.DataFrame(
            rows,
            columns=["open_time","open","high","low","close","volume","close_time"],
        )

    def candle_count(self, symbol: str, timeframe: str) -> int:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql(
                    "SELECT COUNT(*) FROM candles WHERE symbol = ? AND timeframe = ?"
                ),
                (symbol, timeframe),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def earliest_open_time(self, symbol: str, timeframe: str) -> int | None:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql(
                    "SELECT MIN(open_time) FROM candles WHERE symbol = ? AND timeframe = ?"
                ),
                (symbol, timeframe),
            )
            row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])

    def latest_open_time(self, symbol: str, timeframe: str) -> int | None:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql(
                    "SELECT MAX(open_time) FROM candles WHERE symbol = ? AND timeframe = ?"
                ),
                (symbol, timeframe),
            )
            row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])

    def trim_candles(self, symbol: str, timeframe: str, keep: int) -> None:
        if keep <= 0:
            return
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql(
                    """
                    DELETE FROM candles
                    WHERE symbol = ? AND timeframe = ?
                      AND open_time NOT IN (
                        SELECT open_time FROM candles
                        WHERE symbol = ? AND timeframe = ?
                        ORDER BY open_time DESC
                        LIMIT ?
                      )
                    """
                ),
                (symbol, timeframe, symbol, timeframe, int(keep)),
            )
            con.commit()


    def claim_final14(self, rec: dict) -> bool:
        """Atomic event dedupe AND one active/pending position per case."""
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(self._sql("""
                INSERT INTO final14_orders(event_id,case_id,symbol,status,payload)
                VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING
            """), (rec['event_id'],rec['case_id'],rec['symbol'],'reserved',json.dumps(rec)))
            claimed = cur.rowcount == 1
            con.commit()
        return claimed

    def update_final14(self, event_id: str, changes: dict) -> dict:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            if not self.is_postgres:
                cur.execute("BEGIN IMMEDIATE")
            sql = "SELECT payload,status FROM final14_orders WHERE event_id=?"
            if self.is_postgres:
                sql += " FOR UPDATE"
            cur.execute(self._sql(sql), (event_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(event_id)
            rec = json.loads(row[0])
            # A late response cannot resurrect a completed case.
            if row[1] in ('closed','rejected'):
                return rec
            rec.update(changes)
            cur.execute(self._sql("UPDATE final14_orders SET status=?,payload=? WHERE event_id=?"),
                        (rec.get('status',row[1]),json.dumps(rec),event_id))
            con.commit()
            return rec

    def final14_records(self, symbol: str | None = None, active_only: bool = True) -> list[dict]:
        sql = "SELECT payload,status FROM final14_orders WHERE 1=1"
        params = []
        if active_only:
            sql += " AND status NOT IN ('closed','rejected')"
        if symbol is not None:
            sql += " AND symbol=?"
            params.append(symbol)
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(self._sql(sql),params)
            return [{**json.loads(p),'status':status} for p,status in cur.fetchall()]
