"""Pure candle-only V5_SELL_ONLY_ATR140 filter for ETH-USDT/C15 (combo_id=105).

Rules (100% closed 1H OHLCV candles derived from M15 bars):
- Scope: exclusively ETH-USDT / combo_id=105; all other combos pass through unchanged.
- BUY: DISABLED (always dropped with reason ``BLOCKED_V5_BUY_DISABLED``).
- SELL: admitted iff ``ATR(14)_1H / SMA(ATR(14)_1H, 50) <= 1.40`` on the closed 1H bar
  matching ``signal.signal_close_ms`` (fails closed on missing or non-finite values).

ZERO dependencies on trades, positions, TP/SL history, simulator, database, or L3/L4.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.l2_lab8.filters.base import FilterContext, FilterDecision, hash_filter_config
from autotrade_v3.l2_lab8.strategy_core.indicators.indicators import atr
from autotrade_v3.l2_lab8.strategy_core.indicators.signals import ensure_dt, resample_ohlcv

C15_SYMBOL = "ETH-USDT"
C15_COMBO_ID = 105
C15_V5_VARIANT_NAME = "V5_SELL_ONLY_ATR140"
C15_V5_ATR_PERIOD = 14
C15_V5_ATR_SMA_PERIOD = 50
C15_V5_ATR_RATIO_MAX = 1.40
H1_MS = 3_600_000


def _index_to_utc_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    """Convert DatetimeIndex to int64 UTC epoch milliseconds."""
    idx_utc = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    idx_ms = idx_utc.as_unit("ms")
    return np.asarray(idx_ms.asi8, dtype=np.int64)


def build_c15_1h_atr_ratio_lookup(
    df_1h: pd.DataFrame,
    *,
    atr_period: int = C15_V5_ATR_PERIOD,
    sma_period: int = C15_V5_ATR_SMA_PERIOD,
) -> dict[int, float]:
    """Build strictly causal ``{close_ms: ATR(14)_1H / SMA(ATR(14)_1H, 50)}`` from closed 1H bars."""
    if df_1h.empty:
        return {}
    d = df_1h.sort_index()
    atr_14 = atr(d, int(atr_period)).to_numpy(dtype=float)
    atr_sma = (
        pd.Series(atr_14)
        .rolling(int(sma_period), min_periods=int(sma_period))
        .mean()
        .to_numpy(dtype=float)
    )
    atr_ratio = np.where(
        np.isfinite(atr_sma) & (atr_sma > 0.0),
        atr_14 / atr_sma,
        np.nan,
    )
    open_ms = _index_to_utc_ms(pd.DatetimeIndex(d.index))
    close_ms = open_ms + H1_MS - 1
    return {int(cms): float(val) for cms, val in zip(close_ms, atr_ratio)}


class C15V5SellAtr140Filter:
    """Locked ETH-USDT/C15 V5_SELL_ONLY_ATR140 candle-only L2 filter."""

    name: str = "ETH-USDT_C15_V5_SELL_ONLY_ATR140"

    def __init__(
        self,
        atr_ratio_by_close_ms: dict[int, float],
        *,
        atr_period: int = C15_V5_ATR_PERIOD,
        sma_period: int = C15_V5_ATR_SMA_PERIOD,
        atr_ratio_max: float = C15_V5_ATR_RATIO_MAX,
    ) -> None:
        self._atr_ratio_by_close_ms = dict(atr_ratio_by_close_ms)
        self.atr_period: int = int(atr_period)
        self.sma_period: int = int(sma_period)
        self.atr_ratio_max: float = float(atr_ratio_max)

    @classmethod
    def from_1h_dataframe(cls, df_1h: pd.DataFrame) -> "C15V5SellAtr140Filter":
        """Construct filter directly from closed 1H OHLCV bars."""
        lookup = build_c15_1h_atr_ratio_lookup(
            df_1h,
            atr_period=C15_V5_ATR_PERIOD,
            sma_period=C15_V5_ATR_SMA_PERIOD,
        )
        return cls(lookup)

    @classmethod
    def from_m15_dataframe(cls, df_eth_m15: pd.DataFrame | None) -> "C15V5SellAtr140Filter":
        """Construct filter from closed M15 ETH-USDT OHLCV bars resampled strictly to 1H."""
        if df_eth_m15 is None or df_eth_m15.empty:
            return cls({})
        d1 = resample_ohlcv(ensure_dt(df_eth_m15), "1h")
        if d1.empty:
            return cls({})
        return cls.from_1h_dataframe(d1)

    def config_dict(self) -> dict[str, Any]:
        return {
            "filter": self.name,
            "variant": C15_V5_VARIANT_NAME,
            "symbol": C15_SYMBOL,
            "combo_id": C15_COMBO_ID,
            "timeframe": "1h",
            "buy_enabled": False,
            "sell_atr_period": self.atr_period,
            "sell_atr_sma_period": self.sma_period,
            "sell_atr_ratio_max_inclusive": self.atr_ratio_max,
            "candle_only": True,
            "locked": True,
        }

    def config_hash(self) -> str:
        return hash_filter_config(self.config_dict())

    def evaluate(
        self,
        signal: Any,
        ctx: FilterContext | None = None,
    ) -> FilterDecision:
        """Evaluate V5_SELL_ONLY_ATR140 admission strictly from closed 1H candle state."""
        del ctx
        if str(getattr(signal, "symbol", "")) != C15_SYMBOL or int(getattr(signal, "combo_id", -1)) != C15_COMBO_ID:
            return FilterDecision(action="KEEP")

        side = str(getattr(signal, "side", "")).upper()
        if side == "BUY":
            return FilterDecision(action="DROP", reason="BLOCKED_V5_BUY_DISABLED", label="V5_DROP")

        if side != "SELL":
            return FilterDecision(action="DROP", reason="BLOCKED_V5_UNKNOWN_SIDE", label="V5_DROP")

        cms = int(getattr(signal, "signal_close_ms"))
        ratio = self._atr_ratio_by_close_ms.get(cms)
        if ratio is None:
            return FilterDecision(action="DROP", reason="BLOCKED_V5_MISSING_1H_BAR", label="V5_DROP")
        if not math.isfinite(ratio):
            return FilterDecision(action="DROP", reason="BLOCKED_V5_NONFINITE_ATR_RATIO", label="V5_DROP")
        if ratio <= self.atr_ratio_max:
            return FilterDecision(action="KEEP", label="V5_KEEP")
        return FilterDecision(action="DROP", reason="BLOCKED_V5_SELL_ATR_RATIO_GT_140", label="V5_DROP")
