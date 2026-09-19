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
