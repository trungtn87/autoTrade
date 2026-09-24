from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pandas as pd


class CandleStore:
    """Supabase/Postgres is the production source of truth.

    SQLite exists only for deterministic local/CI tests.
    """

    def __init__(self, db_path: str, database_url: str = ""):
        self.database_url=(database_url or "").strip()
        self.is_postgres=bool(self.database_url)
        self.path=Path(db_path)
        self._lock=threading.RLock()
        if not self.is_postgres:
            self._init_sqlite()

    @property
    def backend(self)->str:
        return "supabase_postgres" if self.is_postgres else "sqlite_test_only"

    def _connect(self):
        if self.is_postgres:
            import psycopg
            return psycopg.connect(self.database_url)
        return sqlite3.connect(self.path)

    def _sql(self,sql:str)->str:
        return sql.replace("?","%s") if self.is_postgres else sql

    def _init_sqlite(self)->None:
        with self._connect() as con:
            cur=con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS candles(
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    close_time INTEGER NOT NULL,
                    PRIMARY KEY(symbol,timeframe,open_time)
                )
            """)
            con.commit()

    def upsert(self,symbol:str,timeframe:str,df:pd.DataFrame)->int:
        if df is None or df.empty:
            return 0
        rows=[(
            symbol,timeframe,int(r.open_time),float(r.open),float(r.high),
            float(r.low),float(r.close),float(r.volume),int(r.close_time),
        ) for r in df.itertuples(index=False)]
        sql=self._sql("""
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
        """)
        with self._lock,self._connect() as con:
            cur=con.cursor()
            cur.executemany(sql,rows)
            con.commit()
        return len(rows)

    def load(self,symbol:str,timeframe:str="15m",limit:int|None=None)->pd.DataFrame:
        sql="""
            SELECT open_time,open,high,low,close,volume,close_time
            FROM candles
            WHERE symbol=? AND timeframe=?
            ORDER BY open_time DESC
        """
        params=[symbol,timeframe]
        if limit is not None:
            sql+=" LIMIT ?"
            params.append(int(limit))
        with self._lock,self._connect() as con:
            cur=con.cursor()
            cur.execute(self._sql(sql),params)
            rows=list(cur.fetchall())
        rows.reverse()
        return pd.DataFrame(rows,columns=[
            "open_time","open","high","low","close","volume","close_time"
        ])

    def stats(self,symbol:str,timeframe:str="15m")->dict:
        with self._lock,self._connect() as con:
            cur=con.cursor()
            cur.execute(self._sql("""
                SELECT COUNT(*),MAX(open_time),MAX(close_time)
                FROM candles
                WHERE symbol=? AND timeframe=?
            """),(symbol,timeframe))
            row=cur.fetchone() or (0,None,None)
        return {
            "count":int(row[0] or 0),
            "latest_open_time":int(row[1]) if row[1] is not None else None,
            "latest_close_time":int(row[2]) if row[2] is not None else None,
        }

    def trim(self,symbol:str,timeframe:str,keep:int)->None:
        with self._lock,self._connect() as con:
            cur=con.cursor()
            cur.execute(self._sql("""
                DELETE FROM candles
                WHERE symbol=? AND timeframe=?
                  AND open_time NOT IN(
                    SELECT open_time FROM candles
                    WHERE symbol=? AND timeframe=?
                    ORDER BY open_time DESC
                    LIMIT ?
                  )
            """),(symbol,timeframe,symbol,timeframe,int(keep)))
            con.commit()
