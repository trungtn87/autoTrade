"""Integration tests for V3-14: RunStore + SignalCache + Filters + Diff + Validation (C5/D1)."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import pandas as pd

from autotrade_v3.external.annotations import AnnotationStore
from autotrade_v3.filters import AnnotationGuardFilter, FilterContext
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager
from autotrade_v3.runstore import RunStore, SignalCache, diff_runs
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.validation.gates_c import gate_c5
from autotrade_v3.validation.gates_d import gate_d1
from autotrade_v3.validation.runner import VerifyContext


class _TweakedExitStrategyWrapper:
    """Wraps a strategy keeping signal_spec_hash identical while changing tp1_pct & exit_spec_hash."""

    def __init__(self, base: object, new_tp1_pct: float = 0.035) -> None:
        self._base = base
        self._new_tp1 = new_tp1_pct
        self.strategy_id = base.strategy_id  # type: ignore[attr-defined]
        self.version = base.version  # type: ignore[attr-defined]

    def get_signal_spec_hash(self) -> str:
        return self._base.get_signal_spec_hash()  # type: ignore[attr-defined]

    def get_exit_spec_hash(self) -> str:
        return "tweaked_exit_" + str(self._new_tp1)

    def get_spec_hash(self) -> str:
        return "tweaked_spec_" + str(self._new_tp1)

    def get_exit_policy(self, symbol: str, combo_id: int) -> object:
        p = self._base.get_exit_policy(symbol, combo_id)  # type: ignore[attr-defined]
        return replace(p, tp_pct=self._new_tp1)

    def generate_signals(self, *args: object, **kwargs: object) -> list[object]:
        return self._base.generate_signals(*args, **kwargs)  # type: ignore[attr-defined]


class TestV314Integration(unittest.TestCase):
    def test_cache_hit_on_exit_change_and_diff_no_signal_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cache = SignalCache(tmp / "cache")
            store = RunStore(store_dir=tmp / "store", scratch_dir=tmp / "scratch")
            runner = ExperimentRunner(run_manager=RunManager(runs_dir=tmp / "legacy"))

            r1 = runner.run_experiment(
                strategy_id="20FINAL_BASELINE",
                mode="ALL_SIGNALS",
                symbols=["ETH-USDT"],
                start="2024-01-01",
                end="2024-03-01",
                smoke_test=True,
                cost_profile="TEST_ONLY",
                ambiguity_policy="CONSERVATIVE",
                use_signal_cache=True,
                signal_cache=cache,
                use_runstore=True,
                run_store=store,
            )
            self.assertEqual(r1.signal_gen_calls, 1)
            self.assertEqual(r1.signal_source, "COMPUTED")

            # Run 2: same signal_spec_hash, changed exit_policy (tp1_pct=0.035)
            base_strat = StrategyRegistry.get("20FINAL_BASELINE")
            tweaked = _TweakedExitStrategyWrapper(base_strat, new_tp1_pct=0.035)
            orig_get = StrategyRegistry.get
            try:
                StrategyRegistry.get = staticmethod(lambda sid: tweaked if sid == "20FINAL_BASELINE" else orig_get(sid))  # type: ignore[assignment]
                r2 = runner.run_experiment(
                    strategy_id="20FINAL_BASELINE",
                    mode="ALL_SIGNALS",
                    symbols=["ETH-USDT"],
                    start="2024-01-01",
                    end="2024-03-01",
                    smoke_test=True,
                    cost_profile="TEST_ONLY",
                    ambiguity_policy="CONSERVATIVE",
                    use_signal_cache=True,
                    signal_cache=cache,
                    use_runstore=True,
                    run_store=store,
                )
            finally:
                StrategyRegistry.get = orig_get  # type: ignore[assignment]

            self.assertEqual(r2.signal_gen_calls, 0)
            self.assertEqual(r2.signal_source, "CACHE")

            d = diff_runs(r1.run_dir, r2.run_dir, out_dir=tmp / "diff_out")
            self.assertEqual(d.summary["by_category"]["SIGNAL"]["count"], 0)
            self.assertLessEqual(d.summary["invariant_diff"], 1e-9)

    def test_filter_annotation_pipeline_and_c5_d1_pass_on_runstore(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cache = SignalCache(tmp / "cache")
            store = RunStore(store_dir=tmp / "store", scratch_dir=tmp / "scratch")
            runner = ExperimentRunner(run_manager=RunManager(runs_dir=tmp / "legacy"))

            # First run without filter to grab a real synthetic signal_close_ms
            r0 = runner.run_experiment(
                strategy_id="20FINAL_BASELINE",
                mode="ALL_SIGNALS",
                symbols=["ETH-USDT"],
                start="2024-01-01",
                end="2024-03-01",
                smoke_test=True,
                cost_profile="TEST_ONLY",
                ambiguity_policy="CONSERVATIVE",
                use_signal_cache=True,
                signal_cache=cache,
                use_runstore=True,
                run_store=store,
            )
            sigs_df = pd.read_parquet(r0.run_dir / "signals.parquet")
            self.assertGreater(len(sigs_df), 0)
            target_close_ms = int(sigs_df["signal_close_ms"].iloc[0])

            ann_store = AnnotationStore(tmp / "annotations.duckdb")
            ann_file = tmp / "ann.json"
            ann_file.write_text(
                json.dumps(
                    [
                        {"symbol": "ETH-USDT", "as_of_ms": target_close_ms, "verdict": "DROP"},
                        {"symbol": "ETH-USDT", "as_of_ms": target_close_ms + 1, "verdict": "KEEP"},
                    ]
                ),
                encoding="utf-8",
            )
            ann_store.import_file(ann_file, source="gemini_saved", as_of_verified=True)

            f_ctx = FilterContext(annotation_store=ann_store)
            flt = AnnotationGuardFilter(source="gemini_saved")
            r_flt = runner.run_experiment(
                strategy_id="20FINAL_BASELINE",
                mode="ALL_SIGNALS",
                symbols=["ETH-USDT"],
                start="2024-01-01",
                end="2024-03-01",
                smoke_test=True,
                cost_profile="TEST_ONLY",
                ambiguity_policy="CONSERVATIVE",
                use_signal_cache=True,
                signal_cache=cache,
                filters=[flt],
                filter_context=f_ctx,
                use_runstore=True,
                run_store=store,
            )
            dec_df = pd.read_parquet(r_flt.run_dir / "signal_decisions.parquet")
            self.assertGreater(int((dec_df["status"] == "FILTERED").sum()), 0)

            repo = Path(".").resolve()
            ctx = VerifyContext(r_flt.run_dir, dataset_root=repo, repo_root=repo)
            self.assertEqual(gate_c5(ctx).status, "PASS")
            d1_res = gate_d1(ctx)
            self.assertEqual(d1_res.status, "PASS", msg=str(d1_res))
