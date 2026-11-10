"""Research Run Manager for AutoTrade V3 Offline Research Lab.

Ensures strict immutability, isolated execution provenance, and persistent
indexing of research runs without repository bloat.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Any
import pandas as pd

from autotrade_v3.engine.execution_simulator import ExecutionEvent, SimulationResult, TradeRecord
from autotrade_v3.reports.reporter import (
    export_events_csv,
    export_events_parquet,
    export_trades_csv,
    export_trades_parquet,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAB_RUNS_DIR = REPO_ROOT / "runs" / "lab"


def get_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "UNKNOWN_COMMIT"


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
    ) -> Path:
        """Persist complete run artifacts without overwriting prior runs."""
        run_dir = self.runs_dir / manifest.run_id
        if run_dir.exists():
            # If already exists, add nanosecond timestamp to ensure immutability
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

        # 3. Save Trades & Legs
        trades = sim_result.trades
        if trades:
            export_trades_csv(trades, run_dir / "trades.csv")
            export_trades_parquet(trades, run_dir / "trades.parquet")

        # 4. Save Execution Events
        events = sim_result.events
        if events:
            export_events_csv(events, run_dir / "execution_events.csv")
            export_events_parquet(events, run_dir / "execution_events.parquet")

        # 5. Save Skipped and Rejected Signals
        with open(run_dir / "skipped_signals.json", "w", encoding="utf-8") as f:
            json.dump(sim_result.skipped_signals, f, indent=2, default=str)

        if sim_result.rejected_signals:
            with open(run_dir / "rejected_signals.json", "w", encoding="utf-8") as f:
                json.dump(sim_result.rejected_signals, f, indent=2, default=str)

        # 6. Save Equity Snapshots if present
        if not sim_result.snapshots.empty:
            sim_result.snapshots.to_csv(run_dir / "snapshots.csv", index=False)
            sim_result.snapshots.to_parquet(run_dir / "snapshots.parquet", index=False)

        # 7. Generate Markdown Report
        md_report = self._render_markdown_report(manifest, analysis)
        (run_dir / "report.md").write_text(md_report, encoding="utf-8")

        # 8. Update Catalog Index
        self._update_catalog(manifest, analysis)

        return run_dir

    def _render_markdown_report(self, manifest: RunManifest, analysis: dict[str, Any]) -> str:
        acct = analysis.get("accounting", {})
        pnl = analysis.get("pnl", {})
        wr = analysis.get("win_rate", {})
        risk = analysis.get("risk", {})
        amb = analysis.get("ambiguity", {})
        sym_stats = analysis.get("symbols", {})

        lines = [
            f"# AutoTrade V3 Research Lab Run Report: `{manifest.run_id}`",
            "",
            f"- **Execution Mode:** `{manifest.execution_mode}`",
            f"- **Strategy:** `{manifest.strategy_id}` (v{manifest.strategy_version})",
            f"- **Time Window:** `{manifest.time_window['start']}` to `{manifest.time_window['end']}`",
            f"- **Symbols:** {', '.join(manifest.symbols)}",
            f"- **Git SHA:** `{manifest.git_sha}`",
            f"- **Validation Status:** **{manifest.validation_status}**",
            f"- **Data Hash:** `{manifest.data_manifest_hash[:16]}...`",
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
            f"- **Gross PnL:** ${pnl.get('gross_pnl', 0.0):,.2f}",
            f"- **Total Fees (Taker 0.05%):** ${pnl.get('total_fees', 0.0):,.2f}",
            f"- **Net PnL:** **${pnl.get('net_pnl', 0.0):,.2f}**",
            f"- **ROI ({pnl.get('roi_denominator_name', 'Capital')}):** **{pnl.get('roi_pct', 0.0):+.2f}%**",
            f"- **Note on ROI:** {pnl.get('roi_note', '')}",
            f"- **Profit Factor:** {pnl.get('profit_factor', 0.0):.2f}",
            f"- **Trade-level Win Rate:** {wr.get('trade_win_rate_pct', 0.0):.2f}% ({wr.get('winning_trades', 0)}/{acct.get('accepted_entries', 0)})",
            f"- **Leg-level Win Rate:** {wr.get('leg_win_rate_pct', 0.0):.2f}% ({wr.get('winning_legs', 0)}/{acct.get('total_legs', 0)})",
            "",
            "## 3. Risk & Drawdown",
            "",
            f"- **Closed-Trade Max Drawdown:** ${risk.get('closed_trade_max_drawdown_dollars', 0.0):,.2f} ({risk.get('closed_trade_max_drawdown_pct', 0.0):.2f}%)",
            f"- **Continuous M2M Max Drawdown:** ${risk.get('m2m_max_drawdown_dollars', 0.0):,.2f} ({risk.get('m2m_max_drawdown_pct', 0.0):.2f}%)",
            "",
            "## 4. Intrabar Ambiguity",
            "",
            f"- **TP/SL Same-Candle Ambiguity:** {amb.get('intrabar_tp_sl_ambiguous_count', 0)} legs",
            f"- **Trailing Stop Ambiguity:** {amb.get('intrabar_trailing_ambiguous_count', 0)} legs",
            f"- **Resolution Policy:** `{amb.get('resolution_policy', 'CONSERVATIVE')}`",
            "",
            "## 5. Performance by Symbol",
            "",
            "| Symbol | Entries | Legs | Net PnL | Win Rate (Leg) | Profit Factor |",
            "|:---|---:|---:|---:|---:|---:|",
        ]

        for sym, s_data in sym_stats.items():
            lines.append(
                f"| **{sym}** | {s_data.get('entries', 0):,} | {s_data.get('legs', 0):,} | ${s_data.get('net_pnl', 0.0):,.2f} | {s_data.get('leg_win_rate_pct', 0.0):.1f}% | {s_data.get('profit_factor', 0.0):.2f} |"
            )

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
            "max_drawdown_dollars": risk.get("m2m_max_drawdown_dollars", risk.get("closed_trade_max_drawdown_dollars", 0.0)),
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
