"""Unit and integration tests for V3-09 Reporting & Accounting Corrections."""
from __future__ import annotations

from pathlib import Path
import json
import re
import shutil
import subprocess
import sys
import numpy as np
import pandas as pd
import pytest

from autotrade_v3.analysis.metrics import compute_four_profit_factors
from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.engine.execution_simulator import (
    SimulationResult,
    TradeRecord,
)
from autotrade_v3.engine.signal_engine import SignalEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_V308_DIR = REPO_ROOT / "runs" / "golden" / "v3-08"


def _load_trades_parquet_as_records(parquet_path: Path) -> list[TradeRecord]:
    """Helper to convert parquet rows to TradeRecord objects."""
    df = pd.read_parquet(parquet_path)
    records: list[TradeRecord] = []
    for _, row in df.iterrows():
        records.append(
            TradeRecord(
                trade_id=str(row["trade_id"]),
                leg_id=str(row["leg_id"]),
                symbol=str(row["symbol"]),
                combo_id=int(row["combo_id"]),
                combo_name=str(row["combo_name"]),
                timeframe=str(row["timeframe"]),
                side=str(row["side"]),
                leg_type=str(row["leg_type"]),
                entry_time_ms=int(row["entry_time_ms"]),
                entry_time_dt=pd.Timestamp(row["entry_time_dt"]),
                entry_price=float(row["entry_price"]),
                exit_time_ms=int(row["exit_time_ms"]),
                exit_time_dt=pd.Timestamp(row["exit_time_dt"]),
                exit_price=float(row["exit_price"]),
                exit_reason=str(row["exit_reason"]),
                qty=float(row["qty"]),
                notional=float(row["notional"]),
                margin=float(row["margin"]),
                gross_pnl=float(row["gross_pnl"]),
                fee_total=float(row["fee_total"]),
                net_pnl=float(row["net_pnl"]),
                return_pct=float(row["return_pct"]),
                intrabar_ambiguous=bool(row["intrabar_ambiguous"]),
                bars_held=int(row["bars_held"]),
                provider=str(row["provider"]),
                signal_id=str(row.get("signal_id", "")),
                intrabar_trailing_ambiguous=bool(row.get("intrabar_trailing_ambiguous", False)),
                exit_policy_hash=str(row.get("exit_policy_hash", "")),
                tp_pct=float(row.get("tp_pct", 0.0)),
                sl_pct=float(row.get("sl_pct", 0.0)),
                trailing_act_pct=float(row.get("trailing_act_pct", 0.0)),
                trailing_cb_pct=float(row.get("trailing_cb_pct", 0.0)),
                max_bars_held=row.get("max_bars_held"),
            )
        )
    return records


def _load_run_as_sim_result(run_dir: Path, mode: str) -> SimulationResult:
    """Helper to reconstruct a SimulationResult from parquet/json artifacts in run_dir."""
    trades = _load_trades_parquet_as_records(run_dir / "trades.parquet")
    sigs_df = pd.read_parquet(run_dir / "signals.parquet")
    skipped_pq = run_dir / "skipped_signals.parquet"
    rejected_pq = run_dir / "rejected_signals.parquet"
    skipped = pd.read_parquet(skipped_pq).to_dict("records") if skipped_pq.exists() else []
    rejected = pd.read_parquet(rejected_pq).to_dict("records") if rejected_pq.exists() else []

    # If skipped or rejected signals exist, accepted signals are the remaining signals
    skipped_ids = {str(s.get("signal_id")) for s in skipped if isinstance(s, dict) and s.get("signal_id")}
    rejected_ids = {str(r.get("signal_id")) for r in rejected if isinstance(r, dict) and r.get("signal_id")}
    non_accepted = skipped_ids | rejected_ids
    if non_accepted and "signal_id" in sigs_df.columns:
        accepted_df = sigs_df[~sigs_df["signal_id"].astype(str).isin(non_accepted)]
        accepted_signals = accepted_df.to_dict("records")
    elif non_accepted:
        accepted_cnt = max(0, len(sigs_df) - len(skipped) - len(rejected))
        accepted_signals = sigs_df.iloc[:accepted_cnt].to_dict("records")
    else:
        accepted_signals = sigs_df.to_dict("records")

    summary: dict = {}
    an_path = run_dir / "analysis.json"
    if an_path.exists():
        with open(an_path, "r", encoding="utf-8") as af:
            an_data = json.load(af)
            summary["m2m_max_drawdown_dollars"] = an_data.get("risk", {}).get("m2m_max_drawdown_dollars")
            summary["m2m_max_drawdown_pct"] = an_data.get("risk", {}).get("m2m_max_drawdown_pct")
            if "validation" in an_data:
                summary["validation"] = an_data["validation"]

    tp_sl_cnt = sum(1 for t in trades if getattr(t, "intrabar_ambiguous", False))
    trail_cnt = sum(1 for t in trades if getattr(t, "intrabar_trailing_ambiguous", False))
    amb_stats = {
        "intrabar_tp_sl_ambiguous_count": tp_sl_cnt,
        "intrabar_trailing_ambiguous_count": trail_cnt,
    }

    return SimulationResult(
        mode=mode,
        trades=trades,
        events=[],
        accepted_signals=accepted_signals,
        skipped_signals=skipped,
        rejected_signals=rejected,
        snapshots=pd.DataFrame(),
        ambiguity_stats=amb_stats,
        summary_dict=summary,
    )


def _setup_pristine_run(tmp_path: Path, mode: str = "ALL_SIGNALS") -> tuple[Path, str]:
    """Helper to set up an isolated run directory with pristine report.md generated from golden baseline."""
    from autotrade_v3.lab.run_manager import RunManager, RunManifest

    golden_dir = REPO_ROOT / "runs" / "golden" / "v3-08" / mode
    mode_tmp = tmp_path / mode
    shutil.copytree(golden_dir, mode_tmp)

    sim_res = _load_run_as_sim_result(mode_tmp, mode)
    analysis = ResearchAnalyzer.analyze_simulation(sim_res)
    with open(mode_tmp / "analysis.json", "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2)
    with open(mode_tmp / "run_manifest.json", "r", encoding="utf-8") as f:
        manifest = RunManifest(**json.load(f))

    report_md = RunManager(runs_dir=mode_tmp)._render_markdown_report(manifest, analysis)
    (mode_tmp / "report.md").write_text(report_md, encoding="utf-8")
    return mode_tmp, report_md


class TestReportingAndPFCorrections:
    """Test suite for V3-09 analyzer, combo_key, profit factor, and reconciliation."""

    def test_configured_combos_count(self):
        """Verify baseline configuration combo counts: 11 for BTC, 9 for ETH, 20 total."""
        se = SignalEngine()
        btc_combos = set(se.final14_cases.get("BTC-USDT", {}).keys()) | set(se.new6_cases.get("BTC-USDT", {}).keys())
        eth_combos = set(se.final14_cases.get("ETH-USDT", {}).keys()) | set(se.new6_cases.get("ETH-USDT", {}).keys())
        assert len(btc_combos) == 11, f"Expected 11 BTC combos, got {len(btc_combos)}"
        assert len(eth_combos) == 9, f"Expected 9 ETH combos, got {len(eth_combos)}"
        assert len(btc_combos) + len(eth_combos) == 20

    def test_golden_all_signals_combo_count_underreporting_before_fix(self):
        """B2: Demonstrate that on existing golden analysis.json, combos count was 15 instead of 20."""
        import json

        golden_analysis = GOLDEN_V308_DIR / "ALL_SIGNALS" / "analysis.json"
        assert golden_analysis.exists(), "Golden ALL_SIGNALS analysis.json missing"
        with open(golden_analysis, "r", encoding="utf-8") as f:
            data = json.load(f)
        combos = data.get("combos", [])
        # In V3-08 golden output, combo count was 15 due to P1-4 bug
        assert len(combos) == 15, f"Expected golden analysis.json to exhibit P1-4 (15 combos), got {len(combos)}"

    def test_analyzer_should_produce_20_combos_from_golden_trades(self):
        """P1-4: On golden ALL_SIGNALS trades, analyzer must produce exactly 20 distinct combo rows."""
        trades_records = _load_trades_parquet_as_records(GOLDEN_V308_DIR / "ALL_SIGNALS" / "trades.parquet")
        sim_res = SimulationResult(
            mode="ALL_SIGNALS",
            trades=trades_records,
            events=[],
            accepted_signals=[],
            skipped_signals=[],
            rejected_signals=[],
            snapshots=pd.DataFrame(),
            ambiguity_stats={},
        )
        analysis = ResearchAnalyzer.analyze_simulation(sim_res)
        combos = analysis.get("combos", [])
        assert len(combos) == 20, f"Expected 20 combos, but analyzer returned {len(combos)}"
        keys = [c["combo_key"] for c in combos]
        assert "BTC-USDT/C1" in keys
        assert "ETH-USDT/C1" in keys
        # Check that both symbols have their own C1 row
        btc_c1 = [c for c in combos if c["combo_key"] == "BTC-USDT/C1"][0]
        eth_c1 = [c for c in combos if c["combo_key"] == "ETH-USDT/C1"][0]
        assert btc_c1["symbol"] == "BTC-USDT" and btc_c1["combo_name"] == "C1"
        assert eth_c1["symbol"] == "ETH-USDT" and eth_c1["combo_name"] == "C1"
        assert btc_c1["legs"] == 490
        assert eth_c1["legs"] == 544
        assert btc_c1["legs"] + eth_c1["legs"] == 1034

    def test_same_combo_id_two_symbols_produces_two_rows(self):
        """Unit test: synthetic legs with same combo_id (e.g. 1) on BTC and ETH produce 2 distinct rows."""
        t_btc = TradeRecord(
            trade_id="T_BTC", leg_id="L_BTC", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="1h", side="BUY", leg_type="SINGLE", entry_time_ms=1000,
            entry_time_dt=pd.Timestamp("2024-01-01", tz="UTC"), entry_price=50000.0,
            exit_time_ms=2000, exit_time_dt=pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            exit_price=50500.0, exit_reason="TP", qty=0.1, notional=5000.0, margin=100.0,
            gross_pnl=50.0, fee_total=5.0, net_pnl=45.0, return_pct=0.009, intrabar_ambiguous=False,
            bars_held=60, provider="SYNTHETIC",
        )
        t_eth = TradeRecord(
            trade_id="T_ETH", leg_id="L_ETH", symbol="ETH-USDT", combo_id=1, combo_name="C1",
            timeframe="1h", side="BUY", leg_type="SINGLE", entry_time_ms=1000,
            entry_time_dt=pd.Timestamp("2024-01-01", tz="UTC"), entry_price=3000.0,
            exit_time_ms=2000, exit_time_dt=pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            exit_price=3030.0, exit_reason="TP", qty=1.0, notional=3000.0, margin=60.0,
            gross_pnl=30.0, fee_total=3.0, net_pnl=27.0, return_pct=0.009, intrabar_ambiguous=False,
            bars_held=60, provider="SYNTHETIC",
        )
        sim_res = SimulationResult(
            mode="ALL_SIGNALS", trades=[t_btc, t_eth], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
        )
        analysis = ResearchAnalyzer.analyze_simulation(sim_res)
        combos = analysis["combos"]
        assert len(combos) == 2
        combo_keys = {c["combo_key"] for c in combos}
        assert combo_keys == {"BTC-USDT/C1", "ETH-USDT/C1"}

    def test_trade_level_vs_leg_level_profit_factor_handcrafted(self):
        """Unit test: handcrafted example with 2 trades, Trade 1 having 2 legs (1 win, 1 loss).
        
        Trade 1:
          Leg 1: net_pnl = +10.0, gross_pnl = +11.0, fee = 1.0
          Leg 2: net_pnl = -4.0,  gross_pnl = -3.0,  fee = 1.0
          Trade 1 net = +6.0, gross = +8.0
        Trade 2:
          Leg 1: net_pnl = -2.0,  gross_pnl = -1.0,  fee = 1.0
          Trade 2 net = -2.0, gross = -1.0
          
        Hand calculation:
          Leg level:
            net_wins = 10.0, net_losses = 4.0 + 2.0 = 6.0 -> net_pf_leg = 10.0 / 6.0 = 1.67 (exact 1.6666...)
            gross_wins = 11.0, gross_losses = 3.0 + 1.0 = 4.0 -> gross_pf_leg = 11.0 / 4.0 = 2.75
          Trade level:
            Trade 1 net = +6.0 (win), Trade 2 net = -2.0 (loss)
            net_wins = 6.0, net_losses = 2.0 -> net_pf_trade = 6.0 / 2.0 = 3.00
            Trade 1 gross = +8.0 (win), Trade 2 gross = -1.0 (loss)
            gross_wins = 8.0, gross_losses = 1.0 -> gross_pf_trade = 8.0 / 1.0 = 8.00
        """
        t1_l1 = TradeRecord(
            trade_id="TR_1", leg_id="TR_1_L1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="TP_HALF", entry_time_ms=1000,
            entry_time_dt=pd.Timestamp("2024-01-01", tz="UTC"), entry_price=100.0,
            exit_time_ms=2000, exit_time_dt=pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            exit_price=111.0, exit_reason="TP", qty=1.0, notional=100.0, margin=2.0,
            gross_pnl=11.0, fee_total=1.0, net_pnl=10.0, return_pct=0.10, intrabar_ambiguous=False,
            bars_held=60, provider="SYNTHETIC",
        )
        t1_l2 = TradeRecord(
            trade_id="TR_1", leg_id="TR_1_L2", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="TRAIL_HALF", entry_time_ms=1000,
            entry_time_dt=pd.Timestamp("2024-01-01", tz="UTC"), entry_price=100.0,
            exit_time_ms=3000, exit_time_dt=pd.Timestamp("2024-01-01 02:00", tz="UTC"),
            exit_price=97.0, exit_reason="TRAIL", qty=1.0, notional=100.0, margin=2.0,
            gross_pnl=-3.0, fee_total=1.0, net_pnl=-4.0, return_pct=-0.04, intrabar_ambiguous=False,
            bars_held=120, provider="SYNTHETIC",
        )
        t2_l1 = TradeRecord(
            trade_id="TR_2", leg_id="TR_2_L1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="SINGLE", entry_time_ms=4000,
            entry_time_dt=pd.Timestamp("2024-01-02", tz="UTC"), entry_price=100.0,
            exit_time_ms=5000, exit_time_dt=pd.Timestamp("2024-01-02 01:00", tz="UTC"),
            exit_price=99.0, exit_reason="SL", qty=1.0, notional=100.0, margin=2.0,
            gross_pnl=-1.0, fee_total=1.0, net_pnl=-2.0, return_pct=-0.02, intrabar_ambiguous=False,
            bars_held=60, provider="SYNTHETIC",
        )

        res = compute_four_profit_factors([t1_l1, t1_l2, t2_l1])
        assert res["net_pf_trade"] == 3.00
        assert res["net_pf_leg"] == 1.67
        assert res["gross_pf_trade"] == 8.00
        assert res["gross_pf_leg"] == 2.75

        # Also verify via ResearchAnalyzer.analyze_simulation
        sim_res = SimulationResult(
            mode="ALL_SIGNALS", trades=[t1_l1, t1_l2, t2_l1], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
        )
        analysis = ResearchAnalyzer.analyze_simulation(sim_res)
        pnl = analysis["pnl"]
        assert pnl["net_pf_trade"] == 3.00
        assert pnl["net_pf_leg"] == 1.67
        assert pnl["gross_pf_trade"] == 8.00
        assert pnl["gross_pf_leg"] == 2.75
        assert pnl["profit_factor"] == 1.67
        assert pnl["profit_factor_basis"] == "leg"

    def test_profit_factor_zero_and_no_loss_cases(self):
        """Test profit factor with no losing legs, and with empty trade list."""
        # 1. No losses: net_pf should be "UNDEFINED_NO_LOSS"
        t_win = TradeRecord(
            trade_id="TR_W", leg_id="L_W", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="SINGLE", entry_time_ms=1000,
            entry_time_dt=pd.Timestamp("2024-01-01", tz="UTC"), entry_price=100.0,
            exit_time_ms=2000, exit_time_dt=pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            exit_price=105.0, exit_reason="TP", qty=1.0, notional=100.0, margin=2.0,
            gross_pnl=5.0, fee_total=0.5, net_pnl=4.5, return_pct=0.045, intrabar_ambiguous=False,
            bars_held=60, provider="SYNTHETIC",
        )
        res_win = compute_four_profit_factors([t_win])
        assert res_win["net_pf_trade"] == "UNDEFINED_NO_LOSS"
        assert res_win["net_pf_leg"] == "UNDEFINED_NO_LOSS"
        assert res_win["gross_pf_trade"] == "UNDEFINED_NO_LOSS"
        assert res_win["gross_pf_leg"] == "UNDEFINED_NO_LOSS"

        # 2. Empty list: all 0.0
        res_empty = compute_four_profit_factors([])
        assert res_empty["net_pf_trade"] == 0.0
        assert res_empty["net_pf_leg"] == 0.0
        assert res_empty["gross_pf_trade"] == 0.0
        assert res_empty["gross_pf_leg"] == 0.0

    def test_reconciliation_block_detects_mismatched_symbol_combo(self):
        """Unit test: reconciliation block detects illegal symbol-combo pair (e.g. C2 on ETH-USDT)."""
        t_bad = TradeRecord(
            trade_id="TR_BAD", leg_id="L_BAD", symbol="ETH-USDT", combo_id=2, combo_name="C2",  # C2 is BTC only!
            timeframe="15m", side="BUY", leg_type="SINGLE", entry_time_ms=1000,
            entry_time_dt=pd.Timestamp("2024-01-01", tz="UTC"), entry_price=100.0,
            exit_time_ms=2000, exit_time_dt=pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            exit_price=105.0, exit_reason="TP", qty=1.0, notional=100.0, margin=2.0,
            gross_pnl=5.0, fee_total=0.5, net_pnl=4.5, return_pct=0.045, intrabar_ambiguous=False,
            bars_held=60, provider="SYNTHETIC",
        )
        sim_res = SimulationResult(
            mode="ALL_SIGNALS", trades=[t_bad], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
        )
        analysis = ResearchAnalyzer.analyze_simulation(sim_res)
        rec = analysis["reconciliation"]
        assert rec["symbol_combo_alignment_pass"] is False
        assert rec["reconciliation_pass"] is False

    def test_compare_runs_drawdown_separation(self):
        """P3-3 / 4.7: compare_runs must report closed_trade_max_drawdown and m2m_max_drawdown separately."""
        run_a = {
            "manifest": {"run_id": "RUN_A", "execution_mode": "ALL_SIGNALS", "strategy_id": "20FINAL"},
            "analysis": {
                "accounting": {"total_signals": 100, "accepted_entries": 100, "total_legs": 100},
                "pnl": {"gross_pnl": 50.0, "total_fees": 10.0, "net_pnl": 40.0, "roi_pct": 4.0},
                "win_rate": {"trade_win_rate_pct": 50.0, "leg_win_rate_pct": 50.0},
                "risk": {
                    "closed_trade_max_drawdown_dollars": 15.5,
                    "closed_trade_max_drawdown_pct": 1.55,
                    "m2m_max_drawdown_dollars": None,
                    "m2m_max_drawdown_pct": None,
                },
            },
        }
        run_c = {
            "manifest": {"run_id": "RUN_C", "execution_mode": "BINGX_PORTFOLIO", "strategy_id": "20FINAL"},
            "analysis": {
                "accounting": {"total_signals": 100, "accepted_entries": 80, "total_legs": 120},
                "pnl": {"gross_pnl": 40.0, "total_fees": 8.0, "net_pnl": 32.0, "roi_pct": 3.2},
                "win_rate": {"trade_win_rate_pct": 48.0, "leg_win_rate_pct": 48.0},
                "risk": {
                    "closed_trade_max_drawdown_dollars": 14.0,
                    "closed_trade_max_drawdown_pct": 1.40,
                    "m2m_max_drawdown_dollars": 22.0,
                    "m2m_max_drawdown_pct": 2.20,
                },
            },
        }
        comp = ResearchAnalyzer.compare_runs([run_a, run_c])
        rows = comp["comparison_table"]
        assert len(rows) == 2
        # Mode A: closed dd is 15.5, m2m is None (NOT None for closed dd!)
        row_a = rows[0]
        assert row_a["closed_trade_max_drawdown_dollars"] == 15.5
        assert row_a["closed_trade_max_drawdown_pct"] == 1.55
        assert row_a["m2m_max_drawdown_dollars"] is None

        # Mode C: both present
        row_c = rows[1]
        assert row_c["closed_trade_max_drawdown_dollars"] == 14.0
        assert row_c["m2m_max_drawdown_dollars"] == 22.0

    def test_no_hardcoded_metrics_in_reporting_code(self):
        """AC5: Ensure no hardcoded metric values (5984, 2827, 1488, 1947, 343.52) or 'status': 'PASS' in reporting scripts."""
        target_files = [
            REPO_ROOT / "autotrade_v3" / "lab" / "run_manager.py",
            REPO_ROOT / "autotrade_v3" / "reports" / "reporter.py",
            REPO_ROOT / "scripts" / "reconcile_runs.py",
            REPO_ROOT / "scripts" / "run_v3_backtest.py",
        ]
        banned_patterns = [
            r"\b5984\b",
            r"\b2827\b",
            r"\b1488\b",
            r"\b1947\b",
            r"\b343\.52\b",
            r"\"status\"\s*:\s*\"PASS\"",
            r"\'status\'\s*:\s*\'PASS\'",
        ]
        violations = []
        for file_path in target_files:
            if not file_path.exists():
                continue
            text = file_path.read_text(encoding="utf-8")
            for pat in banned_patterns:
                for match in re.finditer(pat, text):
                    line_no = text[: match.start()].count("\n") + 1
                    line = text.splitlines()[line_no - 1].strip()
                    violations.append(f"{file_path.name}:{line_no} matches '{pat}': {line}")

        assert not violations, "Found hardcoded metrics / status in reporting code:\n" + "\n".join(violations)

    def test_verify_report_on_golden_reports(self, tmp_path):
        """AC4 & Integration: verify_report passes on all 3 golden runs with newly rendered report.md."""
        from autotrade_v3.lab.run_manager import RunManager

        for mode in ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]:
            golden_dir = REPO_ROOT / "runs" / "golden" / "v3-08" / mode
            assert golden_dir.exists(), f"Golden dir missing: {golden_dir}"

            # Copy to tmp_path to avoid mutating golden dir
            mode_tmp = tmp_path / mode
            shutil.copytree(golden_dir, mode_tmp)

            # Re-generate analysis.json with v3-09 analyzer
            sim_res = _load_run_as_sim_result(mode_tmp, mode)
            analysis = ResearchAnalyzer.analyze_simulation(sim_res)
            with open(mode_tmp / "analysis.json", "w", encoding="utf-8") as f:
                json.dump(analysis, f, indent=2)

            with open(mode_tmp / "run_manifest.json", "r", encoding="utf-8") as f:
                manifest_dict = json.load(f)

            # Recreate manifest dataclass or mock object
            from autotrade_v3.lab.run_manager import RunManifest
            manifest = RunManifest(**manifest_dict)

            report_md = RunManager(runs_dir=mode_tmp)._render_markdown_report(manifest, analysis)
            (mode_tmp / "report.md").write_text(report_md, encoding="utf-8")

            # Run verify_report.py
            cmd = [
                sys.executable,
                str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"),
                "--run-dir",
                str(mode_tmp),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            assert res.returncode == 0, f"verify_report failed on {mode}:\n{res.stdout}\n{res.stderr}"

    def test_adversarial_mutation_m1_report_number_changed(self, tmp_path):
        """Adversarial M1: Alter a number in report.md -> verify_report must exit non-zero and identify error."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "$410.70" in report_md, "Expected pristine report to contain $410.70"
        mutated_report = report_md.replace("$410.70", "$999.99")
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail on mutated report.md"
        assert "FAIL" in res.stdout or "FAIL" in res.stderr

    def test_adversarial_mutation_gross_pf_trade(self, tmp_path):
        """Mutation test: changing Gross PF Trade in report.md must be detected by verify_report."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "**Gross Profit Factor (Trade):**" in report_md
        mutated_report = re.sub(
            r"(\*\*Gross Profit Factor \(Trade\):\*\*\s*)[\d\.]+",
            r"\g<1>9.99",
            report_md,
        )
        assert mutated_report != report_md
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail on Gross PF Trade mutation"
        out = res.stdout + res.stderr
        assert "Gross Profit Factor (Trade)" in out

    def test_adversarial_mutation_gross_pf_leg(self, tmp_path):
        """Mutation test: changing Gross PF Leg in report.md must be detected by verify_report."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "**Gross Profit Factor (Leg):**" in report_md
        mutated_report = re.sub(
            r"(\*\*Gross Profit Factor \(Leg\):\*\*\s*)[\d\.]+",
            r"\g<1>9.99",
            report_md,
        )
        assert mutated_report != report_md
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail on Gross PF Leg mutation"
        out = res.stdout + res.stderr
        assert "Gross Profit Factor (Leg)" in out

    def test_adversarial_mutation_roi(self, tmp_path):
        """Mutation test: changing ROI in report.md must be detected by verify_report."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "+41.07%" in report_md
        mutated_report = report_md.replace("+41.07%", "+99.99%")
        assert mutated_report != report_md
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail on ROI mutation"
        out = res.stdout + res.stderr
        assert "ROI" in out

    def test_adversarial_mutation_delete_combo_table(self, tmp_path):
        """Mutation test: deleting the combo table from report.md must cause verify_report to fail."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "## 7. Performance by Combo" in report_md
        sec7_start = report_md.find("## 7. Performance by Combo")
        sec8_start = report_md.find("## 8. Reconciliation Audit")
        assert sec7_start != -1 and sec8_start != -1
        mutated_report = report_md[:sec7_start] + report_md[sec8_start:]
        assert "## 7. Performance by Combo" not in mutated_report
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail when combo table is deleted"
        out = res.stdout + res.stderr
        assert "Performance by Combo" in out

    def test_adversarial_mutation_delete_exit_reason_table(self, tmp_path):
        """Mutation test: deleting the exit reasons table from report.md must cause verify_report to fail."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "## 6. Exit Reason Breakdown" in report_md
        sec6_start = report_md.find("## 6. Exit Reason Breakdown")
        sec7_start = report_md.find("## 7. Performance by Combo")
        assert sec6_start != -1 and sec7_start != -1
        mutated_report = report_md[:sec6_start] + report_md[sec7_start:]
        assert "## 6. Exit Reason Breakdown" not in mutated_report
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail when exit reason table is deleted"
        out = res.stdout + res.stderr
        assert "Exit Reason Breakdown" in out

    def test_adversarial_mutation_pnl_004_usd(self, tmp_path):
        """Mutation test: changing Net PnL by exactly $0.04 USD must be caught by tol=0.009."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "$410.70" in report_md
        # Mutate by exactly 4 cents ($410.70 -> $410.74)
        mutated_report = report_md.replace("$410.70", "$410.74")
        assert mutated_report != report_md
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail when Net PnL differs by $0.04 USD"
        out = res.stdout + res.stderr
        assert "Net PnL" in out
        assert "0.04" in out

    def test_adversarial_mutation_delete_gates_table(self, tmp_path):
        """Mutation test: deleting the Validation Gates Audit table from report.md must cause verify_report to fail."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "## Validation Gates Audit" in report_md
        sec_start = report_md.find("## Validation Gates Audit")
        sec_end = report_md.find("## 1. Accounting & Signals")
        assert sec_start != -1 and sec_end != -1
        mutated_report = report_md[:sec_start] + report_md[sec_end:]
        assert "## Validation Gates Audit" not in mutated_report
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail when Validation Gates Audit table is deleted"
        out = res.stdout + res.stderr
        assert "Validation Gates Audit" in out or "Gate" in out

    def test_adversarial_mutation_real_data_false_with_real_pass(self, tmp_path):
        """Mutation test: report claiming REAL_DATA_PASS but Real Market Data: False must fail fail-closed."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "(Real Market Data: True)" in report_md
        mutated_report = report_md.replace("(Real Market Data: True)", "(Real Market Data: False)")
        assert mutated_report != report_md
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail when REAL_DATA_PASS has Real Market Data: False"
        out = res.stdout + res.stderr
        assert "Real Market Data is False" in out or "contradictory" in out

    def test_adversarial_mutation_tp_sl_ambiguity_zero(self, tmp_path):
        """Mutation test: changing TP/SL ambiguity from 3 to 0 must be caught against trades.parquet."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "- **TP/SL Same-Candle Ambiguity:** 3 legs" in report_md
        mutated_report = report_md.replace("- **TP/SL Same-Candle Ambiguity:** 3 legs", "- **TP/SL Same-Candle Ambiguity:** 0 legs")
        assert mutated_report != report_md
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail when TP/SL ambiguity is mutated to 0"
        out = res.stdout + res.stderr
        assert "TP/SL Same-Candle Ambiguity" in out

    def test_adversarial_mutation_trailing_ambiguity_zero(self, tmp_path):
        """Mutation test: changing Trailing Stop Ambiguity from 486 to 0 must be caught against trades.parquet."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        assert "- **Trailing Stop Ambiguity:** 486 legs" in report_md
        mutated_report = report_md.replace("- **Trailing Stop Ambiguity:** 486 legs", "- **Trailing Stop Ambiguity:** 0 legs")
        assert mutated_report != report_md
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail when Trailing ambiguity is mutated to 0"
        out = res.stdout + res.stderr
        assert "Trailing Stop Ambiguity" in out

    def test_adversarial_mutation_missing_companion_ledger(self, tmp_path):
        """Mutation test: missing companion ledger (signals.parquet) must fail fail-closed."""
        mode_tmp, report_md = _setup_pristine_run(tmp_path, "ALL_SIGNALS")
        sig_p = mode_tmp / "signals.parquet"
        assert sig_p.exists()
        sig_p.unlink()

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail when signals.parquet is missing"
        out = res.stdout + res.stderr
        assert "MISSING_COMPANION_LEDGER" in out

    def test_regression_validation_and_ambiguity_preservation(self, tmp_path):
        """Regression test: Re-analyzing golden runs preserves validation block, gates, and non-zero ambiguity."""
        for mode in ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]:
            mode_tmp, report_md = _setup_pristine_run(tmp_path, mode)
            an_data = json.loads((mode_tmp / "analysis.json").read_text(encoding="utf-8"))

            # 1. Validation block must be present and real data pass
            assert "validation" in an_data, f"Mode {mode} missing 'validation' in analysis.json"
            val = an_data["validation"]
            assert val.get("overall_status") == "REAL_DATA_PASS"
            assert val.get("is_real_data") is True
            assert "GATE_A_DATA" in val.get("gates", {})
            assert "GATE_B_STRATEGY" in val.get("gates", {})
            assert "GATE_C_EXECUTION" in val.get("gates", {})
            assert "GATE_D_RESULT" in val.get("gates", {})

            # 2. Report markdown must contain validation gates table
            assert "## Validation Gates Audit" in report_md
            assert "Gate A" in report_md
            assert "Gate B" in report_md
            assert "Gate C" in report_md
            assert "Gate D" in report_md
            assert "(Real Market Data: True)" in report_md

            # 3. Ambiguity counts must match trades ledger
            amb = an_data.get("ambiguity", {})
            assert amb.get("intrabar_tp_sl_ambiguous_count") == 3
            if mode == "ALL_SIGNALS":
                assert amb.get("intrabar_trailing_ambiguous_count") == 486
            else:
                assert amb.get("intrabar_trailing_ambiguous_count") == 459


    def test_adversarial_mutation_m2_trade_net_pnl_changed(self, tmp_path):
        """Adversarial M2: Mutate net_pnl of one leg in trades.parquet -> verify_report on old report must fail."""
        from autotrade_v3.lab.run_manager import RunManager, RunManifest

        mode = "ALL_SIGNALS"
        golden_dir = REPO_ROOT / "runs" / "golden" / "v3-08" / mode
        mode_tmp = tmp_path / "m2"
        shutil.copytree(golden_dir, mode_tmp)

        sim_res = _load_run_as_sim_result(mode_tmp, mode)
        analysis = ResearchAnalyzer.analyze_simulation(sim_res)
        with open(mode_tmp / "analysis.json", "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2)
        with open(mode_tmp / "run_manifest.json", "r", encoding="utf-8") as f:
            manifest = RunManifest(**json.load(f))

        # Write pristine report.md
        report_md = RunManager(runs_dir=mode_tmp)._render_markdown_report(manifest, analysis)
        (mode_tmp / "report.md").write_text(report_md, encoding="utf-8")

        # Now mutate trades.parquet
        trades_df = pd.read_parquet(mode_tmp / "trades.parquet")
        trades_df_mut = trades_df.copy()
        trades_df_mut.loc[0, "net_pnl"] += 100.0
        trades_df_mut.to_parquet(mode_tmp / "trades.parquet")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail when trades.parquet is mutated"

    def test_adversarial_mutation_m3_symbol_changed(self, tmp_path):
        """Adversarial M3: Alter symbol of one leg in copy -> reconciliation detects mismatch."""
        mode = "ALL_SIGNALS"
        golden_dir = REPO_ROOT / "runs" / "golden" / "v3-08" / mode
        trades = _load_trades_parquet_as_records(golden_dir / "trades.parquet")

        # Mutate symbol of leg 0 to ETH-USDT when combo is C2 (which is BTC only)
        trades[0].symbol = "ETH-USDT"
        trades[0].combo_id = 2
        trades[0].combo_name = "C2"

        sim_res = SimulationResult(
            mode=mode,
            trades=trades,
            events=[],
            accepted_signals=[],
            skipped_signals=[],
            rejected_signals=[],
            snapshots=pd.DataFrame(),
            ambiguity_stats={},
        )
        analysis = ResearchAnalyzer.analyze_simulation(sim_res)
        rec = analysis["reconciliation"]
        assert rec["symbol_combo_alignment_pass"] is False
        assert rec["reconciliation_pass"] is False

    def test_adversarial_mutation_m4_hardcoded_metric_scan(self, tmp_path):
        """Adversarial M4: Inserting a hardcoded metric constant into a file copy makes the scan fail."""
        fake_file = tmp_path / "fake_reporter.py"
        fake_file.write_text("def report():\n    total_signals = 5984\n", encoding="utf-8")

        banned_patterns = [r"\b5984\b"]
        text = fake_file.read_text(encoding="utf-8")
        found = any(re.search(pat, text) for pat in banned_patterns)
        assert found is True, "Hardcode scanner must catch hardcoded 5984"

    def test_to_utc_ms_all_timestamp_types(self):
        """P3-1 unit test: to_utc_ms with naive datetime, aware datetime, str, Timestamp, int."""
        from datetime import datetime, timezone
        from autotrade_v3.engine.candle_loader import to_utc_ms

        expected_ms = 1704067200000  # 2024-01-01 00:00:00 UTC

        # 1. Integer ms
        assert to_utc_ms(expected_ms) == expected_ms

        # 2. String
        assert to_utc_ms("2024-01-01 00:00:00Z") == expected_ms
        assert to_utc_ms("2024-01-01T00:00:00+00:00") == expected_ms

        # 3. Naive datetime
        dt_naive = datetime(2024, 1, 1, 0, 0, 0)
        assert to_utc_ms(dt_naive) == expected_ms

        # 4. Aware datetime
        dt_aware = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert to_utc_ms(dt_aware) == expected_ms

        # 5. Naive pandas Timestamp
        ts_naive = pd.Timestamp("2024-01-01 00:00:00")
        assert to_utc_ms(ts_naive) == expected_ms

        # 6. Aware pandas Timestamp
        ts_aware = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
        assert to_utc_ms(ts_aware) == expected_ms

    def test_simulate_trailing_leg_max_bars_held_zero(self):
        """P3-2 unit test: _simulate_trailing_leg does not raise UnboundLocalError when max_bars_held=0."""
        from autotrade_v3.engine.candle_loader import datetime_index_to_ms, generate_synthetic_m1
        from autotrade_v3.engine.execution_simulator import (
            ExecutionSimulator,
            ExitPolicy,
            RiskConfig,
            Signal,
        )

        df_m1 = generate_synthetic_m1("BTC-USDT", start="2024-01-01", periods=100)
        sim = ExecutionSimulator(risk_config=RiskConfig())

        sig = Signal(
            symbol="BTC-USDT",
            combo_id=1,
            combo_name="C1",
            timeframe="15m",
            side="BUY",
            signal_close_ms=int(df_m1.index[0].timestamp() * 1000),
            signal_close_dt=df_m1.index[0],
            bar_position=0,
            bar_open_ms=int(df_m1.index[0].timestamp() * 1000),
            entry_price=float(df_m1.iloc[1]["open"]),
            tp_pct=0.01,
            sl_pct=0.01,
            tp_price=float(df_m1.iloc[1]["open"]) * 1.01,
            sl_price=float(df_m1.iloc[1]["open"]) * 0.99,
            approved=True,
            warmup_ready=True,
            smc_dir=1,
            ob_blocked=False,
            provider="SYNTHETIC",
            is_new6=False,
            signal_id="SIG_TEST_0",
        )

        policy = ExitPolicy(
            tp_pct=0.01,
            sl_pct=0.01,
            trailing_act_pct=0.005,
            trailing_cb_pct=0.005,
            max_bars_held=0,  # Forces immediate exit on entry bar
        )

        # Must execute cleanly without UnboundLocalError for trailing_ambiguous
        trade = sim._simulate_trailing_leg(
            trade_id="TR_TEST_0",
            leg_id="TR_TEST_0_L2",
            leg_type="TRAIL_HALF",
            sig=sig,
            entry_idx=1,
            entry_price=float(df_m1.iloc[1]["open"]),
            leg_notional=50.0,
            is_buy=True,
            sl_price=float(df_m1.iloc[1]["open"]) * 0.99,
            opens=df_m1["open"].to_numpy(dtype=float),
            highs=df_m1["high"].to_numpy(dtype=float),
            lows=df_m1["low"].to_numpy(dtype=float),
            closes=df_m1["close"].to_numpy(dtype=float),
            n_bars=len(df_m1),
            t_ms_array=datetime_index_to_ms(df_m1.index),
            df_bars=df_m1,
            policy=policy,
        )

        assert trade.exit_reason == "MAX_BARS_HELD"
        assert trade.intrabar_trailing_ambiguous is False
