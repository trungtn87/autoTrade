"""Pure candle-only filter for ETH-USDT/C13 B1_VOL_REGIME (V3_LAB_10@10.0.0)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.base import FilterContext, FilterDecision
from autotrade_v3.strategy_core.indicators.indicators import atr
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv

C13_SYMBOL = "ETH-USDT"
C13_COMBO_ID = 103
C13_MAX_ATR_RATIO_50 = 1.50
H1_MS = 3_600_000


def build_c13_atr_ratio_lookup(df_eth_m15: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute closed-bar 1H atr_ratio_50 = ATR(14) / SMA(ATR(14), 50) lookup arrays."""
    d1 = resample_ohlcv(ensure_dt(df_eth_m15), "1h")
    if d1.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    a14 = atr(d1, 14)
    atr_ratio_50 = (a14 / a14.rolling(50).mean()).to_numpy(dtype=float)
    close_ms_1h = (datetime_index_to_ms(d1.index) + H1_MS - 1).astype(np.int64)
    return close_ms_1h, atr_ratio_50


class C13B1VolRegimeFilter:
    """Block ETH-USDT/C13 entries when 1H atr_ratio_50 exceeds 1.50 (protects 0.80% SL)."""

    name: str = "C13_B1_VOL_REGIME"

    def __init__(
        self,
        close_ms_1h: np.ndarray,
        atr_ratio_50: np.ndarray,
        max_atr_ratio_50: float = C13_MAX_ATR_RATIO_50,
    ) -> None:
        self.close_ms_1h = close_ms_1h
        self.atr_ratio_50 = atr_ratio_50
        self.max_atr_ratio_50 = float(max_atr_ratio_50)

    @classmethod
    def from_m15_dataframe(
        cls,
        df_eth_m15: pd.DataFrame,
        max_atr_ratio_50: float = C13_MAX_ATR_RATIO_50,
    ) -> C13B1VolRegimeFilter:
        close_ms_1h, atr_ratio_50 = build_c13_atr_ratio_lookup(df_eth_m15)
        return cls(close_ms_1h, atr_ratio_50, max_atr_ratio_50=max_atr_ratio_50)

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        sig_ms = int(sig.signal_close_ms)
        idx1 = int(np.searchsorted(self.close_ms_1h, sig_ms, side="right")) - 1
        if idx1 < 0 or np.isnan(self.atr_ratio_50[idx1]):
            return FilterDecision(action="DROP", reason="BLOCKED_C13_WARMUP_1H", label=self.name)
        if float(self.atr_ratio_50[idx1]) > self.max_atr_ratio_50:
            return FilterDecision(action="DROP", reason="BLOCKED_C13_HIGH_ATR_RATIO_50", label=self.name)
        return FilterDecision(action="KEEP", reason="PASS", label=self.name)
