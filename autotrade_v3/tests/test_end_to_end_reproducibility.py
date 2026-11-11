"""Unit tests verifying 100% deterministic reproducibility of backtest runs."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from scripts.run_v3_backtest import run_backtest


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestReproducibility(unittest.TestCase):
    def test_deterministic_identical_runs(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            out1 = Path(tmp1)
            out2 = Path(tmp2)

            run_backtest(
                catalog_path=None,
                out_dir=out1,
                symbols=["ETH-USDT"],
                mode="virtual",
                smoke_test=True,
                initial_equity=1000.0,
                notional=100.0,
            )

            run_backtest(
                catalog_path=None,
                out_dir=out2,
                symbols=["ETH-USDT"],
                mode="virtual",
                smoke_test=True,
                initial_equity=1000.0,
                notional=100.0,
            )

            # Compare CSV trades hash
            csv1_hash = hash_file(out1 / "virtual_trades.csv")
            csv2_hash = hash_file(out2 / "virtual_trades.csv")
            self.assertEqual(csv1_hash, csv2_hash, "Trade records must be byte-for-byte identical")

            # Compare metrics in JSON
            j1 = json.loads((out1 / "virtual_summary.json").read_text(encoding="utf-8"))["overall_metrics"]
            j2 = json.loads((out2 / "virtual_summary.json").read_text(encoding="utf-8"))["overall_metrics"]
            self.assertEqual(j1["net_pnl"], j2["net_pnl"])
            self.assertEqual(j1["total_trades"], j2["total_trades"])
            self.assertEqual(j1["win_rate_pct"], j2["win_rate_pct"])
            self.assertEqual(j1["max_drawdown_dollars"], j2["max_drawdown_dollars"])


if __name__ == "__main__":
    unittest.main()
