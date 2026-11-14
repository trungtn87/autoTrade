"""Unit tests for autotrade_v3.runstore.diff (categories 0-9, priority order, sum invariant)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from autotrade_v3.runstore.diff import diff_runs


def _write_run(
    run_dir: Path,
    *,
    run_id: str,
    dataset_hash: str = "ds1",
    ambiguity_policy: str = "CONSERVATIVE",
    signals: list[dict],
    decisions: list[dict],
    legs: list[dict],
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_manifest.json").write_text(
        json.dumps({"run_id": run_id, "dataset_hash": dataset_hash, "ambiguity_policy": ambiguity_policy}),
        encoding="utf-8",
    )
    pd.DataFrame(signals).to_parquet(run_dir / "signals.parquet", index=False)
    pd.DataFrame(decisions).to_parquet(run_dir / "signal_decisions.parquet", index=False)
    pd.DataFrame(legs).to_parquet(run_dir / "legs.parquet", index=False)
    return run_dir


def _sig(sid: str, sym: str = "BTC-USDT", close_ms: int = 1000) -> dict:
    return {
        "signal_id": sid,
        "symbol": sym,
        "combo_id": 1,
        "combo_name": "C1",
        "timeframe": "15m",
        "side": "BUY",
        "signal_close_ms": close_ms,
        "entry_price": 100.0,
    }


def _leg(
    sid: str,
    *,
    net: float = 1.0,
    entry_t: int = 1001,
    entry_raw: float = 100.0,
    entry_fill: float = 100.0,
    exit_t: int = 2000,
    exit_reason: str = "TP",
    exit_trig: float = 102.0,
    exit_fill: float = 102.0,
    fee_total: float = 0.1,
    ambig_type: str = "NONE",
) -> dict:
    return {
        "signal_id": sid,
        "leg_id": f"{sid}#L1",
        "symbol": "BTC-USDT",
        "combo_name": "C1",
        "entry_time_ms": entry_t,
        "entry_px_raw": entry_raw,
        "entry_px_fill": entry_fill,
        "exit_time_ms": exit_t,
        "exit_reason": exit_reason,
        "exit_px_trigger": exit_trig,
        "exit_px_fill": exit_fill,
        "fee_total": fee_total,
        "ambiguity_type": ambig_type,
        "net_pnl": net,
    }


class TestRunDiff(unittest.TestCase):
    def test_all_categories_and_invariant(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sigs_a = [
                _sig("S_ID"),
                _sig("S_DATA"),
                _sig("S_SIG_ONLY_A"),
                _sig("S_ENTRY"),
                _sig("S_AMBIG"),
                _sig("S_EXIT"),
                _sig("S_COST"),
                _sig("S_UNEXP"),
            ]
            sigs_b = [
                _sig("S_ID"),
                _sig("S_DATA"),
                _sig("S_SIG_ONLY_B"),
                _sig("S_ENTRY"),
                _sig("S_AMBIG"),
                _sig("S_EXIT"),
                _sig("S_COST"),
                _sig("S_UNEXP"),
            ]
            decs_a = [{"signal_id": s["signal_id"], "status": "ACCEPTED"} for s in sigs_a]
            decs_b = [{"signal_id": s["signal_id"], "status": "ACCEPTED"} for s in sigs_b]
            # S_ENTRY becomes SKIPPED in B
            decs_b[3]["status"] = "SKIPPED"

            legs_a = [
                _leg("S_ID", net=1.0),
                _leg("S_DATA", net=1.0),
                _leg("S_SIG_ONLY_A", net=2.0),
                _leg("S_ENTRY", net=1.5),
                _leg("S_AMBIG", net=-1.0, exit_reason="SL_INTRABAR_AMBIGUOUS", exit_trig=98.0, ambig_type="TP_AND_SL"),
                _leg("S_EXIT", net=1.0, exit_reason="TP", exit_trig=102.0),
                _leg("S_COST", net=1.0, fee_total=0.1),
                _leg("S_UNEXP", net=1.0),
            ]
            legs_b = [
                _leg("S_ID", net=1.0),
                _leg("S_DATA", net=3.0, exit_reason="TP"),  # DATA wins over anything else
                _leg("S_SIG_ONLY_B", net=4.0),
                # S_ENTRY has no leg in B
                _leg("S_AMBIG", net=2.0, exit_reason="TP", exit_trig=102.0, ambig_type="TP_AND_SL"),
                _leg("S_EXIT", net=1.5, exit_reason="TP", exit_trig=103.0),
                _leg("S_COST", net=0.8, fee_total=0.3),
                _leg("S_UNEXP", net=9.0),  # identical fields except net_pnl -> UNEXPLAINED
            ]

            dir_a = _write_run(tmp / "A", run_id="R_A", dataset_hash="dsA", signals=sigs_a, decisions=decs_a, legs=legs_a)
            dir_b = _write_run(tmp / "B", run_id="R_B", dataset_hash="dsB", signals=sigs_b, decisions=decs_b, legs=legs_b)

            res = diff_runs(
                dir_a,
                dir_b,
                out_dir=tmp / "out",
                m1_diff_checker=lambda sym, t0, t1: True,  # only S_DATA? Wait: checker returns True for all, so let's scope to S_DATA time or test priority!
            )
            cats = dict(zip(res.diff_df["signal_id"], res.diff_df["category"]))
            # When m1_diff_checker is True for all, DATA (priority 1) wins!
            self.assertEqual(cats["S_DATA"], "DATA")
            self.assertEqual(cats["S_SIG_ONLY_A"], "DATA")

            # Now scope m1_diff_checker strictly to S_DATA's symbol/window via a dedicated symbol
            sigs_a2 = [_sig("S_DATA", sym="SOL-USDT") if s["signal_id"] == "S_DATA" else s for s in sigs_a]
            sigs_b2 = [_sig("S_DATA", sym="SOL-USDT") if s["signal_id"] == "S_DATA" else s for s in sigs_b]
            dir_a2 = _write_run(tmp / "A2", run_id="R_A2", dataset_hash="dsA", signals=sigs_a2, decisions=decs_a, legs=legs_a)
            dir_b2 = _write_run(tmp / "B2", run_id="R_B2", dataset_hash="dsB", signals=sigs_b2, decisions=decs_b, legs=legs_b)

            res2 = diff_runs(
                dir_a2,
                dir_b2,
                out_dir=tmp / "out2",
                m1_diff_checker=lambda sym, t0, t1: sym == "SOL-USDT",
            )
            cats2 = dict(zip(res2.diff_df["signal_id"], res2.diff_df["category"]))
            self.assertEqual(cats2["S_ID"], "IDENTICAL")
            self.assertEqual(cats2["S_DATA"], "DATA")
            self.assertEqual(cats2["S_SIG_ONLY_A"], "SIGNAL")
            self.assertEqual(cats2["S_SIG_ONLY_B"], "SIGNAL")
            self.assertEqual(cats2["S_ENTRY"], "ENTRY")
            self.assertEqual(cats2["S_AMBIG"], "AMBIGUITY")
            self.assertEqual(cats2["S_EXIT"], "EXIT_LOGIC")
            self.assertEqual(cats2["S_COST"], "COST")
            self.assertEqual(cats2["S_UNEXP"], "UNEXPLAINED")
            self.assertLessEqual(res2.summary["invariant_diff"], 1e-9)
            self.assertEqual(res2.summary["unexplained_count"], 1)
            self.assertTrue((tmp / "out2/diff.parquet").exists())
            self.assertTrue((tmp / "out2/diff_summary.json").exists())

    def test_priority_order_entry_beats_ambiguity_beats_exit_beats_cost(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sigs = [_sig("S1")]
            decs = [{"signal_id": "S1", "status": "ACCEPTED"}]
            la = [_leg("S1", entry_t=1001, exit_reason="SL_INTRABAR_AMBIGUOUS", ambig_type="TP_AND_SL", fee_total=0.1, net=-1.0)]
            lb = [_leg("S1", entry_t=1002, exit_reason="TP", ambig_type="TP_AND_SL", fee_total=0.2, net=2.0)]
            da = _write_run(tmp / "A", run_id="R_A", signals=sigs, decisions=decs, legs=la)
            db = _write_run(tmp / "B", run_id="R_B", signals=sigs, decisions=decs, legs=lb)
            res = diff_runs(da, db)
            self.assertEqual(res.diff_df["category"].iloc[0], "ENTRY")
