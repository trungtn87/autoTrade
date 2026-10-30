from __future__ import annotations

import math

import pandas as pd


def synthetic_15m(count: int = 3400) -> pd.DataFrame:
    start = 1_700_000_000_000
    step = 15 * 60_000
    twelve_h = 12 * 60 * 60_000
    start = (start // twelve_h) * twelve_h
    rows = []
    prev_close = 30_000.0
    for i in range(count):
        t = start + i * step
        drift = i * 1.75
        wave = 180.0 * math.sin(i / 17.0) + 65.0 * math.sin(i / 5.0)
        close = 30_000.0 + drift + wave
        open_ = prev_close
        high = max(open_, close) + 35.0 + (i % 7)
        low = min(open_, close) - 32.0 - (i % 5)
        volume = 100.0 + (i % 23) * 3.0 + abs(math.sin(i / 8.0)) * 40.0
        rows.append({
            "open_time": t,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "close_time": t + step - 1,
        })
        prev_close = close
    return pd.DataFrame(rows)
