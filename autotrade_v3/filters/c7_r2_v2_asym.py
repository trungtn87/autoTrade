"""Pure candle-only filter for ETH-USDT/C7 R2_V2_ASYM_BUY_VOL20_SELL_TREND (V3_LAB_7@7.0.0)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.base import FilterContext, FilterDecision

C7_SYMBOL = "ETH-USDT"
C7_COMBO_ID = 7
C7_BUY_VOL_MULT_MIN = 2.0
C7_SELL_EMA_TREND_1H_MAX = 0.0


def build_c7_features_lookup(
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute closed-bar 15m vol_mult and 1h ema_trend_1h lookup arrays."""
    v15 = df_15m["volume"].astype(float)
    vol_mult_15m = (v15 / v15.rolling(20).mean().replace(0, np.nan)).to_numpy(dtype=float)
    close_ms_15m = (datetime_index_to_ms(df_15m.index) + 900_000 - 1).astype(np.int64)

    c1 = df_1h["close"].astype(float)
    ema20_1h = c1.ewm(span=20, adjust=False).mean()
    ema50_1h = c1.ewm(span=50, adjust=False).mean()
    ema_trend_1h = ((ema20_1h - ema50_1h) / c1 * 100.0).to_numpy(dtype=float)
    close_ms_1h = (datetime_index_to_ms(df_1h.index) + 3_600_000 - 1).astype(np.int64)

    return close_ms_15m, vol_mult_15m, close_ms_1h, ema_trend_1h


class C7R2V2AsymFilter:
    """Asymmetric candle-only filter for ETH-USDT/C7: BUY vol_mult>=2.0, SELL ema_trend_1h<=0.0."""

    name: str = "C7_R2_V2_ASYM_BUY_VOL20_SELL_TREND"

    def __init__(
        self,
        close_ms_15m: np.ndarray,
        vol_mult_15m: np.ndarray,
        close_ms_1h: np.ndarray,
        ema_trend_1h: np.ndarray,
    ) -> None:
        self.close_ms_15m = close_ms_15m
        self.vol_mult_15m = vol_mult_15m
        self.close_ms_1h = close_ms_1h
        self.ema_trend_1h = ema_trend_1h

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        sig_ms = int(sig.signal_close_ms)
        if sig.side == "BUY":
            idx = int(np.searchsorted(self.close_ms_15m, sig_ms, side="right")) - 1
            if idx < 0 or np.isnan(self.vol_mult_15m[idx]):
                return FilterDecision(action="DROP", reason="BLOCKED_C7_WARMUP_15M", label=self.name)
            if self.vol_mult_15m[idx] < C7_BUY_VOL_MULT_MIN:
                return FilterDecision(action="DROP", reason="BLOCKED_C7_BUY_VOL_LT_20", label=self.name)
            return FilterDecision(action="KEEP", reason="PASS", label=self.name)

        if sig.side == "SELL":
            idx = int(np.searchsorted(self.close_ms_1h, sig_ms, side="right")) - 1
            if idx < 0 or np.isnan(self.ema_trend_1h[idx]):
                return FilterDecision(action="DROP", reason="BLOCKED_C7_WARMUP_1H", label=self.name)
            if self.ema_trend_1h[idx] > C7_SELL_EMA_TREND_1H_MAX:
                return FilterDecision(action="DROP", reason="BLOCKED_C7_SELL_TREND_POS", label=self.name)
            return FilterDecision(action="KEEP", reason="PASS", label=self.name)

        return FilterDecision(action="DROP", reason="BLOCKED_C7_UNKNOWN_SIDE", label=self.name)
