"""Unit tests for autotrade_v3.external.ledger_import."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from autotrade_v3.external.ledger_import import import_external_ledger
from autotrade_v3.runstore import RunStore, diff_runs


class TestImportLedger(unittest.TestCase):
    def test_missing_required_column_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            csv_p = tmp / "ext.csv"
            pd.DataFrame([{"sym": "BTC-USDT", "net": 1.0}]).to_csv(csv_p, index=False)
            map_p = tmp / "map.json"
            map_p.write_text(json.dumps({"symbol": "sym", "net_pnl": "net"}), encoding="utf-8")

            store = RunStore(store_dir=tmp / "store", scratch_dir=tmp / "scratch")
            with self.assertRaises(ValueError):
                import_external_ledger(csv_p, map_p, store=store)

    def test_import_records_missing_optional_cols_and_supports_diff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            csv_p = tmp / "ext.csv"
            pd.DataFrame(
                [
                    {
                        "sym": "BTC-USDT",
                        "combo": "C1",
                        "dir": "BUY",
                        "sig_ms": 1704067199999,
                        "in_ms": 1704067200000,
                        "in_px": 50000.0,
                        "out_ms": 1704067260000,
                        "out_px": 50500.0,
                        "pnl": 0.90,
                    }
                ]
            ).to_csv(csv_p, index=False)
            map_p = tmp / "map.json"
            map_p.write_text(
                json.dumps(
                    {
                        "symbol": "sym",
                        "combo_name": "combo",
                        "side": "dir",
                        "signal_close_ms": "sig_ms",
                        "entry_time_ms": "in_ms",
                        "entry_price": "in_px",
                        "exit_time_ms": "out_ms",
                        "exit_price": "out_px",
                        "net_pnl": "pnl",
                    }
                ),
                encoding="utf-8",
            )
            store = RunStore(store_dir=tmp / "store", scratch_dir=tmp / "scratch")
            res = import_external_ledger(csv_p, map_p, store=store)
            manifest = json.loads((res.run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source"], "EXTERNAL")
            self.assertIn("fee_entry", manifest["missing_columns"])
            self.assertIn("slippage_cost", manifest["missing_columns"])

            legs = pd.read_parquet(res.run_dir / "legs.parquet")
            self.assertEqual(legs["signal_id"].iloc[0], "SIG_BTCUSDT_C1_15m_BUY_1704067199999")
            self.assertTrue(pd.isna(legs["fee_entry"].iloc[0]))

            # Self-diff works cleanly
            d = diff_runs(res.run_dir, res.run_dir)
            self.assertEqual(d.summary["expected_delta_net"], 0.0)
            self.assertEqual(d.summary["unexplained_count"], 0)
