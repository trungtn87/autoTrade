from __future__ import annotations

import math
import numpy as np
import pandas as pd


def sma(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length, min_periods=length).mean()


def ema(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(span=length, adjust=False, min_periods=1).mean()


def rma(s: pd.Series, length: int) -> pd.Series:
    """Wilder RMA close to Pine ta.rma semantics."""
    x = s.astype(float).to_numpy()
    out = np.full(len(x), np.nan, dtype=float)
    seed_vals: list[float] = []
    seeded = False
    prev = np.nan
    for i, v in enumerate(x):
        if np.isnan(v):
            continue
        if not seeded:
            seed_vals.append(float(v))
            if len(seed_vals) == length:
                prev = float(np.mean(seed_vals))
                out[i] = prev
                seeded = True
        else:
            prev = (prev * (length - 1) + float(v)) / length
            out[i] = prev
    return pd.Series(out, index=s.index)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    a = df["high"] - df["low"]
    b = (df["high"] - prev_close).abs()
    c = (df["low"] - prev_close).abs()
    return pd.concat([a, b, c], axis=1).max(axis=1)


def atr(df: pd.DataFrame, length: int) -> pd.Series:
    return rma(true_range(df), length)


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    ch = close.diff()
    up = ch.clip(lower=0)
    dn = (-ch).clip(lower=0)
    au = rma(up, length)
    ad = rma(dn, length)
    rs = au / ad
    out = 100 - (100 / (1 + rs))
    out = out.where(ad != 0, 100.0)
    out = out.where(~((au == 0) & (ad == 0)), 50.0)
    return out


def dmi(df: pd.DataFrame, di_length: int = 14, adx_smoothing: int = 14):
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr_rma = rma(true_range(df), di_length)
    plus = 100 * rma(plus_dm, di_length) / tr_rma
    minus = 100 * rma(minus_dm, di_length) / tr_rma
    denom = plus + minus
    dx = 100 * (plus - minus).abs() / denom
    dx = dx.where(denom != 0, 0.0)
    adx = rma(dx, adx_smoothing)
    return plus, minus, adx


def mfi(df: pd.DataFrame, length: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    raw = tp * df["volume"]
    ch = tp.diff()
    pos = raw.where(ch > 0, 0.0)
    neg = raw.where(ch < 0, 0.0)
    ps = pos.rolling(length, min_periods=length).sum()
    ns = neg.rolling(length, min_periods=length).sum()
    ratio = ps / ns
    out = 100 - (100 / (1 + ratio))
    out = out.where(ns != 0, 100.0)
    out = out.where(~((ps == 0) & (ns == 0)), 50.0)
    return out


def cci(src: pd.Series, length: int) -> pd.Series:
    ma = sma(src, length)
    mad = src.rolling(length, min_periods=length).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    out = (src - ma) / (0.015 * mad)
    return out


def stoch(close: pd.Series, high: pd.Series, low: pd.Series, length: int) -> pd.Series:
    hh = high.rolling(length, min_periods=length).max()
    ll = low.rolling(length, min_periods=length).min()
    den = hh - ll
    return 100 * (close - ll) / den


def stdev(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length, min_periods=length).std(ddof=0)


def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def parabolic_sar(df: pd.DataFrame, start: float, inc: float, maximum: float) -> pd.Series:
    """Implementation based on the Pine reference algorithm."""
    n = len(df)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    out = np.full(n, np.nan)
    if n < 2:
        return pd.Series(out, index=df.index)

    is_below = None
    max_min = np.nan
    acceleration = start
    result = np.nan

    for i in range(n):
        if i == 1:
            if close[i] > close[i - 1]:
                is_below = True
                max_min = high[i]
                result = low[i - 1]
            else:
                is_below = False
                max_min = low[i]
                result = high[i - 1]
            acceleration = start
            out[i] = result
            continue
        if i < 2 or is_below is None or np.isnan(result):
            continue

        result = result + acceleration * (max_min - result)
        first_trend_bar = False

        if is_below:
            result = min(result, low[i - 1])
            result = min(result, low[i - 2])
            if result > low[i]:
                first_trend_bar = True
                is_below = False
                result = max_min
                max_min = low[i]
                acceleration = start
        else:
            result = max(result, high[i - 1])
            result = max(result, high[i - 2])
            if result < high[i]:
                first_trend_bar = True
                is_below = True
                result = max_min
                max_min = high[i]
                acceleration = start

        if not first_trend_bar:
            if is_below and high[i] > max_min:
                max_min = high[i]
                acceleration = min(acceleration + inc, maximum)
            elif (not is_below) and low[i] < max_min:
                max_min = low[i]
                acceleration = min(acceleration + inc, maximum)

        out[i] = result

    return pd.Series(out, index=df.index)


def nadaraya_mid(close: pd.Series, length: int, smooth: int) -> pd.Series:
    weights = np.array([math.exp(-math.pow(i / length * smooth, 2.0)) for i in range(length + 1)], dtype=float)
    den = weights.sum()
    arr = close.to_numpy(float)
    out = np.full(len(arr), np.nan)
    for t in range(length, len(arr)):
        vals = arr[t - length:t + 1][::-1]
        if np.isnan(vals).any():
            continue
        out[t] = float(np.dot(vals, weights) / den)
    return pd.Series(out, index=close.index)


def ut_atr_trailing_stop(close: pd.Series, atr14: pd.Series, mult: float = 1.5) -> pd.Series:
    x = close.to_numpy(float)
    a = atr14.to_numpy(float)
    out = np.full(len(x), np.nan)
    for i in range(len(x)):
        if np.isnan(x[i]) or np.isnan(a[i]):
            continue
        prev_stop = 0.0 if i == 0 or np.isnan(out[i - 1]) else out[i - 1]
        prev_src = np.nan if i == 0 else x[i - 1]
        trail = mult * a[i]
        if not np.isnan(prev_src) and x[i] > prev_stop and prev_src > prev_stop:
            out[i] = max(prev_stop, x[i] - trail)
        elif not np.isnan(prev_src) and x[i] < prev_stop and prev_src < prev_stop:
            out[i] = min(prev_stop, x[i] + trail)
        else:
            out[i] = x[i] - trail if x[i] > prev_stop else x[i] + trail
    return pd.Series(out, index=close.index)


def combo3_custom_trend(df: pd.DataFrame, atr8: pd.Series, factor: float = 4.0) -> pd.Series:
    hl2 = (df["high"] + df["low"]) / 2.0
    upper = hl2 + factor * atr8
    lower = hl2 - factor * atr8
    c = df["close"].to_numpy(float)
    u = upper.to_numpy(float)
    l = lower.to_numpy(float)
    h = hl2.to_numpy(float)
    out = np.full(len(df), np.nan)
    for i in range(len(df)):
        if i == 0 or np.isnan(out[i - 1]):
            out[i] = h[i]
            continue
        prev = out[i - 1]
        out[i] = max(l[i], prev) if c[i - 1] > prev else min(u[i], prev)
    return pd.Series(out, index=df.index)


def combo15_supertrend_dir(df: pd.DataFrame, atr14: pd.Series) -> pd.Series:
    base = sma(df["close"], 8)
    upper = base + 2 * atr14
    lower = base - 2 * atr14
    out = np.zeros(len(df), dtype=int)
    prev = 1
    for i in range(len(df)):
        lo = lower.iloc[i]
        up = upper.iloc[i]
        c = df["close"].iloc[i]
        d = prev
        if not np.isnan(lo) and c > lo:
            d = 1
        elif not np.isnan(up) and c < up:
            d = -1
        out[i] = d
        prev = d
    return pd.Series(out, index=df.index)
