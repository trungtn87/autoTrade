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
        self._session=threading.local()
        if not self.is_postgres:
            self._init_sqlite()

    def _connect(self):
        if self.is_postgres:
            import psycopg
            return psycopg.connect(self.database_url)
        return sqlite3.connect(self.path)

    def _sql(self,sql:str)->str:
        return sql.replace("?","%s") if self.is_postgres else sql

    @contextmanager
    def _connection(self):
        held=getattr(self._session,"connection",None)
        if held is not None:
            self.assert_trading_owner()
            yield held
        else:
            with self._connect() as con:
                yield con

    def assert_trading_owner(self):
        if not getattr(self._session,"held",False):
            raise RuntimeError("execution coordinator ownership missing/lost")
        con=getattr(self._session,"connection",None)
        if con is not None:
            # Never reconnect here: the exact session owns the advisory lock.
            row=con.execute("""SELECT EXISTS (SELECT 1 FROM pg_locks
                WHERE locktype='advisory' AND pid=pg_backend_pid()
                AND classid=1105 AND objid=2026 AND objsubid=2 AND granted)""").fetchone()
            if not row or not row[0]:
                raise RuntimeError("execution advisory ownership missing/lost")

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
        with self._lock,self._connection() as con:
            cur=con.cursor()
            cur.execute(self._sql(
                "SELECT 1 FROM processed_targets WHERE event_id=? AND target=?"
            ),(event_id,target))
            return cur.fetchone() is not None

    def mark(self,event_id:str,payload:dict,target:str="bingx_account")->None:
        body=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str)
        with self._lock,self._connection() as con:
            cur=con.cursor()
            cur.execute(self._sql("""
                INSERT INTO processed_targets(event_id,target,payload)
                VALUES(?,?,?)
                ON CONFLICT(event_id,target) DO NOTHING
            """),(event_id,target,body))
            con.commit()


    def get(self,event_id:str,target:str)->dict|None:
        with self._lock,self._connection() as con:
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
        with self._lock,self._connection() as con:
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

    def save_states_atomic(self, states:list[tuple[str,dict]])->None:
        """Commit a new flat-state boundary for all old combos together.

        Never permit partial history retirement: after a crash either the
        entire old side is excluded from active risk or none of it is.
        """
        if not states:
            return
        encoded=[
            (json.dumps(state,ensure_ascii=False,sort_keys=True,default=str),
             event_id,"bingx_state")
            for event_id,state in states
        ]
        with self._lock,self._connection() as con:
            try:
                with con.cursor() as cur:
                    for payload,event_id,target in encoded:
                        cur.execute(self._sql("""
                            UPDATE processed_targets
                               SET payload=?,processed_at=CURRENT_TIMESTAMP
                             WHERE event_id=? AND target=?
                        """),(payload,event_id,target))
                        if cur.rowcount!=1:
                            raise RuntimeError(
                                f"flat reconciliation state missing: {event_id}"
                            )
                con.commit()
            except Exception:
                con.rollback()
                raise

    def list_states(self)->list[tuple[str,dict]]:
        with self._lock,self._connection() as con:
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
                        con.commit()
                        self._session.connection=con if acquired else None
                        self._session.held=acquired
                        try:
                            yield acquired
                        finally:
                            self._session.connection=None
                            self._session.held=False
                            if acquired and not con.closed:
                                cur.execute("SELECT pg_advisory_unlock(1105, 2026)")
                                con.commit()
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
                        self._session.held=True
                        try:
                            yield True
                        finally:
                            self._session.held=False
                            fcntl.flock(handle, fcntl.LOCK_UN)
