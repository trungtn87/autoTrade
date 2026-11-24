"""Tests for UTC H4/H8/H12/D1 resampling from M15 and close-boundary gating."""
from __future__ import annotations

import pandas as pd
import pytest

from autotrade_v3.l2_core import resample_ohlcv as live_resample_ohlcv
from autotrade_v3.live.l2_resampler import (
    M15_MS,
    TimeframeCloseGate,
    bar_close_ms,
    bars_per_bucket,
    is_timeframe_close,
    latest_closed_htf_close_ms,
    normalize_htf,
    resample_m15_closed,
    should_emit_htf_signal,
    timeframe_ms,
)


def _m15_series(start_iso: str, count: int, *, with_open_time_col: bool = True) -> pd.DataFrame:
    start_ms = int(pd.Timestamp(start_iso, tz="UTC").value // 1_000_000)
    opens = [start_ms + i * M15_MS for i in range(count)]
    # Distinct per-bar prices and volumes so OHLCV aggregation errors stand out immediately.
    o = [1000.0 + i * 1.0 for i in range(count)]
    h = [1005.0 + i * 1.0 + (i % 7) * 0.5 for i in range(count)]
    l = [995.0 + i * 1.0 - (i % 5) * 0.5 for i in range(count)]
    c = [1000.5 + i * 1.0 + ((i % 3) - 1) * 0.25 for i in range(count)]
    v = [10.0 + (i % 11) for i in range(count)]
    df = pd.DataFrame({
        "open_time": opens,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "close_time": [t + M15_MS - 1 for t in opens],
    })
    if not with_open_time_col:
        df.index = pd.DatetimeIndex(pd.to_datetime(opens, unit="ms", utc=True)).as_unit("ns")
        df = df[["open", "high", "low", "close", "volume"]]
    return df


def test_normalize_and_timeframe_metadata() -> None:
    assert normalize_htf("4h") == "H4"
    assert normalize_htf("H8") == "H8"
    assert normalize_htf("8h") == "H8"
    assert normalize_htf("480") == "H8"
    assert normalize_htf("12h") == "H12"
    assert normalize_htf("1d") == "D1"
    assert normalize_htf("1440m") == "D1"

    assert timeframe_ms("H4") == 14_400_000
    assert timeframe_ms("H8") == 28_800_000
    assert timeframe_ms("H12") == 43_200_000
    assert timeframe_ms("D1") == 86_400_000

    assert bars_per_bucket("H4") == 16
    assert bars_per_bucket("H8") == 32
    assert bars_per_bucket("H12") == 48
    assert bars_per_bucket("D1") == 96

    with pytest.raises(ValueError, match="Khung thời gian"):
        normalize_htf("1h")


def test_utc_close_boundaries_match_lab_schedule() -> None:
    # Two full UTC days = 192 consecutive 15m bars starting 2026-01-01 00:00:00Z.
    df = _m15_series("2026-01-01T00:00:00Z", 192)
    closes_by_tf: dict[str, list[str]] = {"H4": [], "H8": [], "H12": [], "D1": []}
    triggers_by_tf: dict[str, list[str]] = {"H4": [], "H8": [], "H12": [], "D1": []}

    for row in df.itertuples(index=False):
        open_dt = pd.Timestamp(int(row.open_time), unit="ms", tz="UTC")
        close_dt = pd.Timestamp(int(row.close_time), unit="ms", tz="UTC")
        for tf in closes_by_tf:
            if is_timeframe_close(int(row.close_time), tf):
                triggers_by_tf[tf].append(open_dt.strftime("%Y-%m-%d %H:%M"))
                closes_by_tf[tf].append(close_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])

    expected_triggers = {
        "H4": [
            "2026-01-01 03:45", "2026-01-01 07:45", "2026-01-01 11:45",
            "2026-01-01 15:45", "2026-01-01 19:45", "2026-01-01 23:45",
            "2026-01-02 03:45", "2026-01-02 07:45", "2026-01-02 11:45",
            "2026-01-02 15:45", "2026-01-02 19:45", "2026-01-02 23:45",
        ],
        "H8": [
            "2026-01-01 07:45", "2026-01-01 15:45", "2026-01-01 23:45",
            "2026-01-02 07:45", "2026-01-02 15:45", "2026-01-02 23:45",
        ],
        "H12": [
            "2026-01-01 11:45", "2026-01-01 23:45",
            "2026-01-02 11:45", "2026-01-02 23:45",
        ],
        "D1": [
            "2026-01-01 23:45",
            "2026-01-02 23:45",
        ],
    }
    expected_closes = {
        "H4": [
            "2026-01-01 03:59:59.999", "2026-01-01 07:59:59.999", "2026-01-01 11:59:59.999",
            "2026-01-01 15:59:59.999", "2026-01-01 19:59:59.999", "2026-01-01 23:59:59.999",
            "2026-01-02 03:59:59.999", "2026-01-02 07:59:59.999", "2026-01-02 11:59:59.999",
            "2026-01-02 15:59:59.999", "2026-01-02 19:59:59.999", "2026-01-02 23:59:59.999",
        ],
        "H8": [
            "2026-01-01 07:59:59.999", "2026-01-01 15:59:59.999", "2026-01-01 23:59:59.999",
            "2026-01-02 07:59:59.999", "2026-01-02 15:59:59.999", "2026-01-02 23:59:59.999",
        ],
        "H12": [
            "2026-01-01 11:59:59.999", "2026-01-01 23:59:59.999",
            "2026-01-02 11:59:59.999", "2026-01-02 23:59:59.999",
        ],
        "D1": [
            "2026-01-01 23:59:59.999",
            "2026-01-02 23:59:59.999",
        ],
    }
    assert triggers_by_tf == expected_triggers
    assert closes_by_tf == expected_closes
    assert not is_timeframe_close(-1, "D1")


@pytest.mark.parametrize(
    "tf,pandas_rule,expected_count",
    [("H4", "4h", 12), ("H8", "8h", 6), ("H12", "12h", 4), ("D1", "1D", 2)],
)
def test_resampler_ohlcv_matches_pandas_and_supports_both_input_formats(
    tf: str,
    pandas_rule: str,
    expected_count: int,
) -> None:
    df_col = _m15_series("2026-01-01T00:00:00Z", 192, with_open_time_col=True)
    df_idx = _m15_series("2026-01-01T00:00:00Z", 192, with_open_time_col=False)
    df_col_orig = df_col.copy(deep=True)
    df_idx_orig = df_idx.copy(deep=True)

    # Shuffle input rows to verify resampler sorts chronologically without mutating input.
    shuffled = df_col.sample(frac=1.0, random_state=42).reset_index(drop=True)
    out_col = resample_m15_closed(shuffled, tf)
    out_idx = resample_m15_closed(df_idx, tf)
    ref = live_resample_ohlcv(df_col, pandas_rule)

    pd.testing.assert_frame_equal(df_col, df_col_orig)
    pd.testing.assert_frame_equal(df_idx, df_idx_orig)
    pd.testing.assert_frame_equal(out_col, out_idx)
    pd.testing.assert_frame_equal(out_col, ref, check_names=False, check_freq=False)
    assert len(out_col) == expected_count

    last_open_ms = int(out_col.index[-1].value // 1_000_000)
    assert latest_closed_htf_close_ms(out_col, tf) == bar_close_ms(last_open_ms, tf)


def test_incomplete_leading_trailing_and_gapped_buckets_are_dropped() -> None:
    # Start at 01:15 UTC (leading H8 00:00 bucket missing 5 bars), span two days,
    # and end at 2026-01-02 23:30 UTC (trailing H8/H12/D1 bucket missing 23:45 bar).
    # Total span: 01:15 on day1 -> 23:30 on day2 = 186 bars.
    df = _m15_series("2026-01-01T01:15:00Z", 186)

    # Leading H8 (00:00) and trailing H8 (16:00 day2) are incomplete -> only 4 complete H8 buckets.
    h8 = resample_m15_closed(df, "H8")
    assert [ts.strftime("%Y-%m-%d %H:%M") for ts in h8.index] == [
        "2026-01-01 08:00",
        "2026-01-01 16:00",
        "2026-01-02 00:00",
        "2026-01-02 08:00",
    ]

    # Leading H12 (00:00 day1) and trailing H12 (12:00 day2) incomplete -> 2 complete H12 buckets.
    h12 = resample_m15_closed(df, "H12")
    assert [ts.strftime("%Y-%m-%d %H:%M") for ts in h12.index] == [
        "2026-01-01 12:00",
        "2026-01-02 00:00",
    ]

    # Both D1 buckets are incomplete (day1 starts at 01:15, day2 ends at 23:30) -> empty!
    d1 = resample_m15_closed(df, "D1")
    assert d1.empty
    assert list(d1.columns) == ["open", "high", "low", "close", "volume"]
    assert latest_closed_htf_close_ms(d1, "D1") is None

    # Interior gap: remove one M15 bar (2026-01-01 10:00) from a clean 2-day stream.
    full = _m15_series("2026-01-01T00:00:00Z", 192)
    gap_ms = int(pd.Timestamp("2026-01-01T10:00:00Z").value // 1_000_000)
    gapped = full[full["open_time"] != gap_ms].copy()

    h8_gap = resample_m15_closed(gapped, "H8")
    assert len(h8_gap) == 5
    assert pd.Timestamp("2026-01-01T08:00:00Z") not in h8_gap.index

    d1_gap = resample_m15_closed(gapped, "D1")
    assert len(d1_gap) == 1
    assert list(d1_gap.index) == [pd.Timestamp("2026-01-02T00:00:00Z")]

    # Non-15m aligned timestamps are rejected explicitly.
    bad = full.iloc[:4].copy()
    bad.loc[bad.index[0], "open_time"] = int(bad.iloc[0]["open_time"]) + 60_000
    with pytest.raises(ValueError, match="15 phút"):
        resample_m15_closed(bad, "H8")


def test_timeframe_close_gate_emits_once_per_close_boundary_only() -> None:
    # 2 full days + 31 bars of day 3 (ends at 07:30 UTC, just before 07:45 close)
    stream = _m15_series("2026-01-01T00:00:00Z", 192 + 32)
    gate = TimeframeCloseGate()

    # 1. Mid-bucket tick (07:30 UTC -> 223 bars): rejected by gate.
    mid_df = stream.iloc[: 192 + 31]
    mid_close_ms = int(mid_df.iloc[-1]["close_time"])
    assert not should_emit_htf_signal(mid_close_ms, mid_close_ms, "H8")
    assert gate.resample_if_closed("BTC-USDT", mid_df, "H8", scope="TV10") is None

    # 2. Exact H8 close tick (07:45 UTC -> 224 bars): emits once.
    close_df = stream.iloc[: 192 + 32]
    close_ms = int(close_df.iloc[-1]["close_time"])
    res = gate.resample_if_closed("BTC-USDT", close_df, "H8", scope="TV10")
    assert res is not None
    bars, emitted_close_ms = res
    assert emitted_close_ms == close_ms
    assert latest_closed_htf_close_ms(bars, "H8") == close_ms
    assert len(bars) == 7

    # 3. Duplicate tick (e.g. watchdog after WS) or older bar: never re-emits.
    assert gate.resample_if_closed("BTC-USDT", close_df, "H8", scope="TV10") is None
    assert not gate.try_acquire("BTC-USDT", "H8", close_ms, close_ms, scope="TV10")
    prev_close_ms = int(stream.iloc[191]["close_time"])
    assert not gate.try_acquire("BTC-USDT", "H8", prev_close_ms, prev_close_ms, scope="TV10")

    # 4. Mismatched signal_close_ms vs latest_15m_close_ms is blocked.
    assert not gate.can_emit("BTC-USDT", "H8", close_ms, prev_close_ms, scope="TV12")

    # 5. Other symbol / other scope on same timestamp stays independent.
    assert gate.resample_if_closed("ETH-USDT", close_df, "H8", scope="TV14") is not None
    assert gate.resample_if_closed("BTC-USDT", close_df, "H8", scope="TV12") is not None

    # 6. Exact close tick where one child M15 bar inside the closing bucket is missing:
    #    no fake candle is produced and gate does not advance.
    gapped_close_df = close_df.drop(close_df.index[-3])
    assert gate.resample_if_closed("BTC-USDT", gapped_close_df, "H8", scope="TV_GAP") is None
    assert gate.can_emit("BTC-USDT", "H8", close_ms, close_ms, scope="TV_GAP")
