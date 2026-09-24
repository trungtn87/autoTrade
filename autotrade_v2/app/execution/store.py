from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class ExecutionStore:
    """Layer-3 idempotency state, isolated from market-data storage."""

    def __init__(self, db_path: str, database_url: str = ""):
        self.database_url = (database_url or "").strip()
        self.is_postgres = bool(self.database_url)
        self.path = Path(db_path)
        self._lock = threading.Lock()
        self._init()

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
                CREATE TABLE IF NOT EXISTS v2_execution_events (
                    event_id TEXT PRIMARY KEY,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    payload TEXT NOT NULL
                )
                """
            )
            con.commit()

    def seen(self, event_id: str) -> bool:
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(self._sql("SELECT 1 FROM v2_execution_events WHERE event_id=?"), (event_id,))
            return cur.fetchone() is not None

    def mark(self, event_id: str, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        with self._lock, self._connect() as con:
            cur = con.cursor()
            cur.execute(
                self._sql(
                    """
                    INSERT INTO v2_execution_events(event_id,payload)
                    VALUES (?,?)
                    ON CONFLICT(event_id) DO NOTHING
                    """
                ),
                (event_id, body),
            )
            con.commit()
