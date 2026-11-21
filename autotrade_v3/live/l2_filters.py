"""Lab 10 live signal filters: ETH C3, C6, C7, C12-C15 and their lookup helpers.

Moved verbatim from l2_lab10.py (no logic change). Filters are causal checks on closed bars.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd

from autotrade_v3.l2_core import (  # shared helpers with identical bodies
    _ichimoku, atr, cci, crossunder, edge_event, ema, ensure_dt, resample_ohlcv, rsi, sma, stochastic,
)

if TYPE_CHECKING:  # annotation only, avoids a runtime import cycle with l2_lab10
    from autotrade_v3.live.l2_lab10 import Signal


# --- from autotrade_v3/engine/candle_loader.py
def datetime_index_to_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    """Convert any DatetimeIndex to int64 UTC milliseconds, invariant to unit (s/ms/us/ns) and timezone."""
    if idx.tz is None:
        idx_utc = idx.tz_localize("UTC")
    else:
        idx_utc = idx.tz_convert("UTC")
    return np.asarray(idx_utc.astype("datetime64[ms, UTC]").view("int64"))

H1_MS = 3600000

H4_MS = 14400000

# --- from autotrade_v3/external/annotations.py
@dataclass(frozen=True)
class AnnotationRecord:
    source: str
    symbol: str
    as_of_ms: int
    payload: dict[str, Any]
    payload_hash: str
    as_of_unverified: bool

# --- from autotrade_v3/external/annotations.py
@dataclass(frozen=True)
class AnnotationQueryResult:
    records: list[AnnotationRecord]
    dropped_future_count: int
    has_unverified: bool

# --- from autotrade_v3/filters/base.py
@dataclass(frozen=True)
class FilterDecision:
    action: Literal["KEEP", "DROP"]
    reason: str = ""
    label: str = ""

# --- from autotrade_v3/filters/base.py
class FilterContext:
    """Causal context passed to filters.

    Filters have NO direct DB/filesystem access to annotations; they can ONLY
    call :meth:`get_annotations`, which strictly enforces ``as_of_ms <= signal.signal_close_ms``.
    """

    def __init__(self, annotation_store: Any = None) -> None:
        self._annotation_store = annotation_store
        self.dropped_future_annotations: int = 0
        self.used_unverified_annotations: bool = False

C12_SYMBOL = 'ETH-USDT'

C12_COMBO_ID = 102

C12_V3_ATR_PERIOD = 14

C12_V3_RET_PERIOD = 24

C12_V3_ATR_PCT_MIN = 0.65

C12_V3_RET_24H_MAX = 2.2

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
def _index_to_utc_ms__c12_v3_atr065_no_ext24h(idx: pd.DatetimeIndex) -> np.ndarray:
    idx_utc = idx.tz_localize('UTC') if idx.tz is None else idx.tz_convert('UTC')
    idx_ms = idx_utc.as_unit('ms')
    return np.asarray(idx_ms.asi8, dtype=np.int64)

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
def build_c12_1h_features_lookup(df_1h: pd.DataFrame, *, atr_period: int=C12_V3_ATR_PERIOD, ret_period: int=C12_V3_RET_PERIOD) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build strictly causal sorted arrays (close_ms, atr_pct_1h, ret_24h_1h) from closed 1H bars."""
    if df_1h.empty:
        return (np.array([], dtype=np.int64), np.array([], dtype=float), np.array([], dtype=float))
    d = df_1h.sort_index()
    h = d['high'].astype(float)
    l = d['low'].astype(float)
    c = d['close'].astype(float)
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr_sma = tr.rolling(int(atr_period)).mean()
    atr_pct = (atr_sma / c * 100.0).to_numpy(dtype=float)
    ret_24h = (c.pct_change(int(ret_period)) * 100.0).to_numpy(dtype=float)
    open_ms = _index_to_utc_ms__c12_v3_atr065_no_ext24h(pd.DatetimeIndex(d.index))
    close_ms = open_ms + H1_MS - 1
    return (close_ms, atr_pct, ret_24h)

# --- from autotrade_v3/filters/c12_v3_atr065_no_ext24h.py
class C12V3Atr065NoExt24hFilter:
    """Locked ETH-USDT/C12 V3_ATR065_NO_EXT24H candle-only L2 filter."""
    name: str = 'ETH-USDT_C12_V3_ATR065_NO_EXT24H'

    def __init__(self, close_ms: np.ndarray, atr_pct: np.ndarray, ret_24h: np.ndarray, *, atr_pct_min: float=C12_V3_ATR_PCT_MIN, ret_24h_max: float=C12_V3_RET_24H_MAX) -> None:
        self._close_ms = close_ms
        self._atr_pct = atr_pct
        self._ret_24h = ret_24h
        self.atr_pct_min = float(atr_pct_min)
        self.ret_24h_max = float(ret_24h_max)

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        ok, reason = self.evaluate_signal(signal)
        return FilterDecision(action='KEEP' if ok else 'DROP', reason=reason, label=self.name)

    def evaluate_signal(self, sig) -> tuple[bool, str]:
        if sig.symbol != C12_SYMBOL or int(sig.combo_id) != C12_COMBO_ID:
            return (True, 'PASS_NON_C12')
        if len(self._close_ms) == 0:
            return (False, 'BLOCKED_V3_MISSING_1H')
        idx = int(np.searchsorted(self._close_ms, int(sig.signal_close_ms), side='right')) - 1
        if idx < 0:
            return (False, 'BLOCKED_V3_WARMUP_1H')
        atr_val = float(self._atr_pct[idx])
        ret_val = float(self._ret_24h[idx])
        if not (math.isfinite(atr_val) and math.isfinite(ret_val)):
            return (False, 'BLOCKED_V3_NONFINITE')
        if atr_val < self.atr_pct_min:
            return (False, 'BLOCKED_V3_LOW_ATR_1H')
        if ret_val > self.ret_24h_max:
            return (False, 'BLOCKED_V3_EXTENDED_24H')
        return (True, 'ALLOW_V3_ATR065_NO_EXT24H')

C13_SYMBOL = "ETH-USDT"

C13_COMBO_ID = 103

C13_MAX_ATR_RATIO_50 = 1.50

# --- from autotrade_v3/filters/c13_b1_vol_regime.py
def build_c13_atr_ratio_lookup(df_eth_m15: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute closed-bar 1H atr_ratio_50 = ATR(14) / SMA(ATR(14), 50) lookup arrays."""
    d1 = resample_ohlcv(ensure_dt(df_eth_m15), "1h")
    if d1.empty:
        return np.array([], dtype=np.int64), np.array([], dtype=float)
    a14 = atr(d1, 14)
    atr_ratio_50 = (a14 / a14.rolling(50).mean()).to_numpy(dtype=float)
    close_ms_1h = (datetime_index_to_ms(d1.index) + H1_MS - 1).astype(np.int64)
    return close_ms_1h, atr_ratio_50

# --- from autotrade_v3/filters/c13_b1_vol_regime.py
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

C14_SYMBOL = "BTC-USDT"

C14_COMBO_ID = 104

H3A_MIN_KUMO_PCT_INCLUSIVE = 0.005

H3A_MAX_KUMO_PCT_EXCLUSIVE = 0.010

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py
def build_c14_1h_kumo_lookup(df_1h: pd.DataFrame) -> dict[int, float]:
    """Build strictly causal ``{close_ms: abs(span_a - span_b) / close}`` from closed 1H bars."""
    if df_1h.empty:
        return {}
    d = df_1h.sort_index()
    h = d["high"].to_numpy(float)
    l = d["low"].to_numpy(float)
    c = d["close"].to_numpy(float)
    _, _, span_a, span_b = _ichimoku(h, l, conv=12, base=30, spanb=60)
    kumo_pct = np.where(c > 0.0, np.abs(span_a - span_b) / c, np.nan)

    idx_ns = d.index.tz_convert("UTC").asi8 if d.index.tz is not None else d.index.tz_localize("UTC").asi8
    close_ms = (idx_ns // 1_000_000) + 3_600_000 - 1
    return {int(cms): float(kp) for cms, kp in zip(close_ms, kumo_pct)}

# --- from autotrade_v3/filters/c14_h3a_kumo_band.py
class C14H3aKumoBandFilter:
    """Locked H3a V1 filter: admit BTC-USDT/C14 iff 0.005 <= abs(SpanA - SpanB)/Close < 0.010."""

    name: str = "BTC-USDT_C14_H3a_KUMO_BAND_V1"

    def __init__(self, kumo_by_close_ms: dict[int, float]) -> None:
        self._kumo_by_close_ms = dict(kumo_by_close_ms)
        # Locked parameters per BTC_C14_H3A_STANDARD_CANDIDATE.json
        self.min_inclusive: float = H3A_MIN_KUMO_PCT_INCLUSIVE
        self.max_exclusive: float = H3A_MAX_KUMO_PCT_EXCLUSIVE

    @classmethod
    def from_1h_dataframe(cls, df_1h: pd.DataFrame) -> "C14H3aKumoBandFilter":
        return cls(build_c14_1h_kumo_lookup(df_1h))



    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        if str(getattr(signal, "symbol", "")) != C14_SYMBOL or int(getattr(signal, "combo_id", -1)) != C14_COMBO_ID:
            return FilterDecision(action="KEEP")

        cms = int(getattr(signal, "signal_close_ms"))
        kumo_pct = self._kumo_by_close_ms.get(cms)
        if kumo_pct is None:
            return FilterDecision(action="DROP", reason="H3a_MISSING_1H_BAR", label="H3a_DROP")
        if not math.isfinite(kumo_pct):
            return FilterDecision(action="DROP", reason="H3a_NONFINITE_INDICATOR", label="H3a_DROP")

        if self.min_inclusive <= kumo_pct < self.max_exclusive:
            return FilterDecision(action="KEEP", label="H3a_KEEP")
        return FilterDecision(action="DROP", reason="H3a_OUTSIDE_KUMO_BAND", label="H3a_DROP")

C15_SYMBOL = 'ETH-USDT'

C15_COMBO_ID = 105

C15_V5_VARIANT_NAME = 'V5_SELL_ONLY_ATR140'

C15_V5_ATR_PERIOD = 14

C15_V5_ATR_SMA_PERIOD = 50

C15_V5_ATR_RATIO_MAX = 1.4

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
def _index_to_utc_ms__c15_v5_sell_atr140(idx: pd.DatetimeIndex) -> np.ndarray:
    """Convert DatetimeIndex to int64 UTC epoch milliseconds."""
    idx_utc = idx.tz_localize('UTC') if idx.tz is None else idx.tz_convert('UTC')
    idx_ms = idx_utc.as_unit('ms')
    return np.asarray(idx_ms.asi8, dtype=np.int64)

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
def build_c15_1h_atr_ratio_lookup(df_1h: pd.DataFrame, *, atr_period: int=C15_V5_ATR_PERIOD, sma_period: int=C15_V5_ATR_SMA_PERIOD) -> dict[int, float]:
    """Build strictly causal ``{close_ms: ATR(14)_1H / SMA(ATR(14)_1H, 50)}`` from closed 1H bars."""
    if df_1h.empty:
        return {}
    d = df_1h.sort_index()
    atr_14 = atr(d, int(atr_period)).to_numpy(dtype=float)
    atr_sma = pd.Series(atr_14).rolling(int(sma_period), min_periods=int(sma_period)).mean().to_numpy(dtype=float)
    atr_ratio = np.where(np.isfinite(atr_sma) & (atr_sma > 0.0), atr_14 / atr_sma, np.nan)
    open_ms = _index_to_utc_ms__c15_v5_sell_atr140(pd.DatetimeIndex(d.index))
    close_ms = open_ms + H1_MS - 1
    return {int(cms): float(val) for cms, val in zip(close_ms, atr_ratio)}

# --- from autotrade_v3/filters/c15_v5_sell_atr140.py
class C15V5SellAtr140Filter:
    """Locked ETH-USDT/C15 V5_SELL_ONLY_ATR140 candle-only L2 filter."""
    name: str = 'ETH-USDT_C15_V5_SELL_ONLY_ATR140'

    def __init__(self, atr_ratio_by_close_ms: dict[int, float], *, atr_period: int=C15_V5_ATR_PERIOD, sma_period: int=C15_V5_ATR_SMA_PERIOD, atr_ratio_max: float=C15_V5_ATR_RATIO_MAX) -> None:
        self._atr_ratio_by_close_ms = dict(atr_ratio_by_close_ms)
        self.atr_period: int = int(atr_period)
        self.sma_period: int = int(sma_period)
        self.atr_ratio_max: float = float(atr_ratio_max)

    @classmethod
    def from_1h_dataframe(cls, df_1h: pd.DataFrame) -> 'C15V5SellAtr140Filter':
        """Construct filter directly from closed 1H OHLCV bars."""
        lookup = build_c15_1h_atr_ratio_lookup(df_1h, atr_period=C15_V5_ATR_PERIOD, sma_period=C15_V5_ATR_SMA_PERIOD)
        return cls(lookup)

    @classmethod
    def from_m15_dataframe(cls, df_eth_m15: pd.DataFrame | None) -> 'C15V5SellAtr140Filter':
        """Construct filter from closed M15 ETH-USDT OHLCV bars resampled strictly to 1H."""
        if df_eth_m15 is None or df_eth_m15.empty:
            return cls({})
        d1 = resample_ohlcv(ensure_dt(df_eth_m15), '1h')
        if d1.empty:
            return cls({})
        return cls.from_1h_dataframe(d1)



    def evaluate(self, signal: Any, ctx: FilterContext | None=None) -> FilterDecision:
        """Evaluate V5_SELL_ONLY_ATR140 admission strictly from closed 1H candle state."""
        del ctx
        if str(getattr(signal, 'symbol', '')) != C15_SYMBOL or int(getattr(signal, 'combo_id', -1)) != C15_COMBO_ID:
            return FilterDecision(action='KEEP')
        side = str(getattr(signal, 'side', '')).upper()
        if side == 'BUY':
            return FilterDecision(action='DROP', reason='BLOCKED_V5_BUY_DISABLED', label='V5_DROP')
        if side != 'SELL':
            return FilterDecision(action='DROP', reason='BLOCKED_V5_UNKNOWN_SIDE', label='V5_DROP')
        cms = int(getattr(signal, 'signal_close_ms'))
        ratio = self._atr_ratio_by_close_ms.get(cms)
        if ratio is None:
            return FilterDecision(action='DROP', reason='BLOCKED_V5_MISSING_1H_BAR', label='V5_DROP')
        if not math.isfinite(ratio):
            return FilterDecision(action='DROP', reason='BLOCKED_V5_NONFINITE_ATR_RATIO', label='V5_DROP')
        if ratio <= self.atr_ratio_max:
            return FilterDecision(action='KEEP', label='V5_KEEP')
        return FilterDecision(action='DROP', reason='BLOCKED_V5_SELL_ATR_RATIO_GT_140', label='V5_DROP')

C3_SYMBOL = "ETH-USDT"

C3_COMBO_ID = 3

C3_MAX_EMA_TREND_4H_DIR = 3.0

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py
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

# --- from autotrade_v3/filters/c3_b2_not_overextended_4h.py
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

C6_SYMBOL = "ETH-USDT"

C6_COMBO_ID = 6

C6_BUY_ATR_RATIO_50_MIN = 1.2

C6_SELL_ATR_RATIO_50_MIN = 0.9

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
def calc_adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder-smoothed ADX(n) on closed candles."""
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr_v = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    plus_di = 100.0 * pd.Series(plus_dm, index=high.index).ewm(alpha=1.0 / n, adjust=False).mean() / atr_v.replace(0, np.nan)
    minus_di = 100.0 * pd.Series(minus_dm, index=high.index).ewm(alpha=1.0 / n, adjust=False).mean() / atr_v.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / n, adjust=False).mean()

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
def build_c6_atr_ratio_lookup(df_1h: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Compute closed-bar 1H ATR14 / SMA50(ATR14) lookup arrays."""
    h1 = df_1h["high"].astype(float)
    l1 = df_1h["low"].astype(float)
    c1 = df_1h["close"].astype(float)
    pc1 = c1.shift(1)
    tr1 = pd.concat([(h1 - l1), (h1 - pc1).abs(), (l1 - pc1).abs()], axis=1).max(axis=1)
    atr14_1h = tr1.rolling(14).mean()
    atr_sma50_1h = atr14_1h.rolling(50).mean()
    atr_ratio_50 = (atr14_1h / atr_sma50_1h.replace(0, np.nan)).to_numpy(dtype=float)
    close_ms_1h = (datetime_index_to_ms(df_1h.index) + 3_600_000 - 1).astype(np.int64)
    return close_ms_1h, atr_ratio_50

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
class C6R2V4CombinedFilter:
    """Asymmetric candle-only ATR-ratio filter for ETH-USDT/C6: BUY >= 1.2, SELL >= 0.9."""

    name: str = "C6_R2_V4_COMBINED"

    def __init__(self, close_ms_1h: np.ndarray, atr_ratio_50: np.ndarray) -> None:
        self.close_ms_1h = close_ms_1h
        self.atr_ratio_50 = atr_ratio_50

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        return self.evaluate_signal(signal)

    def evaluate_signal(self, sig: Signal) -> FilterDecision:
        sig_ms = int(sig.signal_close_ms)
        idx = int(np.searchsorted(self.close_ms_1h, sig_ms, side="right")) - 1
        if idx < 0 or np.isnan(self.atr_ratio_50[idx]):
            return FilterDecision(action="DROP", reason="BLOCKED_C6_WARMUP_1H", label=self.name)

        val = float(self.atr_ratio_50[idx])
        if sig.side == "BUY":
            if val < C6_BUY_ATR_RATIO_50_MIN:
                return FilterDecision(action="DROP", reason="BLOCKED_C6_BUY_ATR_RATIO_LT_120", label=self.name)
            return FilterDecision(action="KEEP", reason="PASS", label=self.name)

        if sig.side == "SELL":
            if val < C6_SELL_ATR_RATIO_50_MIN:
                return FilterDecision(action="DROP", reason="BLOCKED_C6_SELL_ATR_RATIO_LT_090", label=self.name)
            return FilterDecision(action="KEEP", reason="PASS", label=self.name)

        return FilterDecision(action="DROP", reason="BLOCKED_C6_UNKNOWN_SIDE", label=self.name)

# --- from autotrade_v3/filters/c6_r2_v4_combined.py
def generate_c6_cci_audit_sell_signals(
    df_15m: pd.DataFrame,
    *,
    combo_name: str,
    tp_pct: float,
    sl_pct: float,
    strategy_id: str,
    exit_policy: Any = None,
    exit_policy_hash: str = "",
    warmup_end_ms: int | None = None,
) -> list[Signal]:
    """Generate corrected CCI-Audit SELL signals for ETH-USDT/C6 on closed 1H candles."""
    # Import at call time: l2_lab10 imports l2_filters during module initialization.
    from autotrade_v3.live.l2_lab10 import Signal

    d1h = ensure_dt(resample_ohlcv(df_15m, "1h"))
    if d1h.empty:
        return []

    o, h, l, c = d1h["open"], d1h["high"], d1h["low"], d1h["close"]
    R = rsi(c, 14)
    K = stochastic(c, h, l, 14)
    D = K.rolling(3).mean()
    C = cci(c, h, l, 9)
    adx_val = calc_adx(h, l, c, 14)
    adxs = (adx_val > 20) & (adx_val > adx_val.shift(1))
    a21 = atr(d1h, 21)
    std20 = c.rolling(20, min_periods=20).std(ddof=0)
    bbl = sma(c, 20) - 2 * std20
    kl = sma(c, 20) - 1.7 * atr(d1h, 20)
    breakout_s = crossunder(c, bbl) & (c < kl)
    ss = (c < o) & ((o - c) > a21 * 1.8)
    sc6 = (R < 70) & (K < 70) & (K < D) & (C < -100) & (C < C.shift(1))
    edge_sell = edge_event(ss & breakout_s & sc6 & adxs)

    idx_ms = datetime_index_to_ms(d1h.index) + 3_600_000 - 1
    out: list[Signal] = []
    for i, flag in enumerate(edge_sell.values):
        if not flag:
            continue
        ts = int(idx_ms[i])
        if warmup_end_ms is not None and ts < warmup_end_ms:
            continue
        ep = float(c.iloc[i])
        out.append(
            Signal(
                symbol=C6_SYMBOL,
                combo_id=C6_COMBO_ID,
                combo_name=combo_name,
                timeframe="1h",
                side="SELL",
                signal_close_ms=ts,
                signal_close_dt=pd.Timestamp(ts, unit="ms", tz="UTC"),
                bar_position=i,
                bar_open_ms=ts - 3_600_000 + 1,
                entry_price=ep,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                tp_price=ep * (1.0 - tp_pct),
                sl_price=ep * (1.0 + sl_pct),
                approved=True,
                warmup_ready=True,
                smc_dir=0,
                ob_blocked=False,
                provider=strategy_id,
                is_new6=False,
                exit_policy=exit_policy,
                exit_policy_hash=exit_policy_hash,
                signal_id=f"ETH-USDT_C6_SELL_CCI_{ts}",
            )
        )
    return out

C7_COMBO_ID = 7

C7_BUY_VOL_MULT_MIN = 2.0

C7_SELL_EMA_TREND_1H_MAX = 0.0

# --- from autotrade_v3/filters/c7_r2_v2_asym.py
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

# --- from autotrade_v3/filters/c7_r2_v2_asym.py
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
