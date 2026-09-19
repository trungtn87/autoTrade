from __future__ import annotations

import pandas as pd


CANDLE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time"
]


def aggregate_15m(df: pd.DataFrame, target_minutes: int) -> pd.DataFrame:
    """Build complete UTC-aligned HTF candles from closed 15m candles."""
    if df.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    if target_minutes not in {60, 240, 360}:
        raise ValueError("target_minutes must be one of 60, 240, 360")

    source_ms = 15 * 60_000
    target_ms = target_minutes * 60_000
    required = target_minutes // 15

    x = (
        df[CANDLE_COLUMNS]
        .sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .copy()
    )
    x["bucket"] = (x["open_time"] // target_ms) * target_ms

    rows = []
    for bucket, g in x.groupby("bucket", sort=True):
        g = g.sort_values("open_time")
        if len(g) != required:
            continue

        expected = [int(bucket) + i * source_ms for i in range(required)]
        actual = [int(v) for v in g["open_time"].tolist()]
        if actual != expected:
            continue

        rows.append({
            "open_time": int(bucket),
            "open": float(g.iloc[0]["open"]),
            "high": float(g["high"].max()),
            "low": float(g["low"].min()),
            "close": float(g.iloc[-1]["close"]),
            "volume": float(g["volume"].sum()),
            "close_time": int(bucket) + target_ms - 1,
        })

    return pd.DataFrame(rows, columns=CANDLE_COLUMNS)
