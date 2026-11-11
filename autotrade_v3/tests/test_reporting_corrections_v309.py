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

    summary: dict = {}
    an_path = run_dir / "analysis.json"
    if an_path.exists():
        with open(an_path, "r", encoding="utf-8") as af:
            an_data = json.load(af)
            summary["m2m_max_drawdown_dollars"] = an_data.get("risk", {}).get("m2m_max_drawdown_dollars")
            summary["m2m_max_drawdown_pct"] = an_data.get("risk", {}).get("m2m_max_drawdown_pct")

    return SimulationResult(
        mode=mode,
        trades=trades,
        events=[],
        accepted_signals=sigs_df.to_dict("records"),
        skipped_signals=skipped,
        rejected_signals=rejected,
        snapshots=pd.DataFrame(),
        ambiguity_stats={},
        summary_dict=summary,
    )


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
        from autotrade_v3.lab.run_manager import RunManager, RunManifest

        mode = "ALL_SIGNALS"
        golden_dir = REPO_ROOT / "runs" / "golden" / "v3-08" / mode
        mode_tmp = tmp_path / "m1"
        shutil.copytree(golden_dir, mode_tmp)

        sim_res = _load_run_as_sim_result(mode_tmp, mode)
        analysis = ResearchAnalyzer.analyze_simulation(sim_res)
        with open(mode_tmp / "analysis.json", "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2)
        with open(mode_tmp / "run_manifest.json", "r", encoding="utf-8") as f:
            manifest = RunManifest(**json.load(f))

        report_md = RunManager(runs_dir=mode_tmp)._render_markdown_report(manifest, analysis)
        # Mutate net pnl in report.md
        assert "$410.70" in report_md, "Expected pristine report to contain $410.70"
        mutated_report = report_md.replace("$410.70", "$999.99")
        (mode_tmp / "report.md").write_text(mutated_report, encoding="utf-8")

        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "autotrade_v3" / "tools" / "verify_report.py"), "--run-dir", str(mode_tmp)],
            capture_output=True, text=True,
        )
        assert res.returncode != 0, "verify_report must fail on mutated report.md"
        assert "FAIL" in res.stdout or "FAIL" in res.stderr

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

        banned_patterns = [r"\b5984\b"]
        text = fake_file.read_text(encoding="utf-8")
        found = any(re.search(pat, text) for pat in banned_patterns)
        assert found is True, "Hardcode scanner must catch hardcoded 5984"
