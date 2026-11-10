"""Unit tests for candle loader and resampler."""
import unittest
import pandas as pd
from autotrade_v3.engine.candle_loader import (
    generate_synthetic_m1,
    resample_m1_to_timeframe,
    validate_m1,
)


class TestCandleLoader(unittest.TestCase):
    def test_synthetic_m1_validity(self):
        df = generate_synthetic_m1("BTC-USDT", start="2024-01-01", periods=180, seed=123)
        self.assertEqual(len(df), 180)
        validate_m1(df)

    def test_gap_detection(self):
        df = generate_synthetic_m1("BTC-USDT", start="2024-01-01", periods=180, seed=123)
        # Drop a row in the middle to create a gap
        df_gapped = df.drop(df.index[10])
        with self.assertRaisesRegex(ValueError, "gaps"):
            validate_m1(df_gapped)

    def test_incomplete_resample_bucket_rejected(self):
        df = generate_synthetic_m1("BTC-USDT", start="2024-01-01", periods=14, seed=123)
        with self.assertRaisesRegex(ValueError, "Incomplete 15-minute bucket"):
            resample_m1_to_timeframe(df, 15)

    def test_resample_m15_aggregation_correctness(self):
        df = generate_synthetic_m1("BTC-USDT", start="2024-01-01", periods=30, seed=123)
        df_m15 = resample_m1_to_timeframe(df, 15)
        self.assertEqual(len(df_m15), 2)
        # Check first bucket
        first_15 = df.iloc[:15]
        self.assertEqual(df_m15.iloc[0]["open"], first_15.iloc[0]["open"])
        self.assertEqual(df_m15.iloc[0]["high"], first_15["high"].max())
        self.assertEqual(df_m15.iloc[0]["low"], first_15["low"].min())
        self.assertEqual(df_m15.iloc[0]["close"], first_15.iloc[-1]["close"])
        self.assertAlmostEqual(df_m15.iloc[0]["volume"], first_15["volume"].sum())


if __name__ == "__main__":
    unittest.main()
