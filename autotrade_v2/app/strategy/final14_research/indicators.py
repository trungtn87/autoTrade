from __future__ import annotations
import numpy as np
import pandas as pd

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()

def rma(s: pd.Series, n: int) -> pd.Series:
    x = s.astype(float).to_numpy()
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return pd.Series(out, index=s.index)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        if np.isfinite(w).all():
            out[i] = w.mean()
            start = i + 1
            break
    else:
        return pd.Series(out, index=s.index)
    for i in range(start, len(x)):
        if np.isfinite(x[i]) and np.isfinite(out[i - 1]):
            out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return pd.Series(out, index=s.index)

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df['close'].shift(1)
    return pd.concat([df['high'] - df['low'],(df['high'] - prev_close).abs(),(df['low'] - prev_close).abs()], axis=1).max(axis=1)

def atr(df: pd.DataFrame, n: int) -> pd.Series:
    return rma(true_range(df), n)

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff(); up = d.clip(lower=0); dn = (-d).clip(lower=0)
    au = rma(up, n); ad = rma(dn, n)
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(ad != 0, 100.0)
    out = out.where(au != 0, 0.0)
    return out

def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    mf = tp * df['volume']; d = tp.diff()
    pos = mf.where(d > 0, 0.0); neg = mf.where(d < 0, 0.0)
    ps = pos.rolling(n, min_periods=n).sum(); ns = neg.rolling(n, min_periods=n).sum()
    ratio = ps / ns.replace(0, np.nan)
    out = 100 - 100 / (1 + ratio)
    out = out.where(ns != 0, 100.0)
    return out

def cci(close_or_source: pd.Series, high: pd.Series, low: pd.Series, n: int) -> pd.Series:
    src = close_or_source; ma = sma(src, n)
    md = src.rolling(n, min_periods=n).apply(lambda a: np.mean(np.abs(a - np.mean(a))), raw=True)
    return (src - ma) / (0.015 * md.replace(0, np.nan))

def stochastic(close: pd.Series, high: pd.Series, low: pd.Series, n: int) -> pd.Series:
    lo = low.rolling(n, min_periods=n).min(); hi = high.rolling(n, min_periods=n).max()
    return 100 * (close - lo) / (hi - lo).replace(0, np.nan)

def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))

def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))

def dmi(df: pd.DataFrame, di_len: int = 14, adx_len: int = 14):
    up = df['high'].diff(); down = -df['low'].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr_rma = rma(true_range(df), di_len)
    plus = 100 * rma(plus_dm, di_len) / tr_rma.replace(0, np.nan)
    minus = 100 * rma(minus_dm, di_len) / tr_rma.replace(0, np.nan)
    dx = 100 * (plus - minus).abs() / (plus + minus).replace(0, np.nan)
    adx = rma(dx, adx_len)
    return plus, minus, adx

def parabolic_sar(df: pd.DataFrame, start: float = 0.02, inc: float = 0.02, max_af: float = 0.2) -> pd.Series:
    h = df['high'].to_numpy(float); l = df['low'].to_numpy(float); c = df['close'].to_numpy(float)
    n = len(df); out = np.full(n, np.nan)
    if n < 2: return pd.Series(out, index=df.index)
    bull = c[1] >= c[0]; af = start; ep = h[0] if bull else l[0]; sar = l[0] if bull else h[0]; out[0] = sar
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, l[i-1], l[i-2]) if i >= 2 else min(sar, l[i-1])
            if l[i] < sar: bull = False; sar = ep; ep = l[i]; af = start
            elif h[i] > ep: ep = h[i]; af = min(max_af, af + inc)
        else:
            sar = max(sar, h[i-1], h[i-2]) if i >= 2 else max(sar, h[i-1])
            if h[i] > sar: bull = True; sar = ep; ep = h[i]; af = start
            elif l[i] < ep: ep = l[i]; af = min(max_af, af + inc)
        out[i] = sar
    return pd.Series(out, index=df.index)

def obv_variant(df: pd.DataFrame) -> pd.Series:
    return (df['close'].diff() * df['volume']).cumsum()

def session_vwap(df: pd.DataFrame) -> pd.Series:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError('DataFrame index must be DatetimeIndex')
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    pv = tp * df['volume']; day = df.index.floor('D')
    return pv.groupby(day).cumsum() / df['volume'].groupby(day).cumsum().replace(0, np.nan)

def edge_event(cond: pd.Series) -> pd.Series:
    current = cond.astype("boolean").fillna(False)
    previous = current.shift(1, fill_value=False)
    return (current & ~previous).astype(bool)

def pine_custom_supertrend_dir(df: pd.DataFrame, atr_n: int, factor: float) -> pd.Series:
    a = atr(df, atr_n); hl2 = (df['high'] + df['low']) / 2
    upper = hl2 + factor * a; lower = hl2 - factor * a
    trend = np.full(len(df), np.nan)
    close = df['close'].to_numpy(float); up = upper.to_numpy(float); lo = lower.to_numpy(float); mid = hl2.to_numpy(float)
    for i in range(len(df)):
        if i == 0 or not np.isfinite(trend[i-1]):
            trend[i] = mid[i]; continue
        if close[i-1] > trend[i-1]:
            trend[i] = max(lo[i], trend[i-1]) if np.isfinite(lo[i]) else trend[i-1]
        else:
            trend[i] = min(up[i], trend[i-1]) if np.isfinite(up[i]) else trend[i-1]
    t = pd.Series(trend, index=df.index)
    return pd.Series(np.where(df['close'] > t, 1, -1), index=df.index)

def pine_combo15_supertrend_dir(df: pd.DataFrame) -> pd.Series:
    a = atr(df, 14); base = sma(df['close'], 8)
    upper = base + 2*a; lower = base - 2*a
    out = np.ones(len(df), dtype=int)
    c = df['close'].to_numpy(float); u=upper.to_numpy(float); l=lower.to_numpy(float)
    for i in range(len(df)):
        prev = out[i-1] if i else 1
        out[i] = 1 if (np.isfinite(l[i]) and c[i] > l[i]) else (-1 if (np.isfinite(u[i]) and c[i] < u[i]) else prev)
    return pd.Series(out, index=df.index)
