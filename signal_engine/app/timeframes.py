from __future__ import annotations

import pandas as pd


CANDLE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time"
]


def aggregate_15m(df: pd.DataFrame, target_minutes: int) -> pd.DataFrame:
    """Build complete UTC-aligned HTF candles from closed 15m candles.

    This is vectorized but preserves the original completeness rules exactly:
    only buckets containing every expected 15m open time are emitted.
    """
    if df.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    if target_minutes not in {60, 240, 360}:
        raise ValueError("target_minutes must be one of 60, 240, 360")

    source_ms = 15 * 60_000
    target_ms = target_minutes * 60_000
    required = target_minutes // 15

    x = df.loc[:, CANDLE_COLUMNS].copy(deep=True)
    x = (
        x.sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .reset_index(drop=True)
    )
    bucket = (x["open_time"].to_numpy(dtype="int64") // target_ms) * target_ms
    x = x.assign(bucket=bucket)

    # Avoid Python-per-group loops: aggregate all buckets in one pandas pass,
    # then keep only fully populated UTC-aligned buckets.
    out = (
        x.groupby("bucket", sort=True, as_index=False)
        .agg(
            first_open=("open_time", "first"),
            last_open=("open_time", "last"),
            n=("open_time", "size"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
    )

    complete = (
        (out["n"] == required)
        & (out["first_open"] == out["bucket"])
        & (out["last_open"] == out["bucket"] + (required - 1) * source_ms)
    )
    out = out.loc[complete].copy()
    if out.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS)

    out["open_time"] = out["bucket"].astype("int64")
    out["close_time"] = out["open_time"] + target_ms - 1

    return out.loc[:, CANDLE_COLUMNS].reset_index(drop=True)
