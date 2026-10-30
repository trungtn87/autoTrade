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


    def get(self,event_id:str,target:str)->dict|None:
        with self._lock,self._connect() as con:
            cur=con.cursor()
            cur.execute(self._sql(
                "SELECT payload FROM processed_targets WHERE event_id=? AND target=?"
            ),(event_id,target))
            row=cur.fetchone()
        if not row or not row[0]:
            return None
        try:
            return json.loads(row[0]) if isinstance(row[0],str) else dict(row[0])
        except Exception:
            return None

    def upsert(self,event_id:str,payload:dict,target:str)->None:
        body=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str)
        with self._lock,self._connect() as con:
            cur=con.cursor()
            cur.execute(self._sql("""
                INSERT INTO processed_targets(event_id,target,payload)
                VALUES(?,?,?)
                ON CONFLICT(event_id,target) DO UPDATE SET
                    payload=excluded.payload,
                    processed_at=CURRENT_TIMESTAMP
            """),(event_id,target,body))
            con.commit()

    def get_state(self,event_id:str)->dict|None:
        return self.get(event_id,"bingx_state")

    def save_state(self,event_id:str,payload:dict)->None:
        self.upsert(event_id,payload,"bingx_state")

    def list_states(self)->list[tuple[str,dict]]:
        with self._lock,self._connect() as con:
            cur=con.cursor()
            cur.execute(self._sql(
                "SELECT event_id,payload FROM processed_targets WHERE target=?"
            ),("bingx_state",))
            rows=cur.fetchall()
        out=[]
        for event_id,payload in rows:
            try:
                body=json.loads(payload) if isinstance(payload,str) else dict(payload or {})
            except Exception:
                body={}
            out.append((str(event_id),body))
        return out
