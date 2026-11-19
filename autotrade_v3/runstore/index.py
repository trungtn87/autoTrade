"""DuckDB run index (derived cache, rebuildable from store directories)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _duckdb():
    """Lazy DuckDB import: only lab stores need it; live imports of this package must not."""
    import duckdb

    return duckdb



_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id VARCHAR PRIMARY KEY,
    created_at_utc VARCHAR,
    execution_mode VARCHAR,
    strategy_id VARCHAR,
    strategy_version VARCHAR,
    cost_profile VARCHAR,
    ambiguity_policy VARCHAR,
    validation_status VARCHAR,
    source VARCHAR,
    signal_source VARCHAR,
    dataset_hash VARCHAR,
    spec_hash VARCHAR,
    code_sha VARCHAR,
    code_dirty BOOLEAN,
    total_signals BIGINT,
    accepted_entries BIGINT,
    total_legs BIGINT,
    net_pnl DOUBLE,
    roi_pct DOUBLE,
    run_dir VARCHAR
)
"""


def _extract_row(run_dir: Path) -> tuple[Any, ...] | None:
    manifest_p = run_dir / "run_manifest.json"
    if not manifest_p.exists():
        return None
    m = json.loads(manifest_p.read_text(encoding="utf-8"))
    analysis_p = run_dir / "analysis.json"
    a = json.loads(analysis_p.read_text(encoding="utf-8")) if analysis_p.exists() else {}
    val_p = run_dir / "validation.json"
    v = json.loads(val_p.read_text(encoding="utf-8")) if val_p.exists() else {}
    val_status = str(v.get("overall_status") or "UNVERIFIED")
    pnl = a.get("pnl", {})
    return (
        str(m.get("run_id", run_dir.name)),
        str(m.get("created_at_utc", "")),
        str(m.get("execution_mode", "")),
        str(m.get("strategy_id", "")),
        str(m.get("strategy_version", "")),
        str(m.get("cost_profile", "")),
        str(m.get("ambiguity_policy", "")),
        val_status,
        str(m.get("source", "V3")),
        str(m.get("signal_source", "")),
        str(m.get("dataset_hash", "")),
        str(m.get("spec_hash", "")),
        str(m.get("code_sha", "")),
        bool(m.get("code_dirty", False)),
        int(m.get("total_signals", 0)),
        int(m.get("accepted_entries", 0)),
        int(m.get("total_legs", 0)),
        float(pnl.get("net_pnl_unrounded", pnl.get("net_pnl", 0.0))),
        float(pnl.get("roi_pct_unrounded", pnl.get("roi_pct", 0.0))),
        str(run_dir.resolve()),
    )


class StoreIndex:
    """Manages ``runs/lab/store/index.duckdb`` as a rebuildable index over store directories."""

    def __init__(self, store_dir: str | Path) -> None:
        self.store_dir = Path(store_dir)
        self.db_path = self.store_dir / "index.duckdb"

    def upsert_run(self, run_dir: Path) -> None:
        row = _extract_row(run_dir)
        if row is None:
            return
        self.store_dir.mkdir(parents=True, exist_ok=True)
        con = _duckdb().connect(str(self.db_path))
        try:
            con.execute(_CREATE_SQL)
            con.execute("DELETE FROM runs WHERE run_id = ?", [row[0]])
            con.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                list(row),
            )
        finally:
            con.close()

    def reindex(self) -> int:
        """Delete and rebuild ``index.duckdb`` completely from run directories."""
        self.store_dir.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()
        con = _duckdb().connect(str(self.db_path))
        count = 0
        try:
            con.execute(_CREATE_SQL)
            if self.store_dir.exists():
                for sub in sorted(self.store_dir.iterdir()):
                    if sub.is_dir() and (sub / "run_manifest.json").exists():
                        row = _extract_row(sub)
                        if row is not None:
                            con.execute(
                                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                list(row),
                            )
                            count += 1
        finally:
            con.close()
        return count

    def list_runs(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            self.reindex()
        con = _duckdb().connect(str(self.db_path), read_only=True)
        try:
            sql = "SELECT * FROM runs ORDER BY created_at_utc DESC, run_id ASC"
            if limit is not None:
                sql += f" LIMIT {int(limit)}"
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        if not self.db_path.exists():
            self.reindex()
        con = _duckdb().connect(str(self.db_path), read_only=True)
        try:
            cur = con.execute("SELECT * FROM runs WHERE run_id = ?", [run_id])
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        finally:
            con.close()
