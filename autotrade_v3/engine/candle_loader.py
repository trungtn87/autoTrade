"""Deterministic M1 candle loader and no-lookahead resampler for AutoTrade V3."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import duckdb
import numpy as np
import pandas as pd

M1_MS = 60_000
M15_MS = 900_000
H1_MS = 3_600_000


def to_utc_ms(ts: str | datetime | pd.Timestamp | int) -> int:
    """Normalize timestamps to UTC integer milliseconds."""
    if isinstance(ts, int):
        return ts
    if isinstance(ts, str):
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    if isinstance(ts, pd.Timestamp):
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return int(ts.value // 1_000_000)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            dt = ts.astimezone(timezone.utc)
        return int(dt.timestamp() * 1000)
    raise TypeError(f"Unsupported timestamp type: {type(ts)}")


def validate_m1(df: pd.DataFrame) -> None:
    """Validate that M1 candles are continuous, monotonically increasing, with no gaps or duplicates."""
    if df.empty:
        raise ValueError("M1 candle dataframe is empty")
    if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
        raise ValueError("M1 candles must have a UTC-aware DatetimeIndex")
    if not df.index.is_monotonic_increasing:
        raise ValueError("M1 candles are not monotonically increasing")
    if df.index.has_duplicates:
        raise ValueError("M1 candles contain duplicate timestamps")

    t_ms = df.index.asi8 // 1_000_000
    if (t_ms % M1_MS != 0).any():
        raise ValueError("M1 candles are not aligned to exact 1-minute boundaries (00 seconds)")
    diffs = t_ms[1:] - t_ms[:-1]
    if len(diffs) > 0 and (diffs != M1_MS).any():
        bad_idx = np.where(diffs != M1_MS)[0][0]
        gap_from = df.index[bad_idx]
        gap_to = df.index[bad_idx + 1]
        raise ValueError(f"M1 candle sequence has gaps: discontinuity between {gap_from} and {gap_to}")

    required_cols = {"open", "high", "low", "close", "volume"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"M1 candle dataframe missing required columns: {missing}")

    # Check OHLC logical consistency
    if (df["high"] < df["low"]).any():
        raise ValueError("M1 candle high is lower than low")
    if (df["high"] < df["open"]).any() or (df["high"] < df["close"]).any():
        raise ValueError("M1 candle high is lower than open or close")
    if (df["low"] > df["open"]).any() or (df["low"] > df["close"]).any():
        raise ValueError("M1 candle low is higher than open or close")


def resample_m1_to_timeframe(
    df: pd.DataFrame,
    timeframe_min: int = 15,
) -> pd.DataFrame:
    """Resample continuous M1 candles into larger timeframe (e.g. 15m, 60m) strictly without lookahead.

    Every bucket MUST contain exactly timeframe_min M1 bars. Incomplete buckets are rejected.
    """
    validate_m1(df)
    if timeframe_min not in (15, 60):
        raise ValueError(f"Supported resampling intervals are 15 or 60 minutes, got {timeframe_min}")

    tf_ms = timeframe_min * M1_MS
    t_ms = df.index.asi8 // 1_000_000
    bucket_ms = (t_ms // tf_ms) * tf_ms

    temp = df.copy()
    temp["bucket_ms"] = bucket_ms

    grouped = temp.groupby("bucket_ms", sort=True)
    counts = grouped.size()
    if (counts != timeframe_min).any():
        bad_buckets = counts[counts != timeframe_min]
        raise ValueError(
            f"Incomplete {timeframe_min}-minute bucket detected: bucket {bad_buckets.index[0]} has {bad_buckets.iloc[0]} bars (expected {timeframe_min})"
        )

    agg_dict = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    if "provider" in temp.columns:
        agg_dict["provider"] = lambda s: s.iloc[0] if (s == s.iloc[0]).all() else "MIXED"

    res = grouped.agg(agg_dict)
    res.index = pd.to_datetime(res.index, unit="ms", utc=True)
    return res


def load_m1_from_catalog(
    catalog_path: str | Path,
    symbol: str,
    start: str | datetime | pd.Timestamp,
    end: str | datetime | pd.Timestamp,
) -> pd.DataFrame:
    """Load raw M1 candles from DuckDB catalog table v3_m1 for symbol and window."""
    start_ms = to_utc_ms(start)
    end_ms = to_utc_ms(end)
    sym_clean = symbol.replace("-", "").upper()

    con = duckdb.connect(str(catalog_path), read_only=True)
    try:
        query = """
            SELECT open_ms, "open", high, low, "close", volume, provider
            FROM v3_m1
            WHERE (symbol = ? OR symbol = ?)
              AND open_ms >= ? AND open_ms < ?
            ORDER BY open_ms ASC
        """
        df = con.execute(query, [symbol, sym_clean, start_ms, end_ms]).df()
    finally:
        con.close()

    if df.empty:
        raise ValueError(f"No M1 data found in catalog {catalog_path} for {symbol} between {start} and {end}")

    df.index = pd.to_datetime(df["open_ms"], unit="ms", utc=True)
    df = df.drop(columns=["open_ms"])
    validate_m1(df)
    return df


def generate_synthetic_m1(
    symbol: str,
    start: str = "2024-01-01",
    periods: int = 5000,
    base_price: float = 60000.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Deterministic synthetic M1 dataset generator for smoke tests, unit tests, and CI runs."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=periods, freq="1min", tz="UTC")
    returns = rng.normal(loc=0.000005, scale=0.0005, size=periods)
    price_series = base_price * np.exp(np.cumsum(returns))

    highs = price_series * (1.0 + np.abs(rng.normal(0, 0.0003, size=periods)))
    lows = price_series * (1.0 - np.abs(rng.normal(0, 0.0003, size=periods)))
    opens = np.roll(price_series, 1)
    opens[0] = base_price
    closes = price_series
    highs = np.maximum(highs, np.maximum(opens, closes))
    lows = np.minimum(lows, np.minimum(opens, closes))
    volumes = np.abs(rng.normal(10.0, 2.0, size=periods)) + 0.1

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "provider": ["SYNTHETIC"] * periods,
        },
        index=idx,
    )
    validate_m1(df)
    return df
