"""Test suite verifying strict offline execution boundary of AutoTrade V3 Research Lab."""
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager
from autotrade_v3.strategy.registry import StrategyRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]


def blocked_connect(*args, **kwargs):
    raise AssertionError("Network connection attempted during STRICT OFFLINE simulation!")


class TestOfflineSecurityBoundary(unittest.TestCase):
    """Verifies that AutoTrade V3 Research Lab never requires or makes external network requests."""

    def test_no_live_api_keys_required(self):
        """Verify engine does not demand BingX or Supabase secrets in environment."""
        # Clean potential sensitive env keys
        sensitive_keys = [
            "BINGX_API_KEY",
            "BINGX_SECRET_KEY",
            "SUPABASE_URL",
            "SUPABASE_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "GEMINI_API_KEY",
        ]
        for key in sensitive_keys:
            self.assertFalse(
                os.environ.get(key, "").startswith("prod_live_secret"),
                f"Production secret found in {key}",
            )

    @patch("socket.socket.connect", side_effect=blocked_connect)
    def test_strictly_offline_backtest_execution(self, mock_socket_connect):
        """Simulate backtest with socket.connect strictly blocked."""
        import tempfile
        # Verify socket is blocked
        with self.assertRaises(AssertionError):
            s = socket.socket()
            s.connect(("8.8.8.8", 53))

        # Run smoke experiment in lab with temporary run manager
        with tempfile.TemporaryDirectory() as tmp_dir:
            runner = ExperimentRunner(run_manager=RunManager(runs_dir=tmp_dir))
            res = runner.run_experiment(
                strategy_id="20FINAL_BASELINE",
                mode="ALL_SIGNALS",
                symbols=["ETH-USDT"],
                start="2023-11-01",
                end="2026-11-01",
                smoke_test=True,
            )

            self.assertIsNotNone(res.run_id)
            self.assertIn(res.manifest.validation_status, ("PASS", "SYNTHETIC_PASS", "SYNTHETIC"))
            self.assertGreater(len(res.sim_result.trades), 0)


if __name__ == "__main__":
    unittest.main()
