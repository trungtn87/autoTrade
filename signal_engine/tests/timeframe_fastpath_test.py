from __future__ import annotations

import time

import numpy as np
import pandas as pd

from app.timeframes import CANDLE_COLUMNS, aggregate_15m


SOURCE_MS = 15 * 60_000


def reference_aggregate_15m(df: pd.DataFrame, target_minutes: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    if target_minutes not in {60, 240, 360}:
        raise ValueError("target_minutes must be one of 60, 240, 360")

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

    rows = []
    for bucket_value, g in x.groupby("bucket", sort=True):
        g = g.sort_values("open_time")
        if len(g) != required:
            continue
        expected = [int(bucket_value) + i * SOURCE_MS for i in range(required)]
        actual = [int(v) for v in g["open_time"].tolist()]
        if actual != expected:
            continue
        rows.append({
            "open_time": int(bucket_value),
            "open": float(g.iloc[0]["open"]),
            "high": float(g["high"].max()),
            "low": float(g["low"].min()),
            "close": float(g.iloc[-1]["close"]),
            "volume": float(g["volume"].sum()),
            "close_time": int(bucket_value) + target_ms - 1,
        })
    return pd.DataFrame(rows, columns=CANDLE_COLUMNS)


def make_frame(n: int = 12000) -> pd.DataFrame:
    t = np.arange(n, dtype=np.int64) * SOURCE_MS
    base = 100.0 + np.arange(n, dtype=float) * 0.01
    return pd.DataFrame({
        "open_time": t,
        "open": base,
        "high": base + 2.0,
        "low": base - 2.0,
        "close": base + 0.5,
        "volume": 10.0 + (np.arange(n) % 17),
        "close_time": t + SOURCE_MS - 1,
    })


def assert_same(a: pd.DataFrame, b: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(
        a.reset_index(drop=True),
        b.reset_index(drop=True),
        check_dtype=False,
        check_exact=True,
    )


def main():
    base = make_frame()

    # Include duplicate and gap cases to prove the vectorized path preserves
    # the old completeness semantics rather than merely matching clean data.
    dup = pd.concat([base.iloc[:500], base.iloc[[499]], base.iloc[500:]], ignore_index=True)
    gap = base.drop(index=[123, 124, 7777]).reset_index(drop=True)

    for name, frame in [("clean", base), ("duplicate", dup), ("gap", gap)]:
        for minutes in (60, 240, 360):
            expected = reference_aggregate_15m(frame, minutes)
            actual = aggregate_15m(frame, minutes)
            assert_same(expected, actual)
            print(
                "TIMEFRAME_PARITY",
                {"case": name, "minutes": minutes, "rows": len(actual)},
                flush=True,
            )

    t0 = time.perf_counter()
    for minutes in (60, 240, 360):
        reference_aggregate_15m(base, minutes)
    old_sec = time.perf_counter() - t0

    t1 = time.perf_counter()
    for minutes in (60, 240, 360):
        aggregate_15m(base, minutes)
    new_sec = time.perf_counter() - t1

    print(
        "TIMEFRAME_BENCHMARK",
        {
            "bars": len(base),
            "reference_sec": round(old_sec, 4),
            "vectorized_sec": round(new_sec, 4),
            "speedup": round(old_sec / new_sec, 2) if new_sec else None,
        },
        flush=True,
    )
    print("TIMEFRAME vectorized parity PASS", flush=True)


if __name__ == "__main__":
    main()
