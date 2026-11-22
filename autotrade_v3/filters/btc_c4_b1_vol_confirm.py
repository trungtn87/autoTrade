"""Pure candle-only filter for BTC-USDT/C4 B1_VOL_CONFIRM (V3_LAB_10)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.base import FilterContext, FilterDecision
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv

BTC_C4_SYMBOL = "BTC-USDT"
BTC_C4_COMBO_ID = 4
BTC_C4_MIN_VOL_RATIO_20 = 2.20
H1_MS = 3_600_000


def build_btc_c4_vol_ratio_lookup(df_btc_m15: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute closed-bar 1H vol_ratio_20 = volume / SMA(volume, 20) lookup arrays."""
    d1 = resample_ohlcv(ensure_dt(df_btc_m15), "1h")
    if d1.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    v = d1["volume"].astype(float)
    vol_ratio_20 = (v / v.rolling(20).mean().replace(0, np.nan)).to_numpy(dtype=float)
    close_ms_1h = (datetime_index_to_ms(d1.index) + H1_MS - 1).astype(np.int64)
    return close_ms_1h, vol_ratio_20


class BtcC4B1VolConfirmFilter:
    """Require closed 1H vol_ratio_20 >= 2.20 for BTC-USDT/C4 entries."""

    name: str = "BTC_C4_B1_VOL_CONFIRM"

    def __init__(
        self,
        close_ms_1h: np.ndarray,
        vol_ratio_20: np.ndarray,
        min_vol_ratio_20: float = BTC_C4_MIN_VOL_RATIO_20,
    ) -> None:
        self.close_ms_1h = close_ms_1h
        self.vol_ratio_20 = vol_ratio_20
        self.min_vol_ratio_20 = float(min_vol_ratio_20)

    @classmethod
    def from_m15_dataframe(
        cls,
        df_btc_m15: pd.DataFrame,
        min_vol_ratio_20: float = BTC_C4_MIN_VOL_RATIO_20,
    ) -> BtcC4B1VolConfirmFilter:
        close_ms_1h, vol_ratio_20 = build_btc_c4_vol_ratio_lookup(df_btc_m15)
        return cls(close_ms_1h, vol_ratio_20, min_vol_ratio_20=min_vol_ratio_20)

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        sig_ms = int(sig.signal_close_ms)
        idx1 = int(np.searchsorted(self.close_ms_1h, sig_ms, side="right")) - 1
        if idx1 < 0 or np.isnan(self.vol_ratio_20[idx1]):
            return FilterDecision(action="DROP", reason="BLOCKED_BTC_C4_WARMUP_1H", label=self.name)
        if float(self.vol_ratio_20[idx1]) < self.min_vol_ratio_20:
            return FilterDecision(action="DROP", reason="BLOCKED_BTC_C4_LOW_VOL_RATIO_20", label=self.name)
        return FilterDecision(action="KEEP", reason="PASS", label=self.name)
