"""Pure candle-only filter for BTC-USDT/C7 B1_NO_CHASE_1H (V3_LAB_10)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.base import FilterContext, FilterDecision
from autotrade_v3.strategy_core.indicators.indicators import ema
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv

BTC_C7_SYMBOL = "BTC-USDT"
BTC_C7_COMBO_ID = 7
BTC_C7_MAX_EMA_TREND_1H_DIR = 0.75
H1_MS = 3_600_000


def build_btc_c7_ema_trend_lookup(df_btc_m15: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute closed-bar 1H ema_trend_1h = (EMA20 - EMA50) / close * 100 lookup arrays."""
    d1 = resample_ohlcv(ensure_dt(df_btc_m15), "1h")
    if d1.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    c = d1["close"].astype(float)
    ema_trend_1h = ((ema(c, 20) - ema(c, 50)) / c * 100.0).to_numpy(dtype=float)
    close_ms_1h = (datetime_index_to_ms(d1.index) + H1_MS - 1).astype(np.int64)
    return close_ms_1h, ema_trend_1h


class BtcC7B1NoChase1HFilter:
    """Block BTC-USDT/C7 entries when directional 1H EMA20/50 extension exceeds 0.75%."""

    name: str = "BTC_C7_B1_NO_CHASE_1H"

    def __init__(
        self,
        close_ms_1h: np.ndarray,
        ema_trend_1h: np.ndarray,
        max_ema_trend_1h_dir: float = BTC_C7_MAX_EMA_TREND_1H_DIR,
    ) -> None:
        self.close_ms_1h = close_ms_1h
        self.ema_trend_1h = ema_trend_1h
        self.max_ema_trend_1h_dir = float(max_ema_trend_1h_dir)

    @classmethod
    def from_m15_dataframe(
        cls,
        df_btc_m15: pd.DataFrame,
        max_ema_trend_1h_dir: float = BTC_C7_MAX_EMA_TREND_1H_DIR,
    ) -> BtcC7B1NoChase1HFilter:
        close_ms_1h, ema_trend_1h = build_btc_c7_ema_trend_lookup(df_btc_m15)
        return cls(close_ms_1h, ema_trend_1h, max_ema_trend_1h_dir=max_ema_trend_1h_dir)

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        sig_ms = int(sig.signal_close_ms)
        idx1 = int(np.searchsorted(self.close_ms_1h, sig_ms, side="right")) - 1
        if idx1 < 0 or np.isnan(self.ema_trend_1h[idx1]):
            return FilterDecision(action="DROP", reason="BLOCKED_BTC_C7_WARMUP_1H", label=self.name)
        sgn = 1.0 if str(sig.side).upper() == "BUY" else -1.0
        ema_trend_1h_dir = float(self.ema_trend_1h[idx1]) * sgn
        if ema_trend_1h_dir > self.max_ema_trend_1h_dir:
            return FilterDecision(action="DROP", reason="BLOCKED_BTC_C7_OVEREXTENDED_1H", label=self.name)
        return FilterDecision(action="KEEP", reason="PASS", label=self.name)
