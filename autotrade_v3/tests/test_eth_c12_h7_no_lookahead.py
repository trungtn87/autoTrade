"""Unit tests: Zero look-ahead verification for V3Lab4Strategy ETH-USDT/C12 H7_SELL_4H_TREND_ONLY."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from autotrade_v3.strategy.v3_lab_4 import V3Lab4Strategy


@dataclass
class _DummySig:
    symbol: str
    combo_id: int
    side: str
    signal_close_ms: int


def _make_m15(start_utc: str, n_bars: int, slope: float = 0.05) -> pd.DataFrame:
    idx = pd.date_range(start_utc, periods=n_bars, freq="15min", tz="UTC")
    t = np.arange(n_bars, dtype=float)
    close = 2000.0 + slope * t
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(n_bars, 100.0),
        },
        index=idx,
    )


def test_before_4h_close_never_uses_developing_4h_bar() -> None:
    """At 00:59:59.999, 01:59:59.999, 02:59:59.999, prior closed 4H bar (bullish) is used, so SELL is blocked."""
    # Uptrend -> close_4h > ema100_4h -> SELL normally blocked
    df = _make_m15("2024-01-01 00:00:00", 1920 + 16, slope=0.1)
    # Shock 03:45 M15 bar way down so the 00:00..03:59 4H bar closes bearish
    df.loc[pd.Timestamp("2024-01-21 03:45:00", tz="UTC"), ["open", "high", "low", "close"]] = 500.0

    for hr in ["00:00:00", "01:00:00", "02:00:00"]:
        c_ms = int(pd.Timestamp(f"2024-01-21 {hr}", tz="UTC").timestamp() * 1000) + 3_600_000 - 1
        sig = _DummySig("ETH-USDT", 102, "SELL", c_ms)
        kept = V3Lab4Strategy._filter_eth_c12_h7_sell_4h_trend([sig], df)
        assert kept == [], f"Look-ahead leak at {hr}!"


def test_exact_4h_close_and_immediately_after() -> None:
    """At 03:59:59.999 (exact 4H close) and 04:59:59.999, the bearish 00:00 4H bar IS closed and allows SELL."""
    df = _make_m15("2024-01-01 00:00:00", 1920 + 32, slope=0.1)
    df.loc[pd.Timestamp("2024-01-21 03:45:00", tz="UTC"), ["open", "high", "low", "close"]] = 500.0

    for hr in ["03:00:00", "04:00:00"]:
        c_ms = int(pd.Timestamp(f"2024-01-21 {hr}", tz="UTC").timestamp() * 1000) + 3_600_000 - 1
        sig = _DummySig("ETH-USDT", 102, "SELL", c_ms)
        kept = V3Lab4Strategy._filter_eth_c12_h7_sell_4h_trend([sig], df)
        assert len(kept) == 1


def test_buy_and_other_combos_always_kept() -> None:
    """BUY on ETH/C12 and SELL on BTC/C12 are never filtered by H7."""
    df = _make_m15("2024-01-01 00:00:00", 1920 + 16, slope=0.1)
    c_ms = int(pd.Timestamp("2024-01-21 00:00:00", tz="UTC").timestamp() * 1000) + 3_600_000 - 1
    buy_eth = _DummySig("ETH-USDT", 102, "BUY", c_ms)
    sell_btc = _DummySig("BTC-USDT", 102, "SELL", c_ms)
    kept = V3Lab4Strategy._filter_eth_c12_h7_sell_4h_trend([buy_eth, sell_btc], df)
    assert kept == [buy_eth, sell_btc]


def test_missing_4h_bar_falls_back_to_prior_closed_bar() -> None:
    """If 00:00..03:59 4H bar is missing, 04:59:59.999 falls back to prior bullish 20:00 4H bar (no look-ahead)."""
    df = _make_m15("2024-01-01 00:00:00", 1920 + 48, slope=0.1)
    # Drop 00:00..03:59 and make 04:00..07:59 bearish
    mask_drop = (df.index >= pd.Timestamp("2024-01-21 00:00:00", tz="UTC")) & (
        df.index < pd.Timestamp("2024-01-21 04:00:00", tz="UTC")
    )
    df = df.loc[~mask_drop].copy()
    df.loc[pd.Timestamp("2024-01-21 07:45:00", tz="UTC"), ["open", "high", "low", "close"]] = 500.0

    c_ms = int(pd.Timestamp("2024-01-21 04:00:00", tz="UTC").timestamp() * 1000) + 3_600_000 - 1
    sig = _DummySig("ETH-USDT", 102, "SELL", c_ms)
    assert V3Lab4Strategy._filter_eth_c12_h7_sell_4h_trend([sig], df) == []


def test_insufficient_ema100_4h_warmup_rejects_sell() -> None:
    """With < 100 4H bars, ema100_4h is NaN and H7 rejects SELL."""
    df_short = _make_m15("2024-01-01 00:00:00", 800, slope=-0.1)
    c_ms = int(df_short.index[-1].timestamp() * 1000) + 900_000 - 1
    sig = _DummySig("ETH-USDT", 102, "SELL", c_ms)
    assert V3Lab4Strategy._filter_eth_c12_h7_sell_4h_trend([sig], df_short) == []
