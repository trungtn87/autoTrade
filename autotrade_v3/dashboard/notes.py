"""Separate DuckDB store for research notes, tags, and pinned baselines (`runs/lab/notes.duckdb`).

Never writes to `runs/lab/store/`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from autotrade_v3.dashboard.queries import DEFAULT_NOTES_DB

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS run_notes (
    run_id VARCHAR PRIMARY KEY,
    tags VARCHAR,
    note VARCHAR,
    is_pinned_baseline BOOLEAN,
    updated_at_utc VARCHAR
)
"""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_run_note(
    run_id: str,
    *,
    tags: str = "",
    note: str = "",
    notes_db_path: str | Path = DEFAULT_NOTES_DB,
) -> dict[str, Any]:
    """Upsert tags and note for a run_id in notes.duckdb without touching RunStore."""
    p = Path(notes_db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = _now_utc()
    con = duckdb.connect(str(p))
    try:
        con.execute(_SCHEMA_SQL)
        existing = con.execute(
            "SELECT is_pinned_baseline FROM run_notes WHERE run_id = ?", [run_id]
        ).fetchone()
        pinned = bool(existing[0]) if existing else False
        con.execute("DELETE FROM run_notes WHERE run_id = ?", [run_id])
        con.execute(
            "INSERT INTO run_notes VALUES (?, ?, ?, ?, ?)",
            [run_id, tags, note, pinned, ts],
        )
    finally:
        con.close()
    return {"run_id": run_id, "tags": tags, "note": note, "is_pinned_baseline": pinned, "updated_at_utc": ts}


def set_pinned_baseline(
    run_id: str,
    *,
    pinned: bool = True,
    notes_db_path: str | Path = DEFAULT_NOTES_DB,
) -> dict[str, Any]:
    """Pin or unpin a run_id as baseline in notes.duckdb (at most one pinned baseline)."""
    p = Path(notes_db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = _now_utc()
    con = duckdb.connect(str(p))
    try:
        con.execute(_SCHEMA_SQL)
        if pinned:
            con.execute("UPDATE run_notes SET is_pinned_baseline = FALSE")
        existing = con.execute(
            "SELECT tags, note FROM run_notes WHERE run_id = ?", [run_id]
        ).fetchone()
        tags = str(existing[0]) if existing else ""
        note = str(existing[1]) if existing else ""
        con.execute("DELETE FROM run_notes WHERE run_id = ?", [run_id])
        con.execute(
            "INSERT INTO run_notes VALUES (?, ?, ?, ?, ?)",
            [run_id, tags, note, bool(pinned), ts],
        )
    finally:
        con.close()
    return {"run_id": run_id, "is_pinned_baseline": bool(pinned), "updated_at_utc": ts}
