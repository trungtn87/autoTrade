from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pandas as pd


class CandleStore:
    """Layer-1 persistence only. Uses isolated V2 table names."""

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
            import psycopg
            return psycopg.connect(self.database_url)
        return sqlite3.connect(self.path)

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _init(self) -> None:
        with self._connect() as con:
            cur = con.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS v2_candles (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_time BIGINT NOT NULL,
                    open DOUBLE PRECISION NOT NULL,
                    high DOUBLE PRECISION NOT NULL,
                    low DOUBLE PRECISION NOT NULL,
                    close DOUBLE PRECISION NOT NULL,
                    volume DOUBLE PRECISION NOT NULL,
                    close_time BIGINT NOT NULL,
                    PRIMARY KEY(symbol, timeframe, open_time)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_v2_candles_symbol_tf_time
                ON v2_candles(symbol, timeframe, open_time)
                """
            )
            con.commit()

    def upsert(self, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        rows = [
            (
                symbol, timeframe, int(row.open_time), float(row.open), float(row.high),
                float(row.low), float(row.close), float(row.volume), int(row.close_time),
            )
            for row in df.itertuples(index=False)
        ]
        sql = self._sql(
            """
            INSERT INTO v2_candles(
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

    def load(self, symbol: str, timeframe: str = "15m", limit: int | None = None) -> pd.DataFrame:
        sql = """
            SELECT open_time,open,high,low,close,volume,close_time
            FROM v2_candles
            WHERE symbol=? AND timeframe=?
            ORDER BY open_time DESC
        """
        params: list = [symbol, timeframe]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(self._sql(sql), params)
            rows = list(cur.fetchall())
        rows.reverse()
        return pd.DataFrame(
            rows,
            columns=["open_time","open","high","low","close","volume","close_time"],
        )

    def count(self, symbol: str, timeframe: str = "15m") -> int:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql("SELECT COUNT(*) FROM v2_candles WHERE symbol=? AND timeframe=?"),
                (symbol, timeframe),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def trim(self, symbol: str, timeframe: str, keep: int) -> None:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql(
                    """
                    DELETE FROM v2_candles
                    WHERE symbol=? AND timeframe=?
                      AND open_time NOT IN (
                        SELECT open_time FROM v2_candles
                        WHERE symbol=? AND timeframe=?
                        ORDER BY open_time DESC
                        LIMIT ?
                      )
                    """
                ),
                (symbol, timeframe, symbol, timeframe, int(keep)),
            )
            con.commit()
