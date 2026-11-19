"""Pure candle-only V3_ATR065_NO_EXT24H filter for ETH-USDT/C12 (combo_id=102).

Rules (100% closed 1H OHLCV candles derived from M15 bars):
- Scope: exclusively ETH-USDT / combo_id=102; all other combos pass through unchanged.
- Admitted iff on the latest closed 1H bar (close_ms <= signal.signal_close_ms):
  * atr_pct_1h = (SMA(TR, 14) / close_1h) * 100.0 >= 0.65
  * ret_24h_1h = (close_1h / close_1h[-24] - 1.0) * 100.0 <= 2.20
  (fails closed on missing or non-finite values).

ZERO dependencies on trades, positions, TP/SL history, simulator, database, or L3/L4.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

from autotrade_v3.filters.base import FilterContext, FilterDecision

C12_SYMBOL = "ETH-USDT"
C12_COMBO_ID = 102
C12_V3_VARIANT_NAME = "V3_ATR065_NO_EXT24H"
C12_V3_ATR_PERIOD = 14
C12_V3_RET_PERIOD = 24
C12_V3_ATR_PCT_MIN = 0.65
C12_V3_RET_24H_MAX = 2.20
H1_MS = 3_600_000


def _index_to_utc_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    idx_utc = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    idx_ms = idx_utc.as_unit("ms")
    return np.asarray(idx_ms.asi8, dtype=np.int64)


def build_c12_1h_features_lookup(
    df_1h: pd.DataFrame,
    *,
    atr_period: int = C12_V3_ATR_PERIOD,
    ret_period: int = C12_V3_RET_PERIOD,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build strictly causal sorted arrays (close_ms, atr_pct_1h, ret_24h_1h) from closed 1H bars."""
    if df_1h.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float), np.array([], dtype=float)
    d = df_1h.sort_index()
    h = d["high"].astype(float)
    l = d["low"].astype(float)
    c = d["close"].astype(float)
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr_sma = tr.rolling(int(atr_period)).mean()
    atr_pct = ((atr_sma / c) * 100.0).to_numpy(dtype=float)
    ret_24h = (c.pct_change(int(ret_period)) * 100.0).to_numpy(dtype=float)
    open_ms = _index_to_utc_ms(pd.DatetimeIndex(d.index))
    close_ms = open_ms + H1_MS - 1
    return close_ms, atr_pct, ret_24h


class C12V3Atr065NoExt24hFilter:
    """Locked ETH-USDT/C12 V3_ATR065_NO_EXT24H candle-only L2 filter."""

    name: str = "ETH-USDT_C12_V3_ATR065_NO_EXT24H"

    def __init__(
        self,
        close_ms: np.ndarray,
        atr_pct: np.ndarray,
        ret_24h: np.ndarray,
        *,
        atr_pct_min: float = C12_V3_ATR_PCT_MIN,
        ret_24h_max: float = C12_V3_RET_24H_MAX,
    ) -> None:
        self._close_ms = close_ms
        self._atr_pct = atr_pct
        self._ret_24h = ret_24h
        self.atr_pct_min = float(atr_pct_min)
        self.ret_24h_max = float(ret_24h_max)

    def evaluate(self, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(ctx.signal)  # type: ignore[attr-defined]

    def evaluate_signal(self, sig) -> tuple[bool, str]:
        if sig.symbol != C12_SYMBOL or int(sig.combo_id) != C12_COMBO_ID:
            return True, "PASS_NON_C12"

        if len(self._close_ms) == 0:
            return False, "BLOCKED_V3_MISSING_1H"

        idx = int(np.searchsorted(self._close_ms, int(sig.signal_close_ms), side="right")) - 1
        if idx < 0:
            return False, "BLOCKED_V3_WARMUP_1H"

        atr_val = float(self._atr_pct[idx])
        ret_val = float(self._ret_24h[idx])
        if not (math.isfinite(atr_val) and math.isfinite(ret_val)):
            return False, "BLOCKED_V3_NONFINITE"

        if atr_val < self.atr_pct_min:
            return False, "BLOCKED_V3_LOW_ATR_1H"
        if ret_val > self.ret_24h_max:
            return False, "BLOCKED_V3_EXTENDED_24H"

        return True, "ALLOW_V3_ATR065_NO_EXT24H"
