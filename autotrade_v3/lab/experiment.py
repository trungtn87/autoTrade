"""Experiment orchestration for AutoTrade V3 Offline Research Lab."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Literal
import pandas as pd

from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import (
    datetime_index_to_ms,
    generate_synthetic_m1,
    resample_m1_to_timeframe,
    to_utc_ms,
)
from autotrade_v3.engine.execution_simulator import ExecutionSimulator, SimulationResult
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.lab.run_manager import (
    RunManager,
    RunManifest,
    get_env_versions,
    get_git_commit,
    get_git_state,
)
from autotrade_v3.lab.validation_gates import ValidationGates, ValidationReport
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.tools.dataset_manifest import generate_dataset_manifest, verify_dataset_integrity


@dataclass
class ExperimentResult:
    run_id: str
    run_dir: Path
    manifest: RunManifest
    sim_result: SimulationResult
    analysis: dict[str, Any]
    validation_report: ValidationReport | None = None
    signal_gen_calls: int = 0
    signal_source: str = "COMPUTED"
    cache_warning: str = ""
    reproducible_hit: bool = False

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
        strategy_id: str = "20FINAL_FIXED_V1",
        mode: Literal["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"] | str = "ALL_SIGNALS",
        symbols: list[str] | None = None,
        start: str = "2023-11-01",
        end: str = "2026-11-01",
        initial_equity: float = 1000.0,
        notional_per_trade: float = 100.0,
        slippage_rate: float = 0.0,
        fee_rate_taker: float = 0.0005,
        smoke_test: bool = False,
        is_golden: bool = False,
        cost_profile: str | None = None,
        ambiguity_policy: str = "CONSERVATIVE",
        use_signal_cache: bool = True,
        signal_cache: Any | None = None,
        filters: list[Any] | None = None,
        filter_context: Any | None = None,
        use_runstore: bool = False,
        run_store: Any | None = None,
    ) -> ExperimentResult:
        """Execute a full research experiment and persist artifacts."""
        from autotrade_v3.sim.ambiguity import normalize_ambiguity_policy
        from autotrade_v3.sim.costs import CostModel
        from autotrade_v3.sim.engine import SIMULATOR_VERSION

        resolved_policy = normalize_ambiguity_policy(ambiguity_policy)
        if not smoke_test and not cost_profile:
            raise ValueError("--cost-profile is required for real-data runs (no implicit default).")
        resolved_profile_name = cost_profile or "IDEAL"
        cost_model = CostModel.from_profile_name(
            resolved_profile_name,
            is_real_data=(not smoke_test),
        )

        target_symbols = symbols or ["BTC-USDT", "ETH-USDT"]
        strategy = StrategyRegistry.get(strategy_id)
        start_ms = to_utc_ms(start)
        end_ms = to_utc_ms(end)

        code_sha, code_dirty, diff_sha256 = get_git_state()
        if is_golden:
            if code_dirty or code_sha == "UNKNOWN_COMMIT" or not code_sha:
                raise RuntimeError(
                    f"Golden run rejected: git working tree is dirty or git commit cannot be determined "
                    f"(code_sha={code_sha}, code_dirty={code_dirty})"
                )

        env_versions = get_env_versions()
        data_manifest = self.data_layer.get_manifest()
        data_hash = data_manifest.data_manifest_hash if not smoke_test else "SYNTHETIC_SMOKE_HASH"

        dataset_hash = "SYNTHETIC_SMOKE_HASH"
        if not smoke_test:
            if is_golden:
                # Live verification against physical partitions
                is_valid, live_hash, err_msg = verify_dataset_integrity(
                    catalog_path=self.data_layer.catalog_path,
                    data_dir=self.data_layer.data_dir,
                )
                if not is_valid:
                    raise RuntimeError(f"Golden run rejected: dataset integrity verification failed: {err_msg}")
                dataset_hash = live_hash
            else:
                gm_path = Path("runs/golden/v3-08/dataset_manifest.json")
                if gm_path.exists():
                    try:
                        with open(gm_path, "r", encoding="utf-8") as f:
                            dataset_hash = json.load(f).get("dataset_hash", "")
                    except Exception:
                        dataset_hash = ""
                if not dataset_hash:
                    try:
                        dm = generate_dataset_manifest(
                            catalog_path=self.data_layer.catalog_path,
                            data_dir=self.data_layer.data_dir,
                        )
                        dataset_hash = dm.get("dataset_hash", "")
                    except Exception:
                        dataset_hash = data_hash

        # 1. Load Data
        candles_m1_eval: dict[str, pd.DataFrame] = {}
        strategy_input_data: dict[str, pd.DataFrame] = {}

        for sym in target_symbols:
            if smoke_test:
                base_px = 65000.0 if "BTC" in sym else 3500.0
                df_m1_full = generate_synthetic_m1(sym, start=start, periods=75000, base_price=base_px, seed=42)
                t_m1_full = datetime_index_to_ms(df_m1_full.index)
                df_m1_eval = df_m1_full.loc[(t_m1_full >= start_ms) & (t_m1_full < end_ms)]
                candles_m1_eval[sym] = df_m1_eval
                strategy_input_data[sym] = resample_m1_to_timeframe(df_m1_full, timeframe_min=15)
            else:
                # Load M1 with warmup buffer for signal priming
                df_m1_full = self.data_layer.query_m1(sym, start, end, include_warmup=True)
                # Slice evaluation window for simulator
                t_m1_full = datetime_index_to_ms(df_m1_full.index)
                df_m1_eval = df_m1_full.loc[(t_m1_full >= start_ms) & (t_m1_full < end_ms)]
                candles_m1_eval[sym] = df_m1_eval
                # Resample full M1 to M15 (strictly no-lookahead) for strategy input
                strategy_input_data[sym] = self.data_layer.query_timeframe(sym, "15m", start, end, include_warmup=True)

        from autotrade_v3.filters import FilterContext, apply_filters, filters_manifest_spec
        from autotrade_v3.runstore import (
            RunStore,
            SignalCache,
            compute_signal_cache_key,
            compute_sim_config_hash,
            rebind_exit_policies,
        )
        from autotrade_v3.sim.engine import Signal as SimSignal

        sig_spec_hash = strategy.get_signal_spec_hash() if hasattr(strategy, "get_signal_spec_hash") else strategy.get_config_hash()
        exit_spec_hash = strategy.get_exit_spec_hash() if hasattr(strategy, "get_exit_spec_hash") else strategy.get_config_hash()
        full_spec_hash = strategy.get_spec_hash() if hasattr(strategy, "get_spec_hash") else strategy.get_config_hash()

        # 2. Generate or Load Signals via SignalCache
        cache_enabled = bool(use_signal_cache and ((not smoke_test) or signal_cache is not None))
        active_cache = signal_cache or (SignalCache() if cache_enabled else None)
        signal_cache_key = ""
        signal_source = "COMPUTED"
        cache_warning = ""
        signal_gen_calls = 0
        signal_compute_time_s = 0.0
        all_signals: list[Any] = []

        if active_cache is not None:
            signal_cache_key = compute_signal_cache_key(
                dataset_hash=dataset_hash,
                signal_spec_hash=sig_spec_hash,
                symbols=target_symbols,
                start=start,
                end=end,
                include_warmup=True,
            )
            t_c0 = time.perf_counter()
            c_res = active_cache.load(signal_cache_key)
            if c_res.warning:
                cache_warning = c_res.warning
            if c_res.hit and c_res.signals_df is not None:
                sig_fields = {f.name for f in fields(SimSignal)}
                loaded = [
                    SimSignal(**{k: v for k, v in row.items() if k in sig_fields})
                    for row in c_res.signals_df.to_dict("records")
                ]
                all_signals = rebind_exit_policies(loaded, strategy)
                signal_source = "CACHE"
                signal_compute_time_s = time.perf_counter() - t_c0

        if signal_source != "CACHE":
            t_g0 = time.perf_counter()
            signal_gen_calls += 1
            all_signals = strategy.generate_signals(
                data=strategy_input_data,
                symbols=target_symbols,
                only_approved=True,
                only_warmup_ready=True,
            )
            signal_compute_time_s = time.perf_counter() - t_g0
            if active_cache is not None and signal_cache_key:
                sig_df = pd.DataFrame([asdict(s) for s in all_signals]) if all_signals else pd.DataFrame(
                    columns=[f.name for f in fields(SimSignal)]
                )
                active_cache.save(
                    signal_cache_key,
                    sig_df,
                    code_sha=code_sha,
                    compute_time_s=signal_compute_time_s,
                    key_inputs={
                        "dataset_hash": dataset_hash,
                        "signal_spec_hash": sig_spec_hash,
                        "symbols": sorted(target_symbols),
                        "start": start,
                        "end": end,
                    },
                )

        # Filter signals whose earliest execution falls within evaluation window.
        # In smoke_test mode, synthetic M1 spans 75,000 bars regardless of `end`,
        # so bound by each symbol's generated M1 horizon to avoid trailing UNFILLABLE signals.
        max_m1_by_sym: dict[str, int] = {}
        if smoke_test:
            for sym, df_m1 in candles_m1_eval.items():
                if not df_m1.empty:
                    max_m1_by_sym[sym] = int(datetime_index_to_ms(df_m1.index)[-1])

        eval_signals = [
            s for s in all_signals
            if s.signal_close_ms + 1 >= start_ms
            and s.signal_close_ms + 1 < (
                min(end_ms, max_m1_by_sym[s.symbol] + 1)
                if smoke_test and s.symbol in max_m1_by_sym
                else end_ms
            )
        ]
        eval_signals.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))

        # 2b. Apply external filters (post-cache, pre-simulator)
        active_filters = list(filters or [])
        f_ctx = filter_context or FilterContext()
        f_res = apply_filters(eval_signals, active_filters, f_ctx)
        sim_signals = f_res.kept_signals
        pre_filtered_decisions = f_res.filtered_decisions

        # 3. Simulate Execution
        risk_cfg = RiskConfig(
            initial_equity=initial_equity,
            notional_per_trade=notional_per_trade,
            slippage_rate=slippage_rate,
            taker_fee_rate=fee_rate_taker,
            cost_profile=cost_model.profile.name,
            cost_model=cost_model,
        )
        simulator = ExecutionSimulator(
            risk_config=risk_cfg,
            cost_model=cost_model,
            ambiguity_policy=resolved_policy,
        )
        sim_result = simulator.simulate_research_mode(
            mode=mode,
            signals=sim_signals,
            candle_dict=candles_m1_eval,
            pre_filtered_decisions=pre_filtered_decisions,
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
        filters_spec = filters_manifest_spec(active_filters)
        sim_cfg_hash = compute_sim_config_hash(
            initial_equity=initial_equity,
            notional_per_trade=notional_per_trade,
            final14_exit_mode="50TP_50TRAIL",
        )
        if is_golden:
            run_id = sim_result.mode
        elif use_runstore:
            run_id = RunStore.build_run_id(
                dataset_hash=dataset_hash,
                spec_hash=full_spec_hash,
                mode=sim_result.mode,
                time_window={"start": start, "end": end},
                symbols=target_symbols,
                sim_config_hash=sim_cfg_hash,
                cost_profile_hash=cost_model.profile.profile_hash,
                ambiguity_policy=resolved_policy,
                filters=filters_spec,
                code_sha=code_sha,
            )
        else:
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
            config_hash=full_spec_hash,
            data_manifest_hash=data_hash,
            git_sha=code_sha,
            execution_mode=sim_result.mode,
            time_window={"start": start, "end": end},
            symbols=target_symbols,
            fee_rate_taker=fee_rate_taker,
            slippage_rate=slippage_rate,
            initial_equity=initial_equity,
            notional_per_trade=notional_per_trade,
            total_signals=len(eval_signals),
            accepted_entries=acct["accepted_entries"],
            total_legs=acct["total_legs"],
            skipped_signals_count=acct["skipped_by_concurrency"],
            rejected_signals_count=acct["rejected_by_margin"],
            ambiguity_stats=sim_result.ambiguity_stats,
            validation_status="PARTIALLY_VERIFIED" if f_res.used_unverified_annotations and val_report.overall_status == "VERIFIED" else val_report.overall_status,
            code_sha=code_sha,
            code_dirty=code_dirty,
            diff_sha256=diff_sha256,
            python_version=env_versions["python_version"],
            pandas_version=env_versions["pandas_version"],
            numpy_version=env_versions["numpy_version"],
            duckdb_version=env_versions["duckdb_version"],
            pyarrow_version=env_versions["pyarrow_version"],
            dataset_hash=dataset_hash,
            signal_spec_hash=sig_spec_hash,
            exit_spec_hash=exit_spec_hash,
            spec_hash=full_spec_hash,
            cost_profile=cost_model.profile.name,
            cost_profile_hash=cost_model.profile.profile_hash,
            ambiguity_policy=resolved_policy,
            simulation_limits=sim_result.summary_dict.get("simulation_limits"),
            liquidation_status=str(sim_result.summary_dict.get("status", "OK")),
            is_golden=is_golden,
            signal_source=signal_source,
            signal_cache_key=signal_cache_key,
            signal_compute_time_s=round(signal_compute_time_s, 6),
            sim_config_hash=sim_cfg_hash,
            filters=filters_spec,
            filtered_signals_count=len(pre_filtered_decisions),
            as_of_unverified=bool(f_res.used_unverified_annotations),
            dropped_future_annotations=int(f_res.dropped_future_annotations),
            simulator_version=SIMULATOR_VERSION,
            parity_status=(
                "PARITY_PENDING_LIVE_VERIFICATION"
                if strategy.strategy_id == "20FINAL_FIXED_V1"
                else "LEGACY_BASELINE"
            ),
        )

        reproducible_hit = False
        if use_runstore and not is_golden:
            active_store = run_store or RunStore()
            if eval_signals:
                sig_dicts_out = []
                for s in eval_signals:
                    sd = asdict(s)
                    if "exit_policy" in sd:
                        sd["exit_policy"] = str(sd["exit_policy"]) if sd["exit_policy"] is not None else ""
                    sd["signal_close_dt"] = str(s.signal_close_dt)
                    sig_dicts_out.append(sd)
                sig_df_out = pd.DataFrame(sig_dicts_out)
            else:
                sig_df_out = pd.DataFrame()
            dec_df_out = pd.DataFrame([asdict(d) for d in getattr(sim_result, "signal_decisions", [])])
            legs_df_out = pd.DataFrame([asdict(t) for t in sim_result.trades]) if sim_result.trades else pd.DataFrame(
                columns=["signal_id", "leg_id", "trade_id", "symbol", "exit_time_ms", "net_pnl"]
            )
            events_df_out = (
                pd.DataFrame([asdict(e) for e in sim_result.events])
                if sim_result.events
                else pd.DataFrame(columns=["signal_id", "event_id", "event_type"])
            )
            md_report = self.run_manager._render_markdown_report(manifest, analysis)
            save_res = active_store.save_run(
                run_id=run_id,
                manifest=manifest.to_dict(),
                signals_df=sig_df_out,
                decisions_df=dec_df_out,
                legs_df=legs_df_out,
                events_df=events_df_out,
                snapshots_df=sim_result.snapshots,
                analysis=analysis,
                validation=val_report.to_dict(),
                report_md=md_report,
                code_dirty=code_dirty,
            )
            run_dir = save_res.run_dir
            reproducible_hit = save_res.reproducible_hit
        else:
            run_dir = self.run_manager.save_run(
                manifest=manifest,
                sim_result=sim_result,
                analysis=analysis,
                signals=eval_signals,
            )

        # 7. Post-save 12 Independent Validation Gates audit on persisted artifacts
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
            artifacts_dir=run_dir,
            write_result=(not is_golden and not reproducible_hit),
        )

        if use_runstore and not is_golden:
            if not reproducible_hit:
                active_store.update_validation(run_dir, val_report.to_dict(), analysis=analysis, manifest=manifest.to_dict())
        else:
            self.run_manager.update_post_validation(
                run_dir=run_dir,
                manifest=manifest,
                analysis=analysis,
                val_report=val_report,
            )

        return ExperimentResult(
            run_id=run_id,
            run_dir=run_dir,
            manifest=manifest,
            sim_result=sim_result,
            analysis=analysis,
            validation_report=val_report,
            signal_gen_calls=signal_gen_calls,
            signal_source=signal_source,
            cache_warning=cache_warning,
            reproducible_hit=reproducible_hit,
        )
