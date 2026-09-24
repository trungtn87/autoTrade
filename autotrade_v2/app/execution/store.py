from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class ExecutionStore:
    """Idempotency backed by Supabase public.processed_targets."""

    def __init__(self,db_path:str,database_url:str=""):
        self.database_url=(database_url or "").strip()
        self.is_postgres=bool(self.database_url)
        self.path=Path(db_path)
        self._lock=threading.RLock()
        if not self.is_postgres:
            self._init_sqlite()

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
                CREATE TABLE IF NOT EXISTS processed_targets(
                    event_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    payload TEXT,
                    PRIMARY KEY(event_id,target)
                )
            """)
            con.commit()

    def seen(self,event_id:str,target:str="bingx_account")->bool:
        with self._lock,self._connect() as con:
            cur=con.cursor()
            cur.execute(self._sql(
                "SELECT 1 FROM processed_targets WHERE event_id=? AND target=?"
            ),(event_id,target))
            return cur.fetchone() is not None

    def mark(self,event_id:str,payload:dict,target:str="bingx_account")->None:
        body=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str)
        with self._lock,self._connect() as con:
            cur=con.cursor()
            cur.execute(self._sql("""
                INSERT INTO processed_targets(event_id,target,payload)
                VALUES(?,?,?)
                ON CONFLICT(event_id,target) DO NOTHING
            """),(event_id,target,body))
            con.commit()
