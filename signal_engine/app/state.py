from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class SignalState:
    """Per-signal, per-target idempotency store."""

    def __init__(self, db_path: str):
        self.path = Path(db_path)
        self._lock = threading.Lock()
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init(self):
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_targets (
                    event_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    payload TEXT,
                    PRIMARY KEY (event_id, target)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    close_time INTEGER NOT NULL,
                    PRIMARY KEY (symbol, timeframe, open_time)
                )
                """
            )
            con.commit()

    def seen(self, event_id: str, target: str) -> bool:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM processed_targets WHERE event_id = ? AND target = ?",
                (event_id, target),
            ).fetchone()
            return row is not None

    def mark(self, event_id: str, target: str, payload: str = ""):
        with self._lock, self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO processed_targets(event_id, target, payload) VALUES (?, ?, ?)",
                (event_id, target, payload),
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
        with self._lock, self._connect() as con:
            con.executemany(
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
                """,
                rows,
            )
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
        params = [symbol, timeframe]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        with self._lock, self._connect() as con:
            rows = con.execute(sql, params).fetchall()

        rows.reverse()
        return pd.DataFrame(
            rows,
            columns=["open_time","open","high","low","close","volume","close_time"],
        )

    def candle_count(self, symbol: str, timeframe: str) -> int:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM candles WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            ).fetchone()
        return int(row[0]) if row else 0

    def latest_open_time(self, symbol: str, timeframe: str) -> int | None:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT MAX(open_time) FROM candles WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            ).fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])

    def trim_candles(self, symbol: str, timeframe: str, keep: int) -> None:
        if keep <= 0:
            return
        with self._lock, self._connect() as con:
            con.execute(
                """
                DELETE FROM candles
                WHERE symbol = ? AND timeframe = ?
                  AND open_time NOT IN (
                    SELECT open_time FROM candles
                    WHERE symbol = ? AND timeframe = ?
                    ORDER BY open_time DESC
                    LIMIT ?
                  )
                """,
                (symbol, timeframe, symbol, timeframe, int(keep)),
            )
            con.commit()
