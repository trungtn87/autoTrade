"""Pure candle-only filter for ETH-USDT/C3 B2_NOT_OVEREXTENDED_4H (V3_LAB_9@9.0.0)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.base import FilterContext, FilterDecision
from autotrade_v3.strategy_core.indicators.indicators import ema
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv

C3_SYMBOL = "ETH-USDT"
C3_COMBO_ID = 3
C3_MAX_EMA_TREND_4H_DIR = 3.0
H4_MS = 14_400_000


def build_c3_4h_trend_lookup(df_eth_m15: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute closed-bar 4H ema_trend_4h lookup arrays from M15 OHLCV (zero look-ahead)."""
    d4 = resample_ohlcv(ensure_dt(df_eth_m15), "4h")
    if d4.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    c4 = d4["close"].astype(float)
    e20 = ema(c4, 20)
    e50 = ema(c4, 50)
    ema_trend_4h = ((e20 - e50) / c4 * 100.0).to_numpy(dtype=float)
    close_ms_4h = (datetime_index_to_ms(d4.index) + H4_MS - 1).astype(np.int64)
    return close_ms_4h, ema_trend_4h


class C3B2NotOverextended4hFilter:
    """Block ETH-USDT/C3 entries when directional 4H EMA(20/50) spread exceeds 3.0%."""

    name: str = "C3_B2_NOT_OVEREXTENDED_4H"

    def __init__(
        self,
        close_ms_4h: np.ndarray,
        ema_trend_4h: np.ndarray,
        max_ema_trend_4h_dir: float = C3_MAX_EMA_TREND_4H_DIR,
    ) -> None:
        self.close_ms_4h = close_ms_4h
        self.ema_trend_4h = ema_trend_4h
        self.max_ema_trend_4h_dir = float(max_ema_trend_4h_dir)

    @classmethod
    def from_m15_dataframe(
        cls,
        df_eth_m15: pd.DataFrame,
        max_ema_trend_4h_dir: float = C3_MAX_EMA_TREND_4H_DIR,
    ) -> C3B2NotOverextended4hFilter:
        close_ms_4h, ema_trend_4h = build_c3_4h_trend_lookup(df_eth_m15)
        return cls(close_ms_4h, ema_trend_4h, max_ema_trend_4h_dir=max_ema_trend_4h_dir)

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        if sig.side not in ("BUY", "SELL"):
            return FilterDecision(action="DROP", reason="BLOCKED_C3_UNKNOWN_SIDE", label=self.name)

        sig_ms = int(sig.signal_close_ms)
        idx4 = int(np.searchsorted(self.close_ms_4h, sig_ms, side="right")) - 1
        if idx4 < 0 or np.isnan(self.ema_trend_4h[idx4]):
            return FilterDecision(action="DROP", reason="BLOCKED_C3_WARMUP_4H", label=self.name)

        sgn = 1.0 if sig.side == "BUY" else -1.0
        ema_trend_4h_dir = float(self.ema_trend_4h[idx4]) * sgn
        if ema_trend_4h_dir > self.max_ema_trend_4h_dir:
            return FilterDecision(action="DROP", reason="BLOCKED_C3_OVEREXTENDED_4H", label=self.name)
        return FilterDecision(action="KEEP", reason="PASS", label=self.name)
