from __future__ import annotations

from contextlib import contextmanager
import hashlib
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

    @contextmanager
    def trading_lock(self):
        """Cross-process exclusion for this store/account; never a lease that expires mid-POST.

        Hold a dedicated PostgreSQL session across exchange calls. Lock
        acquisition failures propagate; never fall back to a local-only lock.
        SQLite uses an OS lock shared by service instances on the same host.
        """
        with self._lock:
            if self.is_postgres:
                with self._connect() as con:
                    with con.cursor() as cur:
                        cur.execute("SELECT pg_try_advisory_lock(1105, 2026)")
                        acquired=bool(cur.fetchone()[0])
                        try:
                            yield acquired
                        finally:
                            if acquired:
                                cur.execute("SELECT pg_advisory_unlock(1105, 2026)")
            else:
                import fcntl
                # Hash only a path; never persist database credentials.
                key=hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()
                with open(f"/tmp/autotrade-execution-{key}.lock", "a") as handle:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        yield False
                    else:
                        try:
                            yield True
                        finally:
                            fcntl.flock(handle, fcntl.LOCK_UN)
