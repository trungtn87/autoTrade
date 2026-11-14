"""Unit tests for autotrade_v3.runstore (deterministic run_id, immutability, index reindex, equity)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from autotrade_v3.runstore import (
    NonReproducibleError,
    RunStore,
    build_equity_df,
    compute_run_id,
    compute_sim_config_hash,
)


def _sample_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    signals = pd.DataFrame([{"signal_id": "S1", "symbol": "BTC-USDT", "signal_close_ms": 1700000059999}])
    decisions = pd.DataFrame([{"signal_id": "S1", "status": "ACCEPTED", "reason": ""}])
    legs = pd.DataFrame(
        [
            {
                "signal_id": "S1",
                "leg_id": "S1#L1",
                "trade_id": "T1",
                "symbol": "BTC-USDT",
                "exit_time_ms": 1700000120000,
                "net_pnl": 1.25,
            }
        ]
    )
    events = pd.DataFrame([{"signal_id": "S1", "event_id": "E1", "event_type": "ENTRY"}])
    return signals, decisions, legs, events


class TestRunStore(unittest.TestCase):
    def test_run_id_deterministic_and_sensitive_to_every_component(self) -> None:
        sim_h = compute_sim_config_hash(initial_equity=1000.0, notional_per_trade=100.0)
        base = dict(
            dataset_hash="ds1",
            spec_hash="sp1",
            mode="ALL_SIGNALS",
            time_window={"start": "2024-01-01", "end": "2024-02-01"},
            symbols=["BTC-USDT", "ETH-USDT"],
            sim_config_hash=sim_h,
            cost_profile_hash="cp1",
            ambiguity_policy="CONSERVATIVE",
            filters=[{"name": "f1", "config_hash": "fh1"}],
            code_sha="abc1234",
        )
        rid0 = compute_run_id(**base)
        self.assertTrue(rid0.startswith("R_"))
        self.assertEqual(len(rid0), 18)
        # Reordered symbols -> same run_id (canonical sorted)
        self.assertEqual(rid0, compute_run_id(**{**base, "symbols": ["ETH-USDT", "BTC-USDT"]}))

        mutations = [
            {"dataset_hash": "ds2"},
            {"spec_hash": "sp2"},
            {"mode": "ONE_TRADE_PER_COMBO"},
            {"time_window": {"start": "2024-01-01", "end": "2024-03-01"}},
            {"symbols": ["BTC-USDT"]},
            {"sim_config_hash": compute_sim_config_hash(initial_equity=2000.0, notional_per_trade=100.0)},
            {"cost_profile_hash": "cp2"},
            {"ambiguity_policy": "OPTIMISTIC"},
            {"filters": [{"name": "f1", "config_hash": "fh2"}]},
            {"code_sha": "def5678"},
        ]
        for mut in mutations:
            kw = {**base, **mut}
            self.assertNotEqual(rid0, compute_run_id(**kw), f"Mutation {mut} did not change run_id")

    def test_save_reproducible_and_non_reproducible_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            store = RunStore(store_dir=tmp / "store", scratch_dir=tmp / "scratch")
            signals, decisions, legs, events = _sample_frames()
            rid = "R_0123456789abcdef"
            manifest = {
                "run_id": rid,
                "execution_mode": "ALL_SIGNALS",
                "initial_equity": 1000.0,
                "total_signals": 1,
                "accepted_entries": 1,
                "total_legs": 1,
            }
            analysis = {"pnl": {"net_pnl": 1.25, "roi_pct": 0.125}}

            r1 = store.save_run(
                run_id=rid,
                manifest=manifest,
                signals_df=signals,
                decisions_df=decisions,
                legs_df=legs,
                events_df=events,
                analysis=analysis,
            )
            self.assertFalse(r1.reproducible_hit)
            self.assertFalse(r1.in_scratch)
            self.assertTrue((r1.run_dir / "legs.parquet").exists())
            self.assertTrue((r1.run_dir / "equity.parquet").exists())

            # Re-run identical -> reproducible_hit=True
            r2 = store.save_run(
                run_id=rid,
                manifest=manifest,
                signals_df=signals,
                decisions_df=decisions,
                legs_df=legs,
                events_df=events,
                analysis=analysis,
            )
            self.assertTrue(r2.reproducible_hit)

            # Re-run different content -> NonReproducibleError, old content intact
            legs_mod = legs.copy()
            legs_mod["net_pnl"] = 999.0
            with self.assertRaises(NonReproducibleError):
                store.save_run(
                    run_id=rid,
                    manifest=manifest,
                    signals_df=signals,
                    decisions_df=decisions,
                    legs_df=legs_mod,
                    events_df=events,
                    analysis=analysis,
                )
            persisted_legs = pd.read_parquet(r1.run_dir / "legs.parquet")
            self.assertEqual(float(persisted_legs["net_pnl"].iloc[0]), 1.25)

    def test_dirty_tree_routes_to_scratch_and_reindex_rebuilds_duckdb(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            store = RunStore(store_dir=tmp / "store", scratch_dir=tmp / "scratch")
            signals, decisions, legs, events = _sample_frames()
            manifest = {"execution_mode": "ALL_SIGNALS", "initial_equity": 1000.0}
            analysis = {"pnl": {"net_pnl": 1.25, "roi_pct": 0.125}}

            res_dirty = store.save_run(
                run_id="R_dirty000000000001",
                manifest=manifest,
                signals_df=signals,
                decisions_df=decisions,
                legs_df=legs,
                events_df=events,
                analysis=analysis,
                code_dirty=True,
            )
            self.assertTrue(res_dirty.in_scratch)
            self.assertEqual(res_dirty.run_dir.parent, tmp / "scratch")
            self.assertEqual(len(store.index.list_runs()), 0)

            res_clean = store.save_run(
                run_id="R_clean000000000001",
                manifest=manifest,
                signals_df=signals,
                decisions_df=decisions,
                legs_df=legs,
                events_df=events,
                analysis=analysis,
                code_dirty=False,
            )
            before = store.index.list_runs()
            self.assertEqual(len(before), 1)
            self.assertEqual(before[0]["run_id"], res_clean.run_id)

            # Delete index.duckdb and rebuild
            store.index.db_path.unlink()
            self.assertFalse(store.index.db_path.exists())
            rebuilt_count = store.index.reindex()
            self.assertEqual(rebuilt_count, 1)
            after = store.index.list_runs()
            self.assertEqual(json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True))

    def test_equity_kinds(self) -> None:
        _, _, legs, _ = _sample_frames()
        eq_a = build_equity_df(mode="ALL_SIGNALS", initial_equity=1000.0, legs_df=legs)
        self.assertEqual(eq_a["equity_kind"].iloc[0], "CLOSED_TRADE")
        self.assertAlmostEqual(float(eq_a["equity"].iloc[0]), 1001.25)

        snaps = pd.DataFrame(
            [{"timestamp_ms": 1700000060000, "balance": 1000.0, "unrealized_pnl": -2.5, "equity": 997.5, "used_margin": 10.0, "free_margin": 987.5}]
        )
        eq_c = build_equity_df(mode="BINGX_PORTFOLIO", initial_equity=1000.0, legs_df=legs, snapshots_df=snaps)
        self.assertEqual(eq_c["equity_kind"].iloc[0], "M1_MTM")
        self.assertAlmostEqual(float(eq_c["equity"].iloc[0]), 997.5)
