from __future__ import annotations

import math
import time

import numpy as np
import pandas as pd

from .bingx_market import BingXMarketClient, closed_only


COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]


def aggregate_candles(
    df: pd.DataFrame,
    source_minutes: int,
    target_minutes: int,
) -> pd.DataFrame:
    """Aggregate complete UTC-aligned source candles into a larger timeframe."""
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    if target_minutes % source_minutes != 0:
        raise ValueError("target timeframe must be divisible by source timeframe")

    source_ms = source_minutes * 60_000
    target_ms = target_minutes * 60_000
    required = target_minutes // source_minutes

    x = df[COLUMNS].sort_values("open_time").drop_duplicates("open_time", keep="last").copy()
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

    return pd.DataFrame(rows, columns=COLUMNS)


def _compare(generated: pd.DataFrame, reference: pd.DataFrame) -> dict:
    if generated.empty or reference.empty:
        return {
            "ok": False,
            "common_candles": 0,
            "reason": "empty_generated_or_reference",
        }

    a = generated.set_index("open_time")
    b = reference.set_index("open_time")
    common = a.index.intersection(b.index).sort_values()
    if len(common) == 0:
        return {
            "ok": False,
            "common_candles": 0,
            "reason": "no_common_open_time",
        }

    a = a.loc[common]
    b = b.loc[common]

    fields = ["open", "high", "low", "close", "volume"]
    field_stats = {}
    all_match = np.ones(len(common), dtype=bool)

    for field in fields:
        av = a[field].to_numpy(dtype=float)
        bv = b[field].to_numpy(dtype=float)
        abs_diff = np.abs(av - bv)

        # Price fields should be effectively exact. Volume summation can have
        # tiny floating-point noise, so use a very small relative tolerance.
        if field == "volume":
            match = np.isclose(av, bv, rtol=1e-9, atol=1e-9, equal_nan=False)
        else:
            match = np.isclose(av, bv, rtol=1e-12, atol=1e-8, equal_nan=False)

        all_match &= match
        field_stats[field] = {
            "matched": int(match.sum()),
            "total": int(len(match)),
            "max_abs_diff": float(abs_diff.max()) if len(abs_diff) else 0.0,
        }

    mismatches = []
    bad_positions = np.where(~all_match)[0][:5]
    for pos in bad_positions:
        ts = int(common[pos])
        mismatches.append({
            "open_time": ts,
            "generated": {
                k: float(a.iloc[pos][k]) for k in fields
            },
            "reference": {
                k: float(b.iloc[pos][k]) for k in fields
            },
        })

    return {
        "ok": bool(all_match.all()),
        "common_candles": int(len(common)),
        "matched_candles": int(all_match.sum()),
        "match_rate": float(all_match.mean()),
        "field_stats": field_stats,
        "mismatch_samples": mismatches,
    }


def validate_15m_resample(
    market: BingXMarketClient,
    symbol: str,
    days: int = 7,
) -> dict:
    """Validate 15m-only architecture against direct BingX 1H/4H history.

    API calls per run:
      1 x 15m
      1 x 1h
      1 x 4h

    6H is validated without a direct 6H BingX request by comparing:
      15m -> 6H
      1h  -> 6H
    """
    if days < 2 or days > 7:
        raise ValueError("days must be between 2 and 7")

    now_ms = int(time.time() * 1000)
    # End at the most recent fully closed 15m boundary.
    fifteen_ms = 15 * 60_000
    end_ms = (now_ms // fifteen_ms) * fifteen_ms - 1
    start_ms = end_ms - days * 24 * 60 * 60_000 + 1

    # Small margin ensures the first complete HTF bucket is available.
    start_with_margin = start_ms - 24 * 60 * 60_000

    limit_15m = min(1440, math.ceil((end_ms - start_with_margin + 1) / fifteen_ms) + 4)
    limit_1h = min(1440, math.ceil((end_ms - start_with_margin + 1) / 3_600_000) + 4)
    limit_4h = min(1440, math.ceil((end_ms - start_with_margin + 1) / 14_400_000) + 4)

    m15 = closed_only(
        market.klines(
            symbol,
            "15m",
            limit_15m,
            start_time=start_with_margin,
            end_time=end_ms,
        ),
        now_ms,
    )
    h1_ref = closed_only(
        market.klines(
            symbol,
            "1h",
            limit_1h,
            start_time=start_with_margin,
            end_time=end_ms,
        ),
        now_ms,
    )
    h4_ref = closed_only(
        market.klines(
            symbol,
            "4h",
            limit_4h,
            start_time=start_with_margin,
            end_time=end_ms,
        ),
        now_ms,
    )

    h1_from_15 = aggregate_candles(m15, 15, 60)
    h4_from_15 = aggregate_candles(m15, 15, 240)
    h6_from_15 = aggregate_candles(m15, 15, 360)
    h6_from_1h = aggregate_candles(h1_ref, 60, 360)

    # Compare only the requested days; margin is bootstrap for full first bucket.
    def trim(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        return df[(df["open_time"] >= start_ms) & (df["close_time"] <= end_ms)].reset_index(drop=True)

    h1_from_15 = trim(h1_from_15)
    h4_from_15 = trim(h4_from_15)
    h6_from_15 = trim(h6_from_15)
    h1_ref = trim(h1_ref)
    h4_ref = trim(h4_ref)
    h6_from_1h = trim(h6_from_1h)

    cmp_1h = _compare(h1_from_15, h1_ref)
    cmp_4h = _compare(h4_from_15, h4_ref)
    cmp_6h = _compare(h6_from_15, h6_from_1h)

    return {
        "ok": bool(cmp_1h.get("ok") and cmp_4h.get("ok") and cmp_6h.get("ok")),
        "symbol": symbol,
        "days": days,
        "api_calls": 3,
        "window": {
            "start_ms": int(start_ms),
            "end_ms": int(end_ms),
        },
        "source_counts": {
            "15m": int(len(m15)),
            "1h_direct": int(len(h1_ref)),
            "4h_direct": int(len(h4_ref)),
        },
        "generated_counts": {
            "1h_from_15m": int(len(h1_from_15)),
            "4h_from_15m": int(len(h4_from_15)),
            "6h_from_15m": int(len(h6_from_15)),
            "6h_from_1h": int(len(h6_from_1h)),
        },
        "comparison": {
            "1h_15m_vs_direct": cmp_1h,
            "4h_15m_vs_direct": cmp_4h,
            "6h_15m_vs_1h": cmp_6h,
        },
    }
