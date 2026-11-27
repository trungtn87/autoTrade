"""Standalone live adapters for PLUS44 TV21 strategies, indicators, and variant filters.

Faithfully ports the 6 TV21 strategy families (``L1_S1``, ``L1_S3``, ``L1_S4``,
``L1_S5``, ``L2_S1``, ``L2_S3``), their separated Lab 1 (``pine_parity``) and
Lab 2 (``pine``) indicator primitives, and all 10 post-edge/internal variants
without importing ``research/``, ``duckdb``, or ``pyarrow`` and without
modifying the active 23-combo PRUNE23 live registry.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from .l2_resampler import (
    M15_MS,
    TimeframeCloseGate,
    _extract_open_ms,
    is_timeframe_close,
    latest_closed_htf_close_ms,
    normalize_htf,
    resample_m15_closed,
    timeframe_ms,
)
from .l3_execution import V3Signal

V6_ATR_MIN_BARS = 214  # 14 (EWM ATR) + 1 (shift) + 200 (rolling quantile) - 1
BTC = "BTC-USDT"
ETH = "ETH-USDT"
BTC_WINDOW_BARS = 13_440
ETH_WINDOW_BARS = 25_920

TV21_WRAPPERS = (
    "BASELINE",
    "V1_TP_EXTENSION",
    "V2_SL_TIGHTENING",
    "V3_BUY_ONLY",
    "V4_SELL_ONLY",
    "V5_EMA50_FILTER",
    "V6_ATR_VOL_FILTER",
)


# ---------------------------------------------------------------------------
# Lab 1 indicator primitives (research/v3_tv_strategy_lab_1/indicators/pine_parity.py)
# ---------------------------------------------------------------------------
def _l1_sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def _l1_rma(s: pd.Series, n: int) -> pd.Series:
    """Pine ta.rma: SMA seed, then alpha=1/n recursion."""
    x = s.astype(float).to_numpy()
    out = np.full(len(x), np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) < n:
        return pd.Series(out, index=s.index)
    seed_end = valid[n - 1]
    window = x[seed_end - n + 1 : seed_end + 1]
    if np.isnan(window).any():
        return pd.Series(out, index=s.index)
    out[seed_end] = window.mean()
    alpha = 1.0 / n
    for i in range(seed_end + 1, len(x)):
        out[i] = out[i - 1] if np.isnan(x[i]) else alpha * x[i] + (1 - alpha) * out[i - 1]
    return pd.Series(out, index=s.index)


def _l1_ema(s: pd.Series, n: int) -> pd.Series:
    return s.astype(float).ewm(span=n, adjust=False, min_periods=n).mean()


def _l1_wma(s: pd.Series, n: int) -> pd.Series:
    n = max(1, int(n))
    weights = np.arange(1.0, n + 1.0)
    return s.rolling(n, min_periods=n).apply(
        lambda a: float(np.dot(a, weights) / weights.sum()), raw=True
    )


def _l1_hma(s: pd.Series, n: int) -> pd.Series:
    return _l1_wma(
        2.0 * _l1_wma(s, max(1, int(n / 2))) - _l1_wma(s, n),
        max(1, int(math.floor(math.sqrt(n)))),
    )


def _l1_true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)


def _l1_atr(df: pd.DataFrame, n: int) -> pd.Series:
    return _l1_rma(_l1_true_range(df), n)


def _l1_dmi_from_series(
    high: pd.Series, low: pd.Series, tr: pd.Series, di_len: int, adx_len: int
) -> tuple[pd.Series, pd.Series, pd.Series]:
    u = high.diff()
    d = -low.diff()
    p = pd.Series(np.where((u > d) & (u > 0), u, 0.0), index=high.index)
    m = pd.Series(np.where((d > u) & (d > 0), d, 0.0), index=high.index)
    rt = _l1_rma(tr, di_len)
    plus = 100 * _l1_rma(p, di_len) / rt
    minus = 100 * _l1_rma(m, di_len) / rt
    den = (plus + minus).replace(0, 1.0)
    adx = 100 * _l1_rma((plus - minus).abs() / den, adx_len)
    return plus.ffill(), minus.ffill(), adx


def _l1_midpoint(df: pd.DataFrame, n: int) -> pd.Series:
    return (df["low"].rolling(n, min_periods=n).min() + df["high"].rolling(n, min_periods=n).max()) / 2.0


def _l1_cmf(df: pd.DataFrame, n: int) -> pd.Series:
    rng = df["high"] - df["low"]
    ad = pd.Series(
        np.where(rng.eq(0), 0.0, ((2 * df["close"] - df["low"] - df["high"]) / rng) * df["volume"]),
        index=df.index,
    )
    return ad.rolling(n, min_periods=n).sum() / df["volume"].rolling(n, min_periods=n).sum()


def _l1_efficiency_ratio(s: pd.Series, n: int) -> pd.Series:
    return s.diff(n).abs() / s.diff().abs().rolling(n, min_periods=n).sum().replace(0, np.nan)


def _l1_edge_signal(state: pd.Series) -> pd.Series:
    state = state.fillna(0).astype(int)
    return state.where(state.ne(state.shift(1)), 0).astype(int)


# ---------------------------------------------------------------------------
# Lab 2 indicator primitives (research/v3_tv_strategy_lab_2/indicators/pine.py)
# Keep separate from Lab 1 (ema uses min_periods=1, ignore_na=True).
# ---------------------------------------------------------------------------
def _l2_sma(s: pd.Series, n: int) -> pd.Series:
    return s.astype(float).rolling(n, min_periods=n).mean()


def _l2_ema(s: pd.Series, n: int) -> pd.Series:
    return s.astype(float).ewm(span=n, adjust=False, min_periods=1, ignore_na=True).mean()


def _l2_rma(s: pd.Series, n: int) -> pd.Series:
    x = s.astype(float).to_numpy()
    out = np.full(len(x), np.nan)
    vals: list[float] = []
    seed = -1
    for i, v in enumerate(x):
        if not np.isnan(v):
            vals.append(v)
        if len(vals) == n:
            seed = i
            out[i] = float(np.mean(vals[-n:]))
            break
    if seed < 0:
        return pd.Series(out, index=s.index)
    for i in range(seed + 1, len(x)):
        out[i] = out[i - 1] if np.isnan(x[i]) else (out[i - 1] * (n - 1) + x[i]) / n
    return pd.Series(out, index=s.index)


def _l2_wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda a: float(np.dot(a, w) / w.sum()), raw=True)


def _l2_hma(s: pd.Series, n: int) -> pd.Series:
    return _l2_wma(2 * _l2_wma(s, max(1, n // 2)) - _l2_wma(s, n), max(1, int(math.sqrt(n))))


def _l2_stdev(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).std(ddof=0)


def _l2_crossover(a: pd.Series, b) -> pd.Series:
    bb = b if isinstance(b, pd.Series) else pd.Series(float(b), index=a.index)
    return (a > bb) & (a.shift(1) <= bb.shift(1))


def _l2_crossunder(a: pd.Series, b) -> pd.Series:
    bb = b if isinstance(b, pd.Series) else pd.Series(float(b), index=a.index)
    return (a < bb) & (a.shift(1) >= bb.shift(1))


def _l2_rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0)
    dn = (-d).clip(lower=0)
    au = _l2_rma(up, n)
    ad = _l2_rma(dn, n)
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(ad.ne(0), 100.0)
    return out


def _l2_edge_state(long_cond: pd.Series, short_cond: pd.Series) -> pd.Series:
    state = 0
    out = np.zeros(len(long_cond), dtype=np.int8)
    for i, (lo, sh) in enumerate(zip(long_cond.fillna(False), short_cond.fillna(False))):
        if lo and state != 1:
            state = 1
            out[i] = 1
        elif sh and state != -1:
            state = -1
            out[i] = -1
    return pd.Series(out, index=long_cond.index)


# ---------------------------------------------------------------------------
# Strategy classes (exact ports of TV21 L1_S1/S3/S4/S5 & L2_S1/S3)
# ---------------------------------------------------------------------------
class TV21Strategy(ABC):
    strategy_id: str
    variants: tuple[str, ...]

    def validate(self, variant: str) -> None:
        if variant not in self.variants:
            raise ValueError(f"Unknown {self.strategy_id} variant: {variant!r}")

    @abstractmethod
    def evaluate(self, bars: pd.DataFrame, variant: str) -> pd.DataFrame:
        """Return frame containing signal in {-1, 0, 1} from closed HTF bars."""


class SuperTrendConfluence(TV21Strategy):
    strategy_id = "L1_S1"
    variants = ("B0", "V1_VOL_COMPRESS", "V2_PULLBACK_RECLAIM")

    def evaluate(self, bars: pd.DataFrame, variant: str) -> pd.DataFrame:
        self.validate(variant)
        d = bars.copy()
        a = _l1_atr(d, 10)
        ar = a / _l1_sma(a, 40)
        up = d["high"].diff()
        dn = -d["low"].diff()
        pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=d.index)
        ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=d.index)
        trr = _l1_rma(_l1_true_range(d), 14)
        pdi = 100 * _l1_rma(pdm, 14) / trr
        ndi = 100 * _l1_rma(ndm, 14) / trr
        adx = _l1_rma(100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, 1), 14)
        regime = pd.Series(np.where(ar > 1.4, 2, np.where((adx < 20) & (ar < 0.9), 0, 1)), index=d.index)
        mult = pd.Series(
            np.where(regime.eq(2), 3 * (1 + (ar - 1) * 0.4), np.where(regime.eq(0), 2.55, 3.0)),
            index=d.index,
        ).clip(1.5, 6)
        src = (d["high"] + d["low"]) / 2
        upper = src + mult * a
        lower = src - mult * a
        band = np.full(len(d), np.nan)
        direction = np.ones(len(d), dtype=int)
        for i in range(len(d)):
            if i == 0 or np.isnan(a.iloc[i]):
                band[i] = lower.iloc[i]
                continue
            direction[i] = direction[i - 1]
            prev = band[i - 1]
            if direction[i] == 1:
                band[i] = max(lower.iloc[i], prev)
                if d["close"].iloc[i] < band[i]:
                    direction[i] = -1
                    band[i] = upper.iloc[i]
            else:
                band[i] = min(upper.iloc[i], prev)
                if d["close"].iloc[i] > band[i]:
                    direction[i] = 1
                    band[i] = lower.iloc[i]
        b = pd.Series(band, index=d.index)
        sd = pd.Series(direction, index=d.index)
        flip = sd.ne(sd.shift())
        trend = _l1_ema(d["close"], 50)
        vm = _l1_sma(d["volume"], 20)
        safe = a.replace(0, 0.001)
        vr = d["volume"] / vm
        disp = pd.Series(np.where(sd.eq(1), d["close"] - b, b - d["close"]), index=d.index) / safe
        aligned = pd.Series(np.where(sd.eq(1), d["close"] > trend, d["close"] < trend), index=d.index)
        ed = (d["close"] - trend).abs() / safe
        prevdist = (d["close"].shift() - b.shift()).abs() / safe
        score = (
            np.select([vr >= 2.5, vr >= 1.5, vr >= 1], [20, 14, 8], 3)
            + np.select([disp >= 1.5, disp >= 0.8, disp >= 0.3, disp > 0], [25, 18, 12, 5], 0)
            + np.where(aligned & (ed > 0.5), 20, np.where(aligned, 14, np.where(ed < 0.3, 8, 2)))
            + np.where(regime.eq(1), 15, np.where(regime.eq(2), 8, 3))
            + np.select([prevdist >= 2, prevdist >= 1, prevdist >= 0.5], [20, 14, 8], 3)
        )
        base = flip & (score >= 65) & aligned & regime.ne(0) & d["volume"].gt(vm)
        sig = pd.Series(np.where(base, sd, 0), index=d.index, dtype=int)
        # V1_VOL_COMPRESS must be applied BEFORE the 5-bar cooldown loop.
        if variant == "V1_VOL_COMPRESS":
            sig = sig.where(ar.shift(1).rolling(5, min_periods=5).min().lt(0.95) & ar.gt(ar.shift()), 0)
        elif variant == "V2_PULLBACK_RECLAIM":
            out = np.zeros(len(d), dtype=int)
            pending = 0
            age = 0
            for i in range(len(d)):
                if sig.iloc[i]:
                    pending = int(sig.iloc[i])
                    age = 0
                    continue
                if pending:
                    age += 1
                    touched = d["low"].iloc[i] <= b.iloc[i] if pending == 1 else d["high"].iloc[i] >= b.iloc[i]
                    reclaimed = d["close"].iloc[i] > b.iloc[i] if pending == 1 else d["close"].iloc[i] < b.iloc[i]
                    if touched and reclaimed:
                        out[i] = pending
                        pending = 0
                    elif age >= 3:
                        pending = 0
            sig = pd.Series(out, index=d.index)
        last = -999
        arr = sig.to_numpy(copy=True)
        for i, v in enumerate(arr):
            if v and i - last <= 5:
                arr[i] = 0
            elif v:
                last = i
        return pd.DataFrame({"signal": arr, "atr": a, "band": b, "regime": regime, "score": score}, index=d.index)


class HullMaAdx(TV21Strategy):
    strategy_id = "L1_S3"
    variants = ("B0", "V1_ADX_RISING", "V2_DI_SPREAD")

    def evaluate(self, bars: pd.DataFrame, variant: str) -> pd.DataFrame:
        self.validate(variant)
        hh = _l1_hma(bars["high"], 15)
        hl = _l1_hma(bars["low"], 15)
        plus, minus, adx = _l1_dmi_from_series(hh, hl, _l1_true_range(bars), 18, 18)
        rising = adx > adx.shift()
        # B0 is intentionally asymmetric: BUY requires rising ADX, SELL does not.
        state = pd.Series(0, index=bars.index, dtype=int)
        state[(plus > minus) & rising] = 1
        state[minus > plus] = -1
        if variant == "V1_ADX_RISING":
            state = (
                pd.Series(0, index=bars.index, dtype=int)
                .mask((plus > minus) & rising, 1)
                .mask((minus > plus) & rising, -1)
            )
        if variant == "V2_DI_SPREAD":
            state = state.where((plus - minus).abs() >= 8, 0)
        return pd.DataFrame(
            {"signal": _l1_edge_signal(state), "plus_di": plus, "minus_di": minus, "adx": adx},
            index=bars.index,
        )


class IchimokuMacd(TV21Strategy):
    strategy_id = "L1_S4"
    variants = ("B0", "V1_KUMO_THICKNESS", "V2_MACD_HIST_ACCEL")

    def evaluate(self, bars: pd.DataFrame, variant: str) -> pd.DataFrame:
        self.validate(variant)
        ten = _l1_midpoint(bars, 10)
        kij = _l1_midpoint(bars, 30)
        sa = (ten + kij) / 2
        sb = _l1_midpoint(bars, 52)
        cloud_hi = pd.concat([sa.shift(25), sb.shift(25)], axis=1).max(axis=1)
        cloud_lo = pd.concat([sa.shift(25), sb.shift(25)], axis=1).min(axis=1)
        src = (bars["high"] + bars["low"]) / 2
        macd = _l1_sma(src, 12) - _l1_sma(src, 26)
        signal = _l1_ema(macd, 9)
        hist = macd - signal
        mf = _l1_cmf(bars, 10)
        pc = bars["close"].diff()
        tsi = 100 * _l1_ema(_l1_ema(pc, 20), 20) / _l1_ema(_l1_ema(pc.abs(), 20), 20)
        bull = (ten > kij) & (bars["close"] > bars["close"].shift(25)) & (bars["close"] > cloud_hi) & (hist > 0) & (mf > 0.1) & (tsi > 0)
        bear = (ten < kij) & (bars["close"] < bars["close"].shift(25)) & (bars["close"] < cloud_lo) & (hist < 0) & (mf < -0.1) & (tsi < 0)
        state = pd.Series(np.where(bull, 1, np.where(bear, -1, 0)), index=bars.index)
        # Filter raw state BEFORE edge_signal(state).
        if variant == "V1_KUMO_THICKNESS":
            state = state.where((cloud_hi - cloud_lo) / _l1_atr(bars, 14) >= 0.25, 0)
        if variant == "V2_MACD_HIST_ACCEL":
            state = state.where((state.eq(1) & hist.diff().gt(0)) | (state.eq(-1) & hist.diff().lt(0)), 0)
        return pd.DataFrame(
            {
                "signal": _l1_edge_signal(state),
                "tenkan": ten,
                "kijun": kij,
                "cloud_high": cloud_hi,
                "cloud_low": cloud_lo,
                "macd_hist": hist,
                "cmf": mf,
                "tsi": tsi,
            },
            index=bars.index,
        )


class DonchianKama(TV21Strategy):
    strategy_id = "L1_S5"
    variants = ("B0", "V1_ER_REGIME", "V2_BREAKOUT_BODY")

    def evaluate(self, bars: pd.DataFrame, variant: str) -> pd.DataFrame:
        self.validate(variant)
        upper = bars["high"].rolling(20, min_periods=20).max().shift()
        lower = bars["low"].rolling(20, min_periods=20).min().shift()
        ma = _l1_sma(bars["close"], 100)
        long = (bars["close"] >= upper) & (upper > ma)
        short = (bars["close"] <= lower) & (lower < ma)
        if variant == "B0":
            # B0 in lab is BUY-only.
            state = pd.Series(np.where(long, 1, 0), index=bars.index)
        elif variant == "V1_ER_REGIME":
            er = _l1_efficiency_ratio(bars["close"], 10)
            state = pd.Series(np.where(long & (er >= 0.30), 1, np.where(short & (er >= 0.30), -1, 0)), index=bars.index)
        else:
            body = (bars["close"] - bars["open"]).abs() / _l1_atr(bars, 14)
            state = pd.Series(np.where(long & (body >= 0.35), 1, np.where(short & (body >= 0.35), -1, 0)), index=bars.index)
        return pd.DataFrame(
            {"signal": _l1_edge_signal(state), "upper": upper, "lower": lower, "trend_ma": ma},
            index=bars.index,
        )


def _qqe(src: pd.Series, factor: float) -> tuple[pd.Series, pd.Series]:
    rm = _l2_ema(_l2_rsi(src, 6), 5)
    ar = (rm.shift(1) - rm).abs()
    dar = _l2_ema(_l2_ema(ar, 11), 11) * factor
    lb = np.zeros(len(src))
    sb = np.zeros(len(src))
    trend = np.ones(len(src), dtype=int)
    x = rm.to_numpy(float)
    d = dar.to_numpy(float)
    for i in range(len(src)):
        if np.isnan(x[i]) or np.isnan(d[i]):
            lb[i] = lb[i - 1] if i else 0.0
            sb[i] = sb[i - 1] if i else 0.0
            trend[i] = trend[i - 1] if i else 1
            continue
        nlb = x[i] - d[i]
        nsb = x[i] + d[i]
        if i:
            lb[i] = max(lb[i - 1], nlb) if x[i - 1] > lb[i - 1] and x[i] > lb[i - 1] else nlb
            sb[i] = min(sb[i - 1], nsb) if x[i - 1] < sb[i - 1] and x[i] < sb[i - 1] else nsb
            cross_short = (
                (x[i] > sb[i - 1] and x[i - 1] <= sb[i - 2] if i > 1 else False)
                or (x[i] < sb[i - 1] and x[i - 1] >= sb[i - 2] if i > 1 else False)
            )
            cross_long = (
                (lb[i - 1] > x[i] and lb[i - 2] <= x[i - 1] if i > 1 else False)
                or (lb[i - 1] < x[i] and lb[i - 2] >= x[i - 1] if i > 1 else False)
            )
            trend[i] = 1 if cross_short else (-1 if cross_long else trend[i - 1])
        else:
            lb[i] = nlb
            sb[i] = nsb
    tl = np.where(trend == 1, lb, sb)
    return rm, pd.Series(tl, index=src.index)


class SslHybridQqe(TV21Strategy):
    strategy_id = "L2_S1"
    variants = ("B0", "V1_FRESH_SSL_CROSS")

    def evaluate(self, bars: pd.DataFrame, variant: str) -> pd.DataFrame:
        self.validate(variant)
        c = bars["close"].astype(float)
        bh = _l2_hma(bars["high"], 60)
        bl = _l2_hma(bars["low"], 60)
        hlv = np.zeros(len(c), dtype=int)
        for i in range(len(c)):
            prev = hlv[i - 1] if i else 0
            hlv[i] = 1 if c.iloc[i] > bh.iloc[i] else (-1 if c.iloc[i] < bl.iloc[i] else prev)
        ssl = pd.Series(np.where(hlv < 0, bh, bl), index=c.index)
        r1, t1 = _qqe(c, 3.0)
        r2, t2 = _qqe(c, 1.61)
        basis = _l2_sma(t1 - 50, 50)
        dev = 0.35 * _l2_stdev(t1 - 50, 50)
        blue = (r2 - 50 > 3) & (r1 - 50 > basis + dev)
        red = (r2 - 50 < -3) & (r1 - 50 < basis - dev)
        qline = t2 - 50
        lo = (c > ssl) & blue & (qline > 0)
        sh = (c < ssl) & red & (qline < 0)
        sig = _l2_edge_state(lo, sh)
        if variant == "V1_FRESH_SSL_CROSS":
            crosses = (_l2_crossover(c, ssl) | _l2_crossunder(c, ssl)).to_numpy()
            age = 999
            fresh = np.zeros(len(c), bool)
            for i, v in enumerate(crosses):
                age = 0 if v else age + 1
                fresh[i] = age <= 3
            sig = sig.where(fresh, 0)
        return pd.DataFrame({"signal": sig, "ssl": ssl, "qqe_line": qline}, index=bars.index)


def _stc(close: pd.Series) -> pd.Series:
    m = _l2_ema(close, 23) - _l2_ema(close, 53)
    v1 = m.rolling(10, min_periods=10).min()
    v2 = m.rolling(10, min_periods=10).max() - v1
    f1 = np.full(len(m), np.nan)
    pf = np.full(len(m), np.nan)
    f2 = np.full(len(m), np.nan)
    pff = np.full(len(m), np.nan)
    ma = m.to_numpy()
    a = v1.to_numpy()
    b = v2.to_numpy()
    for i in range(len(m)):
        f1[i] = (ma[i] - a[i]) / b[i] * 100 if not np.isnan(b[i]) and b[i] > 0 else (f1[i - 1] if i else np.nan)
        pf[i] = f1[i] if i == 0 or np.isnan(pf[i - 1]) else pf[i - 1] + 0.5 * (f1[i] - pf[i - 1])
        if i >= 9 and not np.isnan(pf[i - 9 : i + 1]).all():
            lo = np.nanmin(pf[i - 9 : i + 1])
            span = np.nanmax(pf[i - 9 : i + 1]) - lo
            f2[i] = (pf[i] - lo) / span * 100 if span > 0 else (f2[i - 1] if i else np.nan)
        pff[i] = f2[i] if i == 0 or np.isnan(pff[i - 1]) else pff[i - 1] + 0.5 * (f2[i] - pff[i - 1])
    return pd.Series(pff, index=close.index)


class SchaffTrendCycle(TV21Strategy):
    strategy_id = "L2_S3"
    variants = ("B0", "V1_CONFIRM_1_BAR")

    def evaluate(self, bars: pd.DataFrame, variant: str) -> pd.DataFrame:
        self.validate(variant)
        x = _stc(bars["close"])
        lo = _l2_crossover(x, 25.0)
        sh = _l2_crossunder(x, 75.0)
        if variant == "V1_CONFIRM_1_BAR":
            lo = lo.shift(1, fill_value=False) & x.gt(25)
            sh = sh.shift(1, fill_value=False) & x.lt(75)
        return pd.DataFrame({"signal": lo.astype(int) - sh.astype(int), "stc": x}, index=bars.index)


TV21_STRATEGIES: dict[str, type[TV21Strategy]] = {
    "L1_S1": SuperTrendConfluence,
    "L1_S3": HullMaAdx,
    "L1_S4": IchimokuMacd,
    "L1_S5": DonchianKama,
    "L2_S1": SslHybridQqe,
    "L2_S3": SchaffTrendCycle,
}


# ---------------------------------------------------------------------------
# Post-edge wrapper filters (research/v3_tv_positive_variants_lab_5/run_lab_5.py)
# ---------------------------------------------------------------------------
def apply_tv21_wrapper(evaluated: pd.DataFrame, wrapper_variant: str) -> pd.DataFrame:
    """Apply post-edge TV21 wrapper filter.

    ``V1_TP_EXTENSION`` and ``V2_SL_TIGHTENING`` leave ``signal`` unchanged and
    never re-multiply ``tp_pct``/``sl_pct`` because ``TV21ComboConfig`` already
    stores the pre-multiplied locked percentages from ``TV21_SELECTED_REGISTRY``.
    """
    if wrapper_variant not in TV21_WRAPPERS:
        raise ValueError(f"Unknown TV21 wrapper variant: {wrapper_variant!r}")
    result = evaluated.copy()
    if wrapper_variant in {"BASELINE", "V1_TP_EXTENSION", "V2_SL_TIGHTENING"}:
        return result
    if wrapper_variant == "V3_BUY_ONLY":
        result.loc[result["signal"].lt(0), "signal"] = 0
        return result
    if wrapper_variant == "V4_SELL_ONLY":
        result.loc[result["signal"].gt(0), "signal"] = 0
        return result
    if wrapper_variant == "V5_EMA50_FILTER":
        ema50 = result["close"].ewm(span=50, adjust=False).mean()
        allowed = (result["signal"].gt(0) & result["close"].gt(ema50)) | (
            result["signal"].lt(0) & result["close"].lt(ema50)
        )
        result.loc[result["signal"].ne(0) & ~allowed, "signal"] = 0
        return result
    # V6_ATR_VOL_FILTER: EWM alpha=1/14 adjust=False min_periods=14, rolling(200) quantile 0.20..0.80, shift(1)
    previous = result["close"].shift(1)
    true_range = pd.concat(
        [
            (result["high"] - result["low"]).abs(),
            (result["high"] - previous).abs(),
            (result["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    normalized = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / result["close"]
    prior = normalized.shift(1)
    lower = prior.rolling(200, min_periods=200).quantile(0.20)
    upper = prior.rolling(200, min_periods=200).quantile(0.80)
    allowed = normalized.ge(lower) & normalized.le(upper) & lower.notna() & upper.notna()
    result.loc[result["signal"].ne(0) & ~allowed, "signal"] = 0
    return result


# ---------------------------------------------------------------------------
# Reference TV21 combo specifications (NOT in active live registry until PR-4)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TV21ComboConfig:
    display_id: str
    combo_id: int
    symbol: str
    timeframe: str
    strategy_id: str
    internal_variant: str
    wrapper_variant: str
    tp_pct: float
    sl_pct: float
    warmup_bars: int
    window_bars: int

    def __post_init__(self) -> None:
        if normalize_htf(self.timeframe) != self.timeframe:
            raise ValueError(f"{self.display_id}: timeframe={self.timeframe!r} must be canonical H4/H8/H12/D1")
        if self.strategy_id not in TV21_STRATEGIES:
            raise ValueError(f"{self.display_id}: unknown strategy_id={self.strategy_id!r}")
        TV21_STRATEGIES[self.strategy_id]().validate(self.internal_variant)
        if self.wrapper_variant not in TV21_WRAPPERS:
            raise ValueError(f"{self.display_id}: unknown wrapper_variant={self.wrapper_variant!r}")
        if self.tp_pct <= 0 or self.sl_pct <= 0:
            raise ValueError(f"{self.display_id}: tp_pct and sl_pct must be positive")
        if self.warmup_bars < 0 or self.window_bars <= 0:
            raise ValueError(f"{self.display_id}: warmup_bars >= 0 and window_bars > 0 required")

    @property
    def min_native_bars(self) -> int:
        return max(
            int(self.warmup_bars),
            V6_ATR_MIN_BARS if self.wrapper_variant == "V6_ATR_VOL_FILTER" else 0,
        )


TV21_CONFIGS: tuple[TV21ComboConfig, ...] = (
    TV21ComboConfig("TV03", 31003, BTC, "H4", "L1_S1", "B0", "V6_ATR_VOL_FILTER", 0.03, 0.015, 100, BTC_WINDOW_BARS),
    TV21ComboConfig("TV04", 31004, BTC, "H4", "L1_S4", "B0", "BASELINE", 0.03, 0.015, 100, BTC_WINDOW_BARS),
    TV21ComboConfig("TV07", 31007, ETH, "H4", "L1_S1", "B0", "V1_TP_EXTENSION", 0.036, 0.015, 100, ETH_WINDOW_BARS),
    TV21ComboConfig("TV08", 31008, ETH, "H4", "L1_S4", "V1_KUMO_THICKNESS", "V1_TP_EXTENSION", 0.036, 0.015, 100, ETH_WINDOW_BARS),
    TV21ComboConfig("TV09", 31009, ETH, "H4", "L1_S5", "V2_BREAKOUT_BODY", "V6_ATR_VOL_FILTER", 0.03, 0.015, 120, ETH_WINDOW_BARS),
    TV21ComboConfig("TV10", 31010, BTC, "H8", "L1_S1", "B0", "BASELINE", 0.03, 0.015, 100, BTC_WINDOW_BARS),
    TV21ComboConfig("TV12", 31012, BTC, "H8", "L1_S3", "B0", "V1_TP_EXTENSION", 0.03, 0.0125, 60, BTC_WINDOW_BARS),
    TV21ComboConfig("TV14", 31014, ETH, "H8", "L1_S1", "V1_VOL_COMPRESS", "V1_TP_EXTENSION", 0.036, 0.015, 100, ETH_WINDOW_BARS),
    TV21ComboConfig("TV15", 31015, ETH, "H8", "L1_S3", "B0", "BASELINE", 0.025, 0.0125, 60, ETH_WINDOW_BARS),
    TV21ComboConfig("TV16", 31016, ETH, "H8", "L1_S4", "V2_MACD_HIST_ACCEL", "V1_TP_EXTENSION", 0.036, 0.015, 100, ETH_WINDOW_BARS),
    TV21ComboConfig("TV17", 31017, ETH, "H8", "L1_S5", "V2_BREAKOUT_BODY", "V1_TP_EXTENSION", 0.036, 0.015, 120, ETH_WINDOW_BARS),
    TV21ComboConfig("TV20", 31020, BTC, "H12", "L1_S3", "V1_ADX_RISING", "V1_TP_EXTENSION", 0.03, 0.0125, 60, BTC_WINDOW_BARS),
    TV21ComboConfig("TV21", 31021, BTC, "H12", "L1_S5", "V2_BREAKOUT_BODY", "V1_TP_EXTENSION", 0.036, 0.015, 120, BTC_WINDOW_BARS),
    TV21ComboConfig("TV22", 31022, ETH, "H12", "L1_S1", "B0", "V1_TP_EXTENSION", 0.036, 0.015, 100, ETH_WINDOW_BARS),
    TV21ComboConfig("TV23", 31023, ETH, "H12", "L1_S3", "V2_DI_SPREAD", "V5_EMA50_FILTER", 0.025, 0.0125, 60, ETH_WINDOW_BARS),
    TV21ComboConfig("TV24", 31024, ETH, "H12", "L1_S5", "V2_BREAKOUT_BODY", "V1_TP_EXTENSION", 0.036, 0.015, 120, ETH_WINDOW_BARS),
    TV21ComboConfig("TV27", 31027, BTC, "D1", "L1_S5", "V2_BREAKOUT_BODY", "V2_SL_TIGHTENING", 0.03, 0.012, 120, BTC_WINDOW_BARS),
    TV21ComboConfig("TV28", 31028, BTC, "D1", "L2_S1", "B0", "V2_SL_TIGHTENING", 0.03, 0.012, 120, BTC_WINDOW_BARS),
    TV21ComboConfig("TV30", 31030, ETH, "D1", "L1_S5", "V2_BREAKOUT_BODY", "V1_TP_EXTENSION", 0.036, 0.015, 120, ETH_WINDOW_BARS),
    TV21ComboConfig("TV31", 31031, ETH, "D1", "L2_S3", "B0", "V6_ATR_VOL_FILTER", 0.025, 0.0125, 100, ETH_WINDOW_BARS),
)

TV21_CONFIG_MAP: dict[str, TV21ComboConfig] = {cfg.display_id: cfg for cfg in TV21_CONFIGS}

# PLUS43_V2_1_TP_ONLY (Owner-approved lab lock, v3_lab 5d7231f): TV28 D1->H8 plus TP-only changes on 7 combos.
# (display_id -> (timeframe, tp_pct)); SL, indicators, warmup and window bars are untouched.
TV21_V2_1_TP_OVERRIDES: dict[str, tuple[str | None, float]] = {
    "TV03": (None, 0.024),
    "TV07": (None, 0.045),
    "TV08": (None, 0.045),
    "TV09": (None, 0.0375),
    "TV12": (None, 0.0375),
    "TV20": (None, 0.0375),
    "TV24": (None, 0.045),
    "TV28": ("H8", 0.03),
}


def _apply_v2_1_overrides(cfg: TV21ComboConfig) -> TV21ComboConfig:
    ov = TV21_V2_1_TP_OVERRIDES.get(cfg.display_id)
    if ov is None:
        return cfg
    tf, tp = ov
    return replace(cfg, timeframe=tf or cfg.timeframe, tp_pct=tp)


TV21_CONFIGS_V2_1: tuple[TV21ComboConfig, ...] = tuple(_apply_v2_1_overrides(c) for c in TV21_CONFIGS)
TV21_CONFIG_MAP_V2_1: dict[str, TV21ComboConfig] = {cfg.display_id: cfg for cfg in TV21_CONFIGS_V2_1}


def _utc_ns_ohlcv(bars: pd.DataFrame) -> pd.DataFrame:
    d = bars[["open", "high", "low", "close", "volume"]].astype("float64").copy()
    idx = pd.DatetimeIndex(bars.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    d.index = idx.as_unit("ns")
    return d


def evaluate_tv21_bars(bars: pd.DataFrame, config: TV21ComboConfig) -> pd.DataFrame:
    """Evaluate a TV21 combo on already-resampled closed HTF bars in exact lab order."""
    htf = _utc_ns_ohlcv(bars)
    strat = TV21_STRATEGIES[config.strategy_id]()
    evaluated = strat.evaluate(htf, config.internal_variant)
    warmup = int(config.warmup_bars)
    if warmup:
        evaluated.iloc[:warmup, evaluated.columns.get_loc("signal")] = 0
    evaluated = evaluated.join(htf)
    return apply_tv21_wrapper(evaluated, config.wrapper_variant)


def generate_tv21_signals_from_htf(
    bars: pd.DataFrame,
    config: TV21ComboConfig,
    *,
    registry_version: str | None = None,
    spec_hash: str | None = None,
) -> list[V3Signal]:
    """Generate live ``V3Signal`` objects from closed HTF bars for one TV21 config."""
    if bars is None or bars.empty:
        return []
    htf = _utc_ns_ohlcv(bars)
    transformed = evaluate_tv21_bars(htf, config)
    sig_col = transformed["signal"].to_numpy(dtype=int)
    nz = np.flatnonzero(sig_col != 0)
    if not nz.size:
        return []

    tf_ms = timeframe_ms(config.timeframe)
    open_ms_arr = (htf.index.asi8 // 1_000_000).astype("int64")
    close_arr = htf["close"].to_numpy(dtype=float)
    variant_tag = f"{config.strategy_id}:{config.internal_variant}:{config.wrapper_variant}"

    out: list[V3Signal] = []
    for pos in nz:
        side = "BUY" if sig_col[pos] > 0 else "SELL"
        entry = float(close_arr[pos])
        close_ms = int(open_ms_arr[pos]) + tf_ms - 1
        tp = entry * (1.0 + config.tp_pct if side == "BUY" else 1.0 - config.tp_pct)
        sl = entry * (1.0 - config.sl_pct if side == "BUY" else 1.0 + config.sl_pct)
        out.append(
            V3Signal(
                event_id=f"{config.symbol}|{config.timeframe}|{config.display_id}|{side}|{close_ms}",
                symbol=config.symbol,
                combo=config.combo_id,
                combo_name=config.display_id,
                side=side,
                timeframe=config.timeframe,
                close_time=close_ms,
                entry=entry,
                tp=tp,
                sl=sl,
                tp_pct=config.tp_pct,
                sl_pct=config.sl_pct,
                registry_version=registry_version,
                spec_hash=spec_hash,
                signal_variant=variant_tag,
            )
        )
    return out


def generate_tv21_signals_from_m15(
    candles_15m: pd.DataFrame,
    config: TV21ComboConfig,
    *,
    only_latest_closed: bool = False,
    gate: TimeframeCloseGate | None = None,
    registry_version: str | None = None,
    spec_hash: str | None = None,
) -> list[V3Signal]:
    """Slice ``config.window_bars`` M15 candles, resample to HTF, and emit TV21 signals."""
    if candles_15m is None or candles_15m.empty:
        return []
    frame = candles_15m.tail(config.window_bars)
    if frame.empty:
        return []

    if only_latest_closed:
        if gate is not None:
            res = gate.resample_if_closed(
                config.symbol, frame, config.timeframe, scope=config.display_id
            )
            if res is None:
                return []
            htf_bars, expected_close_ms = res
        else:
            open_ms = _extract_open_ms(frame)
            expected_close_ms = int(open_ms.max()) + M15_MS - 1
            if not is_timeframe_close(expected_close_ms, config.timeframe):
                return []
            htf_bars = resample_m15_closed(frame, config.timeframe)
            htf_close_ms = latest_closed_htf_close_ms(htf_bars, config.timeframe)
            if htf_close_ms is None or htf_close_ms != expected_close_ms:
                return []
        sigs = generate_tv21_signals_from_htf(
            htf_bars, config, registry_version=registry_version, spec_hash=spec_hash
        )
        return [s for s in sigs if int(s.close_time) == expected_close_ms]

    htf_bars = resample_m15_closed(frame, config.timeframe)
    return generate_tv21_signals_from_htf(
        htf_bars, config, registry_version=registry_version, spec_hash=spec_hash
    )
