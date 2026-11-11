"""Unit tests for RiskConfig, leverage, fee, and slippage calculations."""
import unittest
from autotrade_v3.engine.risk_manager import RiskConfig


class TestRiskManager(unittest.TestCase):
    def test_leverage_and_margin(self):
        cfg = RiskConfig(initial_equity=1000.0, notional_per_trade=100.0)
        self.assertEqual(cfg.get_leverage("BTC-USDT"), 100.0)
        self.assertEqual(cfg.get_leverage("ETH-USDT"), 50.0)
        self.assertAlmostEqual(cfg.calc_margin("BTC-USDT", 100.0), 1.0)
        self.assertAlmostEqual(cfg.calc_margin("ETH-USDT", 100.0), 2.0)

    def test_fee_calculation(self):
        cfg = RiskConfig(taker_fee_rate=0.0005, maker_fee_rate=0.0002)
        # 100 notional * 0.0005 = 0.05 taker
        self.assertAlmostEqual(cfg.calc_fee(100.0, is_taker=True), 0.05)
        # 100 notional * 0.0002 = 0.02 maker
        self.assertAlmostEqual(cfg.calc_fee(100.0, is_taker=False), 0.02)

    def test_slippage_direction(self):
        cfg = RiskConfig(slippage_rate=0.001)  # 0.1% slippage
        # BUY Entry: pay more
        self.assertAlmostEqual(cfg.apply_slippage(100.0, "BUY", is_entry=True), 100.1)
        # BUY Exit (sell): receive less
        self.assertAlmostEqual(cfg.apply_slippage(100.0, "BUY", is_entry=False), 99.9)
        # SELL Entry: sell at lower price
        self.assertAlmostEqual(cfg.apply_slippage(100.0, "SELL", is_entry=True), 99.9)
        # SELL Exit (buy back): buy at higher price
        self.assertAlmostEqual(cfg.apply_slippage(100.0, "SELL", is_entry=False), 100.1)


if __name__ == "__main__":
    unittest.main()
