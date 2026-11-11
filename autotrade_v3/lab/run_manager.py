"""Research Run Manager for AutoTrade V3 Offline Research Lab.

Ensures strict immutability, isolated execution provenance, and persistent
indexing of research runs without repository bloat.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from typing import Any
import duckdb
import numpy as np
import pandas as pd
import pyarrow

from autotrade_v3.engine.execution_simulator import ExecutionEvent, SimulationResult, TradeRecord
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.reports.reporter import (
    export_events_csv,
    export_events_parquet,
    export_trades_csv,
    export_trades_parquet,
)
from autotrade_v3.tools.artifact_hash import compute_artifact_hashes

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAB_RUNS_DIR = REPO_ROOT / "runs" / "lab"


def get_git_commit(repo_dir: Path = REPO_ROOT) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "UNKNOWN_COMMIT"


def get_git_state(repo_dir: Path = REPO_ROOT) -> tuple[str, bool, str]:
    """Inspect git state, returning (code_sha, code_dirty, diff_sha256).
    
    Fail-closed: If git fails or state cannot be verified, code_dirty is set to True.
    """
    git_error = False
    try:
        code_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        code_sha = "UNKNOWN_COMMIT"
        git_error = True

    try:
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo_dir, text=True, stderr=subprocess.DEVNULL
        ).strip()
        filtered = [
            line for line in status_out.splitlines()
            if not line[3:].startswith("runs/") and not line[3:].startswith("runs\\")
            and not line[3:].startswith("evidence/") and not line[3:].startswith("evidence\\")
        ]
        code_dirty = len(filtered) > 0
    except Exception:
        code_dirty = True
        git_error = True

    if git_error:
        code_dirty = True

    diff_sha256 = ""
    if code_dirty:
        try:
            diff_bytes = subprocess.check_output(
                ["git", "diff", "HEAD", "--", ":!runs", ":!evidence"],
                cwd=repo_dir,
                stderr=subprocess.DEVNULL,
            )
            diff_sha256 = hashlib.sha256(diff_bytes).hexdigest()
        except Exception:
            diff_sha256 = "UNKNOWN_DIFF"

    return code_sha, code_dirty, diff_sha256


def get_env_versions() -> dict[str, str]:
    """Retrieve runtime library versions for manifest."""
    return {
        "python_version": platform.python_version(),
        "pandas_version": pd.__version__,
        "numpy_version": np.__version__,
        "duckdb_version": duckdb.__version__,
        "pyarrow_version": pyarrow.__version__,
    }


@dataclass
class RunManifest:
    run_id: str
    strategy_id: str
    strategy_version: str
    config_hash: str
    data_manifest_hash: str
    git_sha: str
    execution_mode: str
    time_window: dict[str, str]
    symbols: list[str]
    fee_rate_taker: float
    slippage_rate: float
    initial_equity: float
    notional_per_trade: float
    total_signals: int
    accepted_entries: int
    total_legs: int
    skipped_signals_count: int
    rejected_signals_count: int
    ambiguity_stats: dict[str, int]
    validation_status: str  # "PASS", "FAIL"
    code_sha: str = ""
    code_dirty: bool = False
    diff_sha256: str = ""
    python_version: str = ""
    pandas_version: str = ""
    numpy_version: str = ""
    duckdb_version: str = ""
    pyarrow_version: str = ""
    dataset_hash: str = ""
    is_golden: bool = False
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunManager:
    """Manages the creation, persistence, indexing, and comparison of lab backtest runs."""

    def __init__(self, runs_dir: str | Path = DEFAULT_LAB_RUNS_DIR):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.catalog_file = self.runs_dir / "lab_catalog.json"

    def generate_run_id(
        self,
        strategy_id: str,
        mode: str,
        window_start: str,
        window_end: str,
    ) -> str:
        """Generate human-readable unique run ID."""
        now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        w_s = window_start.replace("-", "")[:8]
        w_e = window_end.replace("-", "")[:8]
        clean_strat = strategy_id.replace(" ", "_").upper()
        clean_mode = mode.replace(" ", "_").upper()
        return f"RUN_{now_str}_{clean_strat}_{clean_mode}_{w_s}_{w_e}"

    def save_run(
        self,
        manifest: RunManifest,
        sim_result: SimulationResult,
        analysis: dict[str, Any],
        signals: list[Signal] | None = None,
    ) -> Path:
        """Persist complete run artifacts without overwriting prior runs."""
        if manifest.is_golden:
            run_dir = self.runs_dir / manifest.execution_mode
        else:
            run_dir = self.runs_dir / manifest.run_id
            if run_dir.exists():
                ns = datetime.now(timezone.utc).strftime("%f")
                manifest.run_id = f"{manifest.run_id}_{ns}"
                run_dir = self.runs_dir / manifest.run_id

        run_dir.mkdir(parents=True, exist_ok=True)

        # 1. Save Run Manifest
        manifest_dict = manifest.to_dict()
        with open(run_dir / "run_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest_dict, f, indent=2)

        # 2. Save Analysis Summary
        with open(run_dir / "analysis.json", "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2, default=str)

        # 3. Save Signals (Input Signals)
        if signals:
            sig_dicts = []
            for s in signals:
                d = asdict(s)
                if "exit_policy" in d:
                    d["exit_policy"] = str(d["exit_policy"]) if d["exit_policy"] is not None else ""
                d["signal_close_dt"] = str(s.signal_close_dt)
                sig_dicts.append(d)
            df_sigs = pd.DataFrame(sig_dicts)
            df_sigs.to_parquet(run_dir / "signals.parquet", index=False)
            df_sigs.to_csv(run_dir / "signals.csv", index=False)
        else:
            # Fallback empty signals schema
            df_sigs = pd.DataFrame(columns=[
                "signal_id", "symbol", "combo_id", "combo_name", "timeframe", "side",
                "signal_close_ms", "signal_close_dt", "entry_price", "tp_price", "sl_price"
            ])
            df_sigs.to_parquet(run_dir / "signals.parquet", index=False)

        # 4. Save Trades & Legs
        trades = sim_result.trades
        if trades:
            export_trades_csv(trades, run_dir / "trades.csv")
            export_trades_parquet(trades, run_dir / "trades.parquet")
        else:
            df_empty_trades = pd.DataFrame(columns=["signal_id", "trade_id", "leg_id", "net_pnl"])
            df_empty_trades.to_parquet(run_dir / "trades.parquet", index=False)

        # 5. Save Execution Events
        events = sim_result.events
        if events:
            export_events_csv(events, run_dir / "execution_events.csv")
            export_events_parquet(events, run_dir / "execution_events.parquet")
        else:
            df_empty_events = pd.DataFrame(columns=[
                "event_id", "signal_id", "timestamp_ms", "timestamp_dt", "trade_id", "leg_id",
                "symbol", "combo_id", "combo_name", "timeframe", "event_type", "side",
                "order_action", "price", "qty", "notional", "fee", "realized_pnl", "exit_reason"
            ])
            df_empty_events.to_parquet(run_dir / "execution_events.parquet", index=False)

        # 6. Save Skipped Signals (JSON & Parquet)
        with open(run_dir / "skipped_signals.json", "w", encoding="utf-8") as f:
            json.dump(sim_result.skipped_signals, f, indent=2, default=str)
        if sim_result.skipped_signals:
            df_skipped = pd.DataFrame(sim_result.skipped_signals)
        else:
            df_skipped = pd.DataFrame(columns=[
                "signal_id", "symbol", "combo_id", "combo_name", "timeframe", "side",
                "signal_close_ms", "signal_close_dt", "reason"
            ])
        df_skipped.to_parquet(run_dir / "skipped_signals.parquet", index=False)

        # 7. Save Rejected Signals (JSON & Parquet)
        with open(run_dir / "rejected_signals.json", "w", encoding="utf-8") as f:
            json.dump(sim_result.rejected_signals, f, indent=2, default=str)
        if sim_result.rejected_signals:
            df_rejected = pd.DataFrame(sim_result.rejected_signals)
        else:
            df_rejected = pd.DataFrame(columns=[
                "signal_id", "timestamp_ms", "timestamp_dt", "symbol", "combo_id",
                "combo_name", "timeframe", "side", "signal_close_ms", "reason",
                "free_margin", "req_margin"
            ])
        df_rejected.to_parquet(run_dir / "rejected_signals.parquet", index=False)

        # 8. Save Equity Snapshots if present
        snapshots_meta = None
        if not sim_result.snapshots.empty:
            snap_pq = run_dir / "snapshots.parquet"
            snap_csv = run_dir / "snapshots.csv"
            sim_result.snapshots.to_parquet(snap_pq, index=False)
            sim_result.snapshots.to_csv(snap_csv, index=False)
            snap_size = snap_pq.stat().st_size
            snap_sha = compute_artifact_hashes(snap_pq)["sha256"]
            is_over_20mb = snap_size > 20 * 1024 * 1024
            snapshots_meta = {
                "file_name": "snapshots.parquet",
                "absolute_path": str(snap_pq.resolve()),
                "size_bytes": snap_size,
                "sha256": snap_sha,
                "committed": not is_over_20mb,
                "reason": "Exceeds 20 MB threshold; omitted from git" if is_over_20mb else "Committed",
            }

        # 9. Generate ARTIFACTS.json
        artifacts_index: dict[str, Any] = {
            "mode": manifest.execution_mode,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "artifacts": {},
        }
        target_artifacts = [
            "run_manifest.json",
            "analysis.json",
            "signals.parquet",
            "trades.parquet",
            "execution_events.parquet",
            "skipped_signals.parquet",
            "rejected_signals.parquet",
        ]
        for art_name in target_artifacts:
            art_file = run_dir / art_name
            if art_file.exists():
                h_info = compute_artifact_hashes(art_file)
                artifacts_index["artifacts"][art_name] = {
                    "file_name": art_name,
                    "size_bytes": h_info["size_bytes"],
                    "sha256": h_info["sha256"],
                    "content_hash": h_info["content_hash"],
                    "row_count": h_info.get("row_count"),
                }
        if snapshots_meta is not None:
            artifacts_index["artifacts"]["snapshots.parquet"] = snapshots_meta

        with open(run_dir / "ARTIFACTS.json", "w", encoding="utf-8") as f:
            json.dump(artifacts_index, f, indent=2)

        # 10. Generate Markdown Report
        md_report = self._render_markdown_report(manifest, analysis)
        (run_dir / "report.md").write_text(md_report, encoding="utf-8")

        # 11. Update Catalog Index (only for non-golden runs to prevent catalog corruption)
        if not manifest.is_golden:
            self._update_catalog(manifest, analysis)

        return run_dir

    def update_post_validation(
        self,
        run_dir: Path | str,
        manifest: RunManifest,
        analysis: dict[str, Any],
        val_report: Any,
    ) -> None:
        """Synchronize manifest, analysis, report.md, validation.json, and ARTIFACTS.json after validation."""
        r_dir = Path(run_dir)
        manifest.validation_status = val_report.overall_status

        # 1. Update run_manifest.json on disk
        with open(r_dir / "run_manifest.json", "w", encoding="utf-8") as f:
            json.dump(asdict(manifest), f, indent=2)

        # 2. Update analysis.json on disk
        val_dict: dict[str, Any] = {
            "overall_status": val_report.overall_status,
            "is_real_data": getattr(val_report, "is_real_data", False),
            "gates": {},
            "failure_reasons": getattr(val_report, "failure_reasons", []),
        }
        raw_gates = getattr(val_report, "gates", {})
        for k, v in raw_gates.items():
            if hasattr(v, "to_dict"):
                val_dict["gates"][k] = v.to_dict()
            elif isinstance(v, dict):
                val_dict["gates"][k] = v
            else:
                val_dict["gates"][k] = str(v)
        analysis["validation"] = val_dict
        with open(r_dir / "analysis.json", "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2)

        # 3. Update report.md on disk
        md_report = self._render_markdown_report(manifest, analysis)
        (r_dir / "report.md").write_text(md_report, encoding="utf-8")

        # 4. Regenerate ARTIFACTS.json on disk
        artifacts_index: dict[str, Any] = {
            "mode": manifest.execution_mode,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "artifacts": {},
        }
        target_artifacts = [
            "run_manifest.json",
            "analysis.json",
            "signals.parquet",
            "trades.parquet",
            "execution_events.parquet",
            "skipped_signals.parquet",
            "rejected_signals.parquet",
        ]
        for art_name in target_artifacts:
            art_file = r_dir / art_name
            if art_file.exists():
                h_info = compute_artifact_hashes(art_file)
                artifacts_index["artifacts"][art_name] = {
                    "file_name": art_name,
                    "size_bytes": h_info["size_bytes"],
                    "sha256": h_info["sha256"],
                    "content_hash": h_info["content_hash"],
                    "row_count": h_info.get("row_count"),
                }
        snap_pq = r_dir / "snapshots.parquet"
        if snap_pq.exists():
            snap_size = snap_pq.stat().st_size
            snap_sha = compute_artifact_hashes(snap_pq)["sha256"]
            is_over_20mb = snap_size > 20 * 1024 * 1024
            artifacts_index["artifacts"]["snapshots.parquet"] = {
                "file_name": "snapshots.parquet",
                "absolute_path": str(snap_pq.resolve()),
                "size_bytes": snap_size,
                "sha256": snap_sha,
                "committed": not is_over_20mb,
                "reason": "Exceeds 20 MB threshold; omitted from git" if is_over_20mb else "Committed",
            }

        with open(r_dir / "ARTIFACTS.json", "w", encoding="utf-8") as f:
            json.dump(artifacts_index, f, indent=2)

        # 5. Update catalog index if not golden
        if not manifest.is_golden:
            self._update_catalog(manifest, analysis)

    def _render_markdown_report(self, manifest: RunManifest, analysis: dict[str, Any]) -> str:
        acct = analysis.get("accounting", {})
        pnl = analysis.get("pnl", {})
        wr = analysis.get("win_rate", {})
        risk = analysis.get("risk", {})
        amb = analysis.get("ambiguity", {})
        sym_stats = analysis.get("symbols", {})
        combos_list = analysis.get("combos", [])
        exit_breakdown = analysis.get("exit_reasons", {})
        recon = analysis.get("reconciliation", {})

        val = analysis.get("validation", {})
        gates_dict = val.get("gates", {})

        lines = [
            f"# AutoTrade V3 Research Lab Run Report: `{manifest.run_id}`",
            "",
            f"- **Execution Mode:** `{manifest.execution_mode}`",
            f"- **Strategy:** `{manifest.strategy_id}` (v{manifest.strategy_version})",
            f"- **Time Window:** `{manifest.time_window['start']}` to `{manifest.time_window['end']}`",
            f"- **Symbols:** {', '.join(manifest.symbols)}",
            f"- **Git SHA:** `{manifest.git_sha}`",
            f"- **Validation Status:** **{manifest.validation_status}** (Real Market Data: {val.get('is_real_data', False)})",
            f"- **Data Hash:** `{manifest.data_manifest_hash[:16]}...`",
            "",
            "## Validation Gates Audit",
            "",
            "| Gate | Status | Summary |",
            "|:---|:---:|:---|",
        ]

        for g_key, g_res in gates_dict.items():
            lines.append(f"| **{g_res.get('gate', g_key)}** | `{g_res.get('status', 'UNKNOWN')}` | {g_res.get('summary', '')} |")

        lines.extend([
            "",
            "## 1. Accounting & Signals",
            "",
            f"- **Total Signals Generated:** {acct.get('total_signals', 0):,}",
            f"- **Accepted Entries (Trades):** {acct.get('accepted_entries', 0):,}",
            f"- **Total Trade Legs:** {acct.get('total_legs', 0):,}",
            f"- **Skipped by Concurrency:** {acct.get('skipped_by_concurrency', 0):,}",
            f"- **Rejected by Margin:** {acct.get('rejected_by_margin', 0):,}",
            "",
            "## 2. Performance & PnL",
            "",
            f"- **Gross PnL:** ${pnl.get('gross_pnl_unrounded', pnl.get('gross_pnl', 0.0)):,.2f}",
            f"- **Total Fees (Taker 0.05%):** ${pnl.get('total_fees_unrounded', pnl.get('total_fees', 0.0)):,.2f}",
            f"- **Net PnL:** **${pnl.get('net_pnl_unrounded', pnl.get('net_pnl', 0.0)):,.2f}**",
            f"- **ROI ({pnl.get('roi_denominator_name', 'Capital')}):** **{pnl.get('roi_pct_unrounded', pnl.get('roi_pct', 0.0)):+.2f}%**",
            f"- **Note on ROI:** {pnl.get('roi_note', '')}",
            f"- **Net Profit Factor (Default):** {pnl.get('net_profit_factor', pnl.get('profit_factor', 'N/A'))}",
            f"- **Gross Profit Factor:** {pnl.get('gross_profit_factor', 'N/A')}",
            f"- **Net Profit Factor (Trade):** {pnl.get('net_pf_trade', 'N/A')}",
            f"- **Net Profit Factor (Leg):** {pnl.get('net_pf_leg', 'N/A')}",
            f"- **Gross Profit Factor (Trade):** {pnl.get('gross_pf_trade', 'N/A')}",
            f"- **Gross Profit Factor (Leg):** {pnl.get('gross_pf_leg', 'N/A')}",
            f"- **Trade-level Win Rate:** {wr.get('trade_win_rate_pct', 0.0):.2f}% ({wr.get('winning_trades', 0)}/{acct.get('accepted_entries', 0)})",
            f"- **Leg-level Win Rate:** {wr.get('leg_win_rate_pct', 0.0):.2f}% ({wr.get('winning_legs', 0)}/{acct.get('total_legs', 0)})",
            "",
            "## 3. Risk & Drawdown",
            "",
            f"- **Closed-Trade Max Drawdown:** ${risk.get('closed_trade_max_drawdown_dollars', 0.0):,.2f} ({risk.get('closed_trade_max_drawdown_pct', 0.0):.2f}%)",
            (
                f"- **Continuous M2M Max Drawdown:** ${risk.get('m2m_max_drawdown_dollars', 0.0):,.2f} ({risk.get('m2m_max_drawdown_pct', 0.0):.2f}%)"
                if risk.get("m2m_max_drawdown_dollars") is not None
                else f"- **Continuous M2M Max Drawdown:** NOT_APPLICABLE ({risk.get('m2m_note', 'Only closed-trade drawdown applies in isolated/unconstrained modes')})"
            ),
            "",
            "## 4. Intrabar Ambiguity",
            "",
            f"- **TP/SL Same-Candle Ambiguity:** {amb.get('intrabar_tp_sl_ambiguous_count', 0)} legs",
            f"- **Trailing Stop Ambiguity:** {amb.get('intrabar_trailing_ambiguous_count', 0)} legs",
            f"- **Resolution Policy:** `{amb.get('resolution_policy', 'CONSERVATIVE')}`",
            "",
            "## 5. Performance by Symbol",
            "",
            "| Symbol | Entries | Legs | Net PnL | Win Rate (Leg) | Net PF | Gross PF | Net PF (Trade) | Gross PF (Trade) |",
            "|:---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])

        for sym, s_data in sym_stats.items():
            sym_net = s_data.get('net_pnl_unrounded', s_data.get('net_pnl', 0.0))
            lines.append(
                f"| **{sym}** | {s_data.get('entries', 0):,} | {s_data.get('legs', 0):,} | ${sym_net:,.2f} | {s_data.get('leg_win_rate_pct', 0.0):.1f}% | {s_data.get('net_profit_factor', s_data.get('profit_factor', 'N/A'))} | {s_data.get('gross_profit_factor', 'N/A')} | {s_data.get('net_pf_trade', 'N/A')} | {s_data.get('gross_pf_trade', 'N/A')} |"
            )

        if exit_breakdown:
            lines.extend([
                "",
                "## 6. Exit Reason Breakdown",
                "",
                "| Exit Reason | Count | Pct of Legs | Gross PnL | Net PnL |",
                "|:---|---:|---:|---:|---:|",
            ])
            for r_name, r_data in sorted(exit_breakdown.items()):
                r_gross = r_data.get('gross_pnl_unrounded', r_data.get('gross_pnl', 0.0))
                r_net = r_data.get('net_pnl_unrounded', r_data.get('net_pnl', 0.0))
                lines.append(
                    f"| **{r_name}** | {r_data.get('count', 0):,} | {r_data.get('pct_of_legs', 0.0):.2f}% | ${r_gross:,.2f} | ${r_net:,.2f} |"
                )

        if combos_list:
            lines.extend([
                "",
                "## 7. Performance by Combo",
                "",
                "| Combo Key | Symbol | ID | Name | Timeframe | Entries | Legs | Net PnL | Win Rate | Net PF (Leg) | Net PF (Trade) |",
                "|:---|:---|---:|:---|:---|---:|---:|---:|---:|---:|---:|",
            ])
            for c in combos_list:
                c_net = c.get('net_pnl_unrounded', c.get('net_pnl', 0.0))
                lines.append(
                    f"| **{c.get('combo_key', '')}** | {c.get('symbol', '')} | {c.get('combo_id', 0)} | {c.get('combo_name', '')} | {c.get('timeframe', '')} | {c.get('entries', 0):,} | {c.get('legs', 0):,} | ${c_net:,.2f} | {c.get('win_rate_pct', 0.0):.1f}% | {c.get('net_pf_leg', 'N/A')} | {c.get('net_pf_trade', 'N/A')} |"
                )

        if recon:
            lines.extend([
                "",
                "## 8. Reconciliation Audit",
                "",
                f"- **Three-Level Parity Pass (Run == Symbol Sum == Combo Sum):** `{recon.get('reconciliation_pass', False)}`",
                f"- **Symbol-Combo Configuration Alignment:** `{recon.get('symbol_combo_alignment_pass', False)}`",
                f"- **Unrounded Tolerance:** `{recon.get('tolerance', 1e-9)}`",
            ])

        return "\n".join(lines) + "\n"

    def _update_catalog(self, manifest: RunManifest, analysis: dict[str, Any]) -> None:
        pnl = analysis.get("pnl", {})
        wr = analysis.get("win_rate", {})
        risk = analysis.get("risk", {})

        entry = {
            "run_id": manifest.run_id,
            "created_at_utc": manifest.created_at_utc,
            "strategy_id": manifest.strategy_id,
            "strategy_version": manifest.strategy_version,
            "execution_mode": manifest.execution_mode,
            "window_start": manifest.time_window["start"],
            "window_end": manifest.time_window["end"],
            "symbols": manifest.symbols,
            "total_signals": manifest.total_signals,
            "accepted_entries": manifest.accepted_entries,
            "total_legs": manifest.total_legs,
            "net_pnl": pnl.get("net_pnl", 0.0),
            "roi_pct": pnl.get("roi_pct", 0.0),
            "profit_factor": pnl.get("profit_factor", 0.0),
            "trade_win_rate_pct": wr.get("trade_win_rate_pct", 0.0),
            "leg_win_rate_pct": wr.get("leg_win_rate_pct", 0.0),
            "closed_trade_max_drawdown_dollars": risk.get("closed_trade_max_drawdown_dollars", 0.0),
            "m2m_max_drawdown_dollars": risk.get("m2m_max_drawdown_dollars"),
            "validation_status": manifest.validation_status,
        }

        catalog: list[dict[str, Any]] = []
        if self.catalog_file.exists():
            try:
                with open(self.catalog_file, "r", encoding="utf-8") as f:
                    catalog = json.load(f)
            except Exception:
                catalog = []

        # Remove duplicate if run_id existed
        catalog = [c for c in catalog if c.get("run_id") != manifest.run_id]
        catalog.append(entry)

        with open(self.catalog_file, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2)

    def list_runs(self) -> list[dict[str, Any]]:
        """List all indexed historical research runs."""
        if not self.catalog_file.exists():
            return []
        try:
            with open(self.catalog_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Load manifest and analysis for a given run ID."""
        run_dir = self.runs_dir / run_id
        if not run_dir.exists():
            return None

        manifest_file = run_dir / "run_manifest.json"
        analysis_file = run_dir / "analysis.json"

        manifest = {}
        analysis = {}
        if manifest_file.exists():
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        if analysis_file.exists():
            with open(analysis_file, "r", encoding="utf-8") as f:
                analysis = json.load(f)

        return {
            "run_id": run_id,
            "manifest": manifest,
            "analysis": analysis,
            "path": str(run_dir),
        }
