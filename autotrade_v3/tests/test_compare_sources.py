import tempfile
import unittest
from pathlib import Path

from autotrade_v3.data_backtest.compare_sources import compare, signatures, utc_ms


class SourceComparisonTests(unittest.TestCase):
    def test_same_input(self):
        a={("BTC-USDT","C1","BUY",1),("ETH-USDT","C15","SELL",2)}
        self.assertEqual(compare(a,set(a))["jaccard_pct"],100.0)
        self.assertEqual(compare(a,set(a))["baseline_only"],0)
    def test_cross_venue(self):
        a={("BTC-USDT","C1","BUY",1),("BTC-USDT","C4","SELL",2)}
        b={("BTC-USDT","C1","BUY",1),("BTC-USDT","C7","SELL",3)}
        self.assertEqual(compare(a,b)["jaccard_pct"],33.333)
        self.assertEqual(compare(a,b)["baseline_only"],1)
        self.assertEqual(compare(a,b)["derived_only"],1)
    def test_duplicate_source_event_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"events.csv"
            path.write_text("symbol,combo,side,signal_close_ms,approved\n"
                            "BTC-USDT,C1,BUY,1704067200001,true\n"
                            "BTC-USDT,C1,BUY,1704067200001,true\n")
            with self.assertRaisesRegex(ValueError,"duplicate"):
                signatures([path],utc_ms("2024-01-01"),utc_ms("2024-01-02"))
