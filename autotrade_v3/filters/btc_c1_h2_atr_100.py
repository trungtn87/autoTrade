"""Pure candle-only filter for BTC-USDT/C1 H2_ATR_1.00 (V3_LAB_11@11.0.0)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.base import FilterContext, FilterDecision
from autotrade_v3.strategy_core.indicators.indicators import atr
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv

BTC_C1_SYMBOL = "BTC-USDT"
BTC_C1_COMBO_ID = 1
BTC_C1_MIN_ATR_RATIO_50 = 1.00
H1_MS = 3_600_000


def build_btc_c1_atr_ratio_lookup(df_btc_m15: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute closed-bar 1H atr_ratio_50 = ATR(14) / SMA(ATR(14), 50) lookup arrays."""
    if df_btc_m15 is None or df_btc_m15.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    d1 = resample_ohlcv(ensure_dt(df_btc_m15), "1h")
    if d1.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    a14 = atr(d1, 14)
    sma50 = a14.rolling(50).mean().replace(0.0, np.nan)
    atr_ratio_50 = (a14 / sma50).to_numpy(dtype=float)
    close_ms_1h = (datetime_index_to_ms(d1.index) + H1_MS - 1).astype(np.int64)
    return close_ms_1h, atr_ratio_50


class BtcC1H2Atr100Filter:
    """Require closed 1H atr_ratio_50 >= 1.00 for BTC-USDT/C1 entries (VOL_EXPANSION)."""

    name: str = "BTC_C1_H2_ATR_1_00"

    def __init__(
        self,
        close_ms_1h: np.ndarray,
        atr_ratio_50: np.ndarray,
        min_atr_ratio_50: float = BTC_C1_MIN_ATR_RATIO_50,
    ) -> None:
        self.close_ms_1h = np.asarray(close_ms_1h, dtype=np.int64)
        self.atr_ratio_50 = np.asarray(atr_ratio_50, dtype=float)
        self.min_atr_ratio_50 = float(min_atr_ratio_50)

    @classmethod
    def from_m15_dataframe(
        cls,
        df_btc_m15: pd.DataFrame | None,
        min_atr_ratio_50: float = BTC_C1_MIN_ATR_RATIO_50,
    ) -> BtcC1H2Atr100Filter:
        if df_btc_m15 is None or df_btc_m15.empty:
            return cls(np.array([], dtype=np.int64), np.array([], dtype=float), min_atr_ratio_50=min_atr_ratio_50)
        close_ms_1h, atr_ratio_50 = build_btc_c1_atr_ratio_lookup(df_btc_m15)
        return cls(close_ms_1h, atr_ratio_50, min_atr_ratio_50=min_atr_ratio_50)

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        if len(self.close_ms_1h) == 0:
            return FilterDecision(action="DROP", reason="BLOCKED_BTC_C1_NO_1H_DATA", label=self.name)
        sig_ms = int(sig.signal_close_ms)
        idx1 = int(np.searchsorted(self.close_ms_1h, sig_ms, side="right")) - 1
        if idx1 < 0 or not np.isfinite(self.atr_ratio_50[idx1]):
            return FilterDecision(action="DROP", reason="BLOCKED_BTC_C1_WARMUP_1H", label=self.name)
        if float(self.atr_ratio_50[idx1]) < self.min_atr_ratio_50:
            return FilterDecision(action="DROP", reason="BLOCKED_BTC_C1_LOW_ATR_RATIO_50", label=self.name)
        return FilterDecision(action="KEEP", reason="PASS", label=self.name)
