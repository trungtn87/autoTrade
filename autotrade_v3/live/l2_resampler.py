"""UTC higher-timeframe resampler (H4, H8, H12, D1 from closed 15m candles) and close-boundary gate.

Follows the exact lab bucketing rules used by PLUS44 / TV21:
* Pure UTC epoch bucketing: ``bucket_open_ms = (open_ms // tf_ms) * tf_ms``.
* Complete-bucket requirement: a bucket is emitted ONLY when all ``tf_minutes // 15``
  consecutive closed 15m child candles are present (partial leading/trailing or
  gapped interior buckets are dropped without generating synthetic candles).
* Close-boundary gate: a signal on timeframe ``tf`` may emit at most once when
  ``(latest_15m_close_ms + 1) % tf_ms == 0`` and ``signal_close_ms == latest_15m_close_ms``.
"""
from __future__ import annotations

import threading

import numpy as np
import pandas as pd

M15_MS = 15 * 60_000
OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")

TIMEFRAME_MINUTES: dict[str, int] = {
    "H4": 240,
    "H8": 480,
    "H12": 720,
    "D1": 1440,
}

_TF_ALIASES: dict[str, str] = {
    "4H": "H4",
    "H4": "H4",
    "240": "H4",
    "240M": "H4",
    "8H": "H8",
    "H8": "H8",
    "480": "H8",
    "480M": "H8",
    "12H": "H12",
    "H12": "H12",
    "720": "H12",
    "720M": "H12",
    "1D": "D1",
    "D1": "D1",
    "1440": "D1",
    "1440M": "D1",
}


def normalize_htf(timeframe: str) -> str:
    """Normalize timeframe tokens (e.g. '8h', 'H8', '480') to canonical H4/H8/H12/D1."""
    key = str(timeframe).strip().upper()
    canonical = _TF_ALIASES.get(key)
    if canonical is None:
        raise ValueError(f"Khung thời gian không hỗ trợ cho HTF resampler: {timeframe!r}")
    return canonical


def timeframe_ms(timeframe: str) -> int:
    """Duration of one candle in milliseconds."""
    return TIMEFRAME_MINUTES[normalize_htf(timeframe)] * 60_000


def bars_per_bucket(timeframe: str) -> int:
    """Expected number of 15m child candles in one complete HTF bucket."""
    return TIMEFRAME_MINUTES[normalize_htf(timeframe)] // 15


def bar_close_ms(open_ms: int, timeframe: str) -> int:
    """Inclusive close timestamp (ms, ending in .999) for a bucket starting at ``open_ms``."""
    return int(open_ms) + timeframe_ms(timeframe) - 1


def _empty_htf_frame() -> pd.DataFrame:
    empty_idx = pd.DatetimeIndex([], tz="UTC").as_unit("ns")
    return pd.DataFrame(
        {col: pd.Series(dtype="float64") for col in OHLCV_COLUMNS},
        index=empty_idx,
    )


def _extract_open_ms(candles: pd.DataFrame) -> np.ndarray:
    """Return 15m open_time (ms, int64) and verify 15m UTC alignment without mutating input."""
    if "open_time" in candles.columns:
        open_ms = candles["open_time"].to_numpy(dtype="int64")
    elif isinstance(candles.index, pd.DatetimeIndex):
        idx = candles.index
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        open_ms = (idx.as_unit("ns").asi8 // 1_000_000).astype("int64")
    else:
        raise ValueError("M15 candles phải có cột open_time hoặc DatetimeIndex")

    if open_ms.size and (open_ms % M15_MS != 0).any():
        bad = int(open_ms[open_ms % M15_MS != 0][0])
        raise ValueError(f"M15 open_time không nằm đúng mốc 15 phút UTC: {bad}")
    return open_ms


def resample_m15_closed(candles: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample closed 15m candles into complete UTC H4/H8/H12/D1 OHLCV bars.

    Incomplete buckets (fewer than ``tf_minutes // 15`` unique 15m child bars)
    are dropped so no synthetic or lookahead candles are produced.
    """
    tf = normalize_htf(timeframe)
    if candles is None or candles.empty:
        return _empty_htf_frame()

    open_ms = _extract_open_ms(candles)
    work = candles.loc[:, list(OHLCV_COLUMNS)].astype("float64").copy()
    work["_open_ms"] = open_ms
    work = work.sort_values("_open_ms", kind="mergesort")
    work = work.drop_duplicates(subset=["_open_ms"], keep="last")

    tf_ms = timeframe_ms(tf)
    expected_bars = bars_per_bucket(tf)
    work["_bucket_ms"] = (work["_open_ms"].to_numpy(dtype="int64") // tf_ms) * tf_ms

    grouped = work.groupby("_bucket_ms", sort=True)
    counts = grouped.size()
    complete_buckets = counts[counts == expected_bars].index
    if len(complete_buckets) == 0:
        return _empty_htf_frame()

    result = grouped.agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).loc[complete_buckets, list(OHLCV_COLUMNS)].astype("float64")
    result.index = pd.DatetimeIndex(
        pd.to_datetime(result.index.astype("int64"), unit="ms", utc=True)
    ).as_unit("ns")
    result.index.name = None
    return result


def is_timeframe_close(latest_15m_close_ms: int, timeframe: str) -> bool:
    """True iff ``latest_15m_close_ms`` marks the exact UTC close of a ``timeframe`` candle."""
    tf_ms = timeframe_ms(timeframe)
    close_ms = int(latest_15m_close_ms)
    return close_ms >= 0 and (close_ms + 1) % tf_ms == 0


def latest_closed_htf_close_ms(htf_bars: pd.DataFrame, timeframe: str) -> int | None:
    """Return inclusive close timestamp (ms) of the last complete HTF bar, or None."""
    if htf_bars is None or htf_bars.empty:
        return None
    idx = pd.DatetimeIndex(htf_bars.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    last_open_ms = int(idx.as_unit("ns").asi8[-1] // 1_000_000)
    return bar_close_ms(last_open_ms, timeframe)


def should_emit_htf_signal(
    latest_15m_close_ms: int,
    signal_close_ms: int,
    timeframe: str,
) -> bool:
    """Gate helper: signal may emit only on the exact HTF close tick matching latest M15 close."""
    return (
        is_timeframe_close(latest_15m_close_ms, timeframe)
        and int(signal_close_ms) == int(latest_15m_close_ms)
    )


class TimeframeCloseGate:
    """Thread-safe once-per-bar gate for HTF (H4/H8/H12/D1) evaluation and emission."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_emitted: dict[tuple[str, str, str], int] = {}

    def can_emit(
        self,
        symbol: str,
        timeframe: str,
        latest_15m_close_ms: int,
        signal_close_ms: int,
        *,
        scope: str = "",
    ) -> bool:
        tf = normalize_htf(timeframe)
        if not should_emit_htf_signal(latest_15m_close_ms, signal_close_ms, tf):
            return False
        key = (str(symbol).upper(), tf, str(scope))
        with self._lock:
            return int(signal_close_ms) > self._last_emitted.get(key, -1)

    def try_acquire(
        self,
        symbol: str,
        timeframe: str,
        latest_15m_close_ms: int,
        signal_close_ms: int,
        *,
        scope: str = "",
    ) -> bool:
        """Reserve emission for ``(symbol, timeframe, scope, signal_close_ms)`` once only."""
        tf = normalize_htf(timeframe)
        if not should_emit_htf_signal(latest_15m_close_ms, signal_close_ms, tf):
            return False
        key = (str(symbol).upper(), tf, str(scope))
        close_ms = int(signal_close_ms)
        with self._lock:
            if close_ms <= self._last_emitted.get(key, -1):
                return False
            self._last_emitted[key] = close_ms
            return True

    def resample_if_closed(
        self,
        symbol: str,
        candles_15m: pd.DataFrame,
        timeframe: str,
        *,
        scope: str = "",
    ) -> tuple[pd.DataFrame, int] | None:
        """Resample ``candles_15m`` and acquire the emit-once gate iff the HTF bucket just closed.

        Returns ``(htf_bars, latest_15m_close_ms)`` on the exact close tick when the closing
        bucket has all child 15m candles; otherwise returns ``None``.
        """
        tf = normalize_htf(timeframe)
        if candles_15m is None or candles_15m.empty:
            return None
        open_ms = _extract_open_ms(candles_15m)
        latest_15m_close_ms = int(open_ms.max()) + M15_MS - 1
        if not is_timeframe_close(latest_15m_close_ms, tf):
            return None
        htf_bars = resample_m15_closed(candles_15m, tf)
        htf_close_ms = latest_closed_htf_close_ms(htf_bars, tf)
        if htf_close_ms is None or htf_close_ms != latest_15m_close_ms:
            return None
        if not self.try_acquire(symbol, tf, latest_15m_close_ms, htf_close_ms, scope=scope):
            return None
        return htf_bars, latest_15m_close_ms
