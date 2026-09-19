from __future__ import annotations

import math

import numpy as np
import pandas as pd


CANDLE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time"
]
INTERVAL_15M_MS = 15 * 60_000


class DataValidationError(RuntimeError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


def validate_15m_candles(df: pd.DataFrame, now_ms: int) -> dict:
    """Strict pre-trade validation for the cached closed 15m series.

    The strategy/order path must not run when this check fails.
    """
    if df is None or df.empty:
        raise DataValidationError("15m cache is empty")

    missing = [c for c in CANDLE_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(f"15m cache missing columns: {missing}")

    x = df.loc[:, CANDLE_COLUMNS].copy(deep=True)
    if len(x) < 2:
        raise DataValidationError("15m cache has fewer than 2 candles", {"count": len(x)})

    numeric = x[CANDLE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    arr = numeric.to_numpy(dtype=float)
    if not np.isfinite(arr).all():
        raise DataValidationError("15m cache contains NaN/inf or non-numeric values")

    open_time = numeric["open_time"].astype("int64")
    close_time = numeric["close_time"].astype("int64")

    if open_time.duplicated().any():
        raise DataValidationError("15m cache contains duplicate open_time")
    if not open_time.is_monotonic_increasing:
        raise DataValidationError("15m cache is not sorted by open_time")

    if (open_time % INTERVAL_15M_MS != 0).any():
        raise DataValidationError("15m candle open_time is not UTC 15m aligned")
    if not (close_time == open_time + INTERVAL_15M_MS - 1).all():
        raise DataValidationError("15m candle close_time does not match open_time + 15m - 1ms")

    diffs = open_time.diff().dropna()
    bad_diffs = diffs[diffs != INTERVAL_15M_MS]
    if len(bad_diffs):
        first_idx = int(bad_diffs.index[0])
        raise DataValidationError(
            "15m cache contains a candle gap",
            {
                "gap_at_row": first_idx,
                "gap_ms": int(bad_diffs.iloc[0]),
            },
        )

    o = numeric["open"].to_numpy(dtype=float)
    h = numeric["high"].to_numpy(dtype=float)
    l = numeric["low"].to_numpy(dtype=float)
    c = numeric["close"].to_numpy(dtype=float)
    v = numeric["volume"].to_numpy(dtype=float)

    if (o <= 0).any() or (h <= 0).any() or (l <= 0).any() or (c <= 0).any():
        raise DataValidationError("15m cache contains non-positive OHLC price")
    if (v < 0).any():
        raise DataValidationError("15m cache contains negative volume")
    if (h < l).any():
        raise DataValidationError("15m cache contains high < low")
    if (h < np.maximum(o, c)).any():
        raise DataValidationError("15m cache contains high below open/close")
    if (l > np.minimum(o, c)).any():
        raise DataValidationError("15m cache contains low above open/close")

    latest_open = int(open_time.iloc[-1])
    latest_close = int(close_time.iloc[-1])
    expected_latest_open = (int(now_ms) // INTERVAL_15M_MS) * INTERVAL_15M_MS - INTERVAL_15M_MS

    if latest_open != expected_latest_open:
        age_intervals = (expected_latest_open - latest_open) // INTERVAL_15M_MS
        raise DataValidationError(
            "latest closed 15m candle is not the expected candle",
            {
                "latest_open": latest_open,
                "expected_latest_open": expected_latest_open,
                "age_intervals": int(age_intervals),
            },
        )

    if latest_close >= int(now_ms):
        raise DataValidationError(
            "latest 15m candle is not closed yet",
            {"latest_close": latest_close, "now_ms": int(now_ms)},
        )

    return {
        "ok": True,
        "count": len(x),
        "first_open": int(open_time.iloc[0]),
        "latest_open": latest_open,
        "latest_close": latest_close,
        "expected_latest_open": expected_latest_open,
        "gap_count": 0,
        "duplicate_count": 0,
    }
