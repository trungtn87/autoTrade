"""Pure candle-only filter and CCI-Audit SELL generator for ETH-USDT/C6 R2_V4_COMBINED (V3_LAB_8@8.0.0)."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.base import FilterContext, FilterDecision
from autotrade_v3.strategy_core.indicators.indicators import atr, crossunder, sma, stochastic
from autotrade_v3.strategy_core.indicators.signals import cci, edge_event, ensure_dt, resample_ohlcv, rsi

C6_SYMBOL = "ETH-USDT"
C6_COMBO_ID = 6
C6_BUY_ATR_RATIO_50_MIN = 1.2
C6_SELL_ATR_RATIO_50_MIN = 0.9


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
