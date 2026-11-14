"""Unit tests for autotrade_v3.runstore.signal_cache."""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from autotrade_v3.runstore.signal_cache import SignalCache, compute_signal_cache_key, rebind_exit_policies


@dataclass(frozen=True)
class _DummyPolicy:
    tp1_pct: float
    sl_pct: float

    def to_dict(self) -> dict[str, float]:
        return {"tp1_pct": self.tp1_pct, "sl_pct": self.sl_pct}

    def policy_hash(self) -> str:
        return f"h_{self.tp1_pct}_{self.sl_pct}"


class _DummyStrategy:
    def get_exit_policy(self, symbol: str, combo_id: int) -> _DummyPolicy:
        return _DummyPolicy(tp1_pct=0.03, sl_pct=0.015)


@dataclass
class _DummySig:
    symbol: str
    combo_id: int
    side: str
    entry_price: float
    tp_pct: float
    sl_pct: float
    tp_price: float
    sl_price: float
    exit_policy: str
    exit_policy_hash: str


class TestSignalCache(unittest.TestCase):
    def test_key_sensitivity_and_hit_miss_corrupt(self) -> None:
        k0 = compute_signal_cache_key(
            dataset_hash="ds1",
            signal_spec_hash="sig1",
            symbols=["BTC-USDT", "ETH-USDT"],
            start="2024-01-01",
            end="2024-02-01",
        )
        # Reordered symbols -> same key
        self.assertEqual(
            k0,
            compute_signal_cache_key(
                dataset_hash="ds1",
                signal_spec_hash="sig1",
                symbols=["ETH-USDT", "BTC-USDT"],
                start="2024-01-01",
                end="2024-02-01",
            ),
        )
        # Changing signal_spec_hash, dataset_hash, symbols, or window -> different key
        self.assertNotEqual(
            k0,
            compute_signal_cache_key(
                dataset_hash="ds2", signal_spec_hash="sig1", symbols=["BTC-USDT", "ETH-USDT"], start="2024-01-01", end="2024-02-01"
            ),
        )
        self.assertNotEqual(
            k0,
            compute_signal_cache_key(
                dataset_hash="ds1", signal_spec_hash="sig2", symbols=["BTC-USDT", "ETH-USDT"], start="2024-01-01", end="2024-02-01"
            ),
        )
        self.assertNotEqual(
            k0,
            compute_signal_cache_key(
                dataset_hash="ds1", signal_spec_hash="sig1", symbols=["BTC-USDT"], start="2024-01-01", end="2024-02-01"
            ),
        )
        self.assertNotEqual(
            k0,
            compute_signal_cache_key(
                dataset_hash="ds1", signal_spec_hash="sig1", symbols=["BTC-USDT", "ETH-USDT"], start="2024-01-01", end="2024-03-01"
            ),
        )

        with tempfile.TemporaryDirectory() as td:
            cache = SignalCache(Path(td) / "cache")
            self.assertFalse(cache.load(k0).hit)

            df = pd.DataFrame([{"signal_id": "S1", "symbol": "BTC-USDT", "signal_close_ms": 1700000059999}])
            cache.save(k0, df, code_sha="abc", compute_time_s=0.123, key_inputs={"dataset_hash": "ds1"})
            hit = cache.load(k0)
            self.assertTrue(hit.hit)
            self.assertEqual(hit.warning, "")
            self.assertEqual(len(hit.signals_df), 1)
            self.assertEqual(cache.stats()["entries"], 1)

            # Corrupt 1 byte in signals.parquet -> CACHE_CORRUPT
            pq_path = cache.entry_dir(k0) / "signals.parquet"
            raw = bytearray(pq_path.read_bytes())
            raw[len(raw) // 2] ^= 0xFF
            pq_path.write_bytes(bytes(raw))
            corrupt = cache.load(k0)
            self.assertFalse(corrupt.hit)
            self.assertEqual(corrupt.warning, "CACHE_CORRUPT")

    def test_rebind_exit_policies(self) -> None:
        sig = _DummySig(
            symbol="BTC-USDT",
            combo_id=1,
            side="BUY",
            entry_price=100.0,
            tp_pct=0.01,
            sl_pct=0.01,
            tp_price=101.0,
            sl_price=99.0,
            exit_policy="{}",
            exit_policy_hash="old",
        )
        out = rebind_exit_policies([sig], _DummyStrategy())
        self.assertAlmostEqual(out[0].tp_pct, 0.03)
        self.assertAlmostEqual(out[0].sl_pct, 0.015)
        self.assertAlmostEqual(out[0].tp_price, 103.0)
        self.assertAlmostEqual(out[0].sl_price, 98.5)
        self.assertEqual(out[0].exit_policy_hash, "h_0.03_0.015")
