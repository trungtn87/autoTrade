"""Experiment orchestration for AutoTrade V3 Offline Research Lab."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
import pandas as pd

from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import (
    generate_synthetic_m1,
    resample_m1_to_timeframe,
    to_utc_ms,
)
from autotrade_v3.engine.execution_simulator import ExecutionSimulator, SimulationResult
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.lab.run_manager import RunManager, RunManifest, get_git_commit
from autotrade_v3.lab.validation_gates import ValidationGates, ValidationReport
from autotrade_v3.strategy.registry import StrategyRegistry


@dataclass
class ExperimentResult:
    run_id: str
    run_dir: Path
    manifest: RunManifest
    sim_result: SimulationResult
    analysis: dict[str, Any]
    validation_report: ValidationReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "manifest": self.manifest.to_dict(),
            "analysis": self.analysis,
            "validation_report": self.validation_report.to_dict() if self.validation_report else None,
        }


class ExperimentRunner:
    """Orchestrates end-to-end backtesting runs with complete provenance."""

    def __init__(
        self,
        data_layer: DataLayer | None = None,
        run_manager: RunManager | None = None,
    ):
        self.data_layer = data_layer or DataLayer()
        self.run_manager = run_manager or RunManager()

    def run_experiment(
        self,
        strategy_id: str = "20FINAL_BASELINE",
        mode: Literal["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"] | str = "ALL_SIGNALS",
        symbols: list[str] | None = None,
        start: str = "2023-11-01",
        end: str = "2026-11-01",
        initial_equity: float = 1000.0,
        notional_per_trade: float = 100.0,
        slippage_rate: float = 0.0,
        fee_rate_taker: float = 0.0005,
        smoke_test: bool = False,
    ) -> ExperimentResult:
        """Execute a full research experiment and persist artifacts."""
        target_symbols = symbols or ["BTC-USDT", "ETH-USDT"]
        strategy = StrategyRegistry.get(strategy_id)
        start_ms = to_utc_ms(start)
        end_ms = to_utc_ms(end)

        git_sha = get_git_commit()
        data_manifest = self.data_layer.get_manifest()
        data_hash = data_manifest.data_manifest_hash if not smoke_test else "SYNTHETIC_SMOKE_HASH"

        # 1. Load Data
        candles_m1_eval: dict[str, pd.DataFrame] = {}
        strategy_input_data: dict[str, pd.DataFrame] = {}

        for sym in target_symbols:
            if smoke_test:
                base_px = 65000.0 if "BTC" in sym else 3500.0
                df_m1_full = generate_synthetic_m1(sym, start=start, periods=75000, base_price=base_px, seed=42)
                df_m1_eval = df_m1_full.loc[(df_m1_full.index.asi8 // 1_000_000 >= start_ms) & (df_m1_full.index.asi8 // 1_000_000 < end_ms)]
                candles_m1_eval[sym] = df_m1_eval
                strategy_input_data[sym] = resample_m1_to_timeframe(df_m1_full, timeframe_min=15)
            else:
                # Load M1 with warmup buffer for signal priming
                df_m1_full = self.data_layer.query_m1(sym, start, end, include_warmup=True)
                # Slice evaluation window for simulator
                df_m1_eval = df_m1_full.loc[(df_m1_full.index.asi8 // 1_000_000 >= start_ms) & (df_m1_full.index.asi8 // 1_000_000 < end_ms)]
                candles_m1_eval[sym] = df_m1_eval
                # Resample full M1 to M15 (strictly no-lookahead) for strategy input
                strategy_input_data[sym] = self.data_layer.query_timeframe(sym, "15m", start, end, include_warmup=True)

        # 2. Generate Signals via Strategy Engine
        all_signals = strategy.generate_signals(
            data=strategy_input_data,
            symbols=target_symbols,
            only_approved=True,
            only_warmup_ready=True,
        )

        # Filter signals whose earliest execution falls within evaluation window
        eval_signals = [
            s for s in all_signals
            if s.signal_close_ms + 1 >= start_ms and s.signal_close_ms + 1 < end_ms
        ]
        eval_signals.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))

        # 3. Simulate Execution
        risk_cfg = RiskConfig(
            initial_equity=initial_equity,
            notional_per_trade=notional_per_trade,
            slippage_rate=slippage_rate,
            taker_fee_rate=fee_rate_taker,
        )
        simulator = ExecutionSimulator(risk_config=risk_cfg)
        sim_result = simulator.simulate_research_mode(
            mode=mode,
            signals=eval_signals,
            candle_dict=candles_m1_eval,
        )

        # 4. Analyze Results
        analysis = ResearchAnalyzer.analyze_simulation(
            sim_result=sim_result,
            initial_equity=initial_equity,
        )

        # 5. Audit Run via Independent Validation Gates (P1-03)
        val_report = ValidationGates.audit_run(
            strategy=strategy,
            mode=sim_result.mode,
            symbols=target_symbols,
            start=start,
            end=end,
            sim_result=sim_result,
            analysis=analysis,
            signals=eval_signals,
            smoke_test=smoke_test,
            data_layer=self.data_layer,
        )
        analysis["validation"] = val_report.to_dict()

        # 6. Build Manifest & Save Run
        run_id = self.run_manager.generate_run_id(
            strategy_id=strategy.strategy_id,
            mode=sim_result.mode,
            window_start=start,
            window_end=end,
        )

        acct = analysis["accounting"]
        manifest = RunManifest(
            run_id=run_id,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            config_hash=strategy.get_config_hash(),
            data_manifest_hash=data_hash,
            git_sha=git_sha,
            execution_mode=sim_result.mode,
            time_window={"start": start, "end": end},
            symbols=target_symbols,
            fee_rate_taker=fee_rate_taker,
            slippage_rate=slippage_rate,
            initial_equity=initial_equity,
            notional_per_trade=notional_per_trade,
            total_signals=acct["total_signals"],
            accepted_entries=acct["accepted_entries"],
            total_legs=acct["total_legs"],
            skipped_signals_count=acct["skipped_by_concurrency"],
            rejected_signals_count=acct["rejected_by_margin"],
            ambiguity_stats=sim_result.ambiguity_stats,
            validation_status=val_report.overall_status,
        )

        run_dir = self.run_manager.save_run(
            manifest=manifest,
            sim_result=sim_result,
            analysis=analysis,
        )

        return ExperimentResult(
            run_id=run_id,
            run_dir=run_dir,
            manifest=manifest,
            sim_result=sim_result,
            analysis=analysis,
            validation_report=val_report,
        )
