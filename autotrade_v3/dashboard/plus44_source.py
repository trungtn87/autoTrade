"""Read-only PLUS44 adapter for the main Lab dashboard."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from autotrade_v3.dashboard.all_signals import RunReport, VerificationResult


DEFAULT_DB = Path("research/v3_lab_11_plus31/backtests.sqlite3")
DEFAULT_OUTPUT = Path("research/v3_lab_11_plus31/outputs/plus44")
PLUS44_GROUP = "PRUNE23_PLUS_TV21"


def database_path() -> Path:
    return Path(os.environ.get("AUTOTRADE_PLUS44_DB", str(DEFAULT_DB)))


def _manifest() -> dict[str, Any]:
    path = DEFAULT_OUTPUT / "RUN_MANIFEST_PLUS44.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def list_runs() -> list[RunReport]:
    db = database_path()
    if not db.is_file():
        return []
    manifest = _manifest()
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT run_id,profile,created_at,config_hash,metrics_json "
            "FROM research_runs WHERE study_group=? AND status='COMPLETED' "
            "ORDER BY created_at DESC, CASE profile WHEN 'BASE' THEN 0 ELSE 1 END",
            (PLUS44_GROUP,),
        ).fetchall()
    result: list[RunReport] = []
    for run_id, profile, created_at, config_hash, metrics_json in rows:
        metrics = json.loads(metrics_json)
        result.append(
            RunReport(
                run_id=str(run_id),
                run_dir=DEFAULT_OUTPUT,
                report_dir=None,
                generated_at_utc=str(created_at),
                cost_profile=str(profile).upper(),
                cost_profile_hash=str(config_hash),
                git_commit=str(manifest.get("git_head") or ""),
                code_dirty=False,
                validation_status="VERIFIED" if metrics.get("accounting_pass") is True else "FAILED",
                net_profit=float(metrics.get("net_pnl", 0.0)),
                max_drawdown_m2m_pct=None,
                entry_count=int(metrics.get("executed_trades", 0)),
            )
        )
    return result


def verify(run: RunReport) -> VerificationResult:
    try:
        with sqlite3.connect(database_path()) as conn:
            row = conn.execute(
                "SELECT metrics_json,portfolio_valid FROM research_runs WHERE run_id=?", (run.run_id,)
            ).fetchone()
            if row is None:
                return VerificationResult(False, "Không tìm thấy run PLUS44")
            metrics = json.loads(row[0])
            ledger_count, ledger_net = conn.execute(
                "SELECT count(*),coalesce(sum(net_pnl),0) FROM trade_ledger WHERE run_id=?", (run.run_id,)
            ).fetchone()
            strategy_count = conn.execute(
                "SELECT count(*) FROM strategy_results WHERE run_id=?", (run.run_id,)
            ).fetchone()[0]
        checks = (
            metrics.get("accounting_pass") is True,
            int(row[1]) == 1,
            int(strategy_count) == 44,
            int(ledger_count) == int(metrics.get("executed_trades", -1)),
            abs(float(ledger_net) - float(metrics.get("net_pnl", 0.0))) <= 1e-8,
        )
        return VerificationResult(all(checks), "ledger ↔ summary ↔ database" if all(checks) else "PLUS44 accounting mismatch")
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        return VerificationResult(False, str(exc))


def load_payload(run: RunReport) -> dict[str, Any] | None:
    if not verify(run).passed:
        return None
    with sqlite3.connect(database_path()) as conn:
        conn.row_factory = sqlite3.Row
        rr = conn.execute("SELECT * FROM research_runs WHERE run_id=?", (run.run_id,)).fetchone()
        strategies = conn.execute(
            "SELECT * FROM strategy_results WHERE run_id=? ORDER BY strategy_id", (run.run_id,)
        ).fetchall()
        equity = conn.execute(
            "SELECT timestamp_ms,equity,drawdown_pct FROM equity_curve WHERE run_id=? ORDER BY timestamp_ms",
            (run.run_id,),
        ).fetchall()
    if rr is None:
        return None
    metrics = json.loads(rr["metrics_json"])
    initial = float(rr["initial_equity"])
    combos = [
        {
            "combo_key": str(row["strategy_id"]),
            "symbol": str(row["symbol"]),
            "timeframe": str(row["timeframe"]),
            "portfolio_net_pnl": {"value": float(row["net_pnl"])},
            "own_entry_pnl": {"value": float(row["net_pnl"])},
            "fees": {"value": float(row["fees"])},
            "entries": {"value": int(row["total_trades"])},
            "leg_win_rate_pct": {"value": float(row["win_rate"])},
            "net_pf_leg": {"value": float(row["profit_factor"])},
            "execution_basis": str(row["execution_basis"]),
        }
        for row in strategies
    ]
    max_dd_pct = max((float(row["drawdown_pct"]) for row in equity), default=0.0)
    return {
        "generated_at_utc": str(rr["created_at"]),
        "run_info": {
            "run_id": run.run_id,
            "time_window_utc": {"start": str(rr["start_time"]), "end": str(rr["end_time"])},
            "portfolio": PLUS44_GROUP,
            "attribution": "SHARED_PORTFOLIO_ALLOCATION",
        },
        "kpi": {
            "account": {
                "initial_capital": {"value": initial},
                "final_equity_mtm": {"value": initial + float(metrics["net_pnl"])},
            },
            "risk": {
                "m2m_max_dd_dollars": {"value": float(metrics["m2m_dd"])},
                "m2m_max_dd_pct": {"value": max_dd_pct},
            },
        },
        "series": {
            "equity_mtm": [float(row["equity"]) for row in equity],
            "timestamps_ms": [int(row["timestamp_ms"]) for row in equity],
        },
        "breakdowns": {"combos": combos, "cost_sensitivity": []},
        "metrics": metrics,
    }


def combo_sparklines(run_id: str) -> dict[str, list[float]]:
    with sqlite3.connect(database_path()) as conn:
        rows = conn.execute(
            "SELECT strategy_id,net_pnl FROM trade_ledger WHERE run_id=? ORDER BY strategy_id,exit_time_ms",
            (run_id,),
        ).fetchall()
    result: dict[str, list[float]] = {}
    for strategy_id, pnl in rows:
        values = result.setdefault(str(strategy_id), [])
        values.append((values[-1] if values else 0.0) + float(pnl))
    return result


def payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
