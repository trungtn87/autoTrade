"""Unit & integration tests for V3_LAB_11@11.0.0 (V3_LAB_10 + BTC-USDT/C1 H2_ATR_1.00)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.btc_c1_h2_atr_100 import (
    BTC_C1_COMBO_ID,
    BTC_C1_MIN_ATR_RATIO_50,
    BTC_C1_SYMBOL,
    BtcC1H2Atr100Filter,
    build_btc_c1_atr_ratio_lookup,
)
from autotrade_v3.lab import (
    V3_LAB_11_ACTIVE_VERSION,
    V3_LAB_11_STRATEGY_ID,
    load_v3_lab_10_strategy,
    load_v3_lab_11_config,
    load_v3_lab_11_strategy,
)
from autotrade_v3.strategy import StrategyRegistry, V3Lab11Strategy
from autotrade_v3.strategy_core.indicators.indicators import atr


def _make_c1_signal(ts_ms: int, side: str = "BUY") -> Signal:
    return Signal(
        symbol=BTC_C1_SYMBOL,
        combo_id=BTC_C1_COMBO_ID,
        combo_name="C1",
        timeframe="1h",
        side=side,
        signal_close_ms=ts_ms,
        signal_close_dt=pd.Timestamp(ts_ms, unit="ms", tz="UTC"),
        bar_position=0,
        bar_open_ms=ts_ms - 3_599_999,
        entry_price=50000.0,
        tp_pct=0.02,
        sl_pct=0.008,
        tp_price=51000.0 if side == "BUY" else 49000.0,
        sl_price=49600.0 if side == "BUY" else 50400.0,
        approved=True,
        warmup_ready=True,
        smc_dir=1 if side == "BUY" else -1,
        ob_blocked=False,
        provider="V3_LAB_11",
        is_new6=False,
        signal_id=f"test_BTC_C1_{side}_{ts_ms}",
    )


def test_v3_lab_11_registry_and_30_combo_config_parity() -> None:
    strat = load_v3_lab_11_strategy()
    assert isinstance(strat, V3Lab11Strategy)
    assert strat.strategy_id == V3_LAB_11_STRATEGY_ID == "V3_LAB_11"
    assert strat.version == V3_LAB_11_ACTIVE_VERSION == "11.0.0"

    reg_strat = StrategyRegistry.get("V3_LAB_11", "11.0.0")
    assert isinstance(reg_strat, V3Lab11Strategy)

    s10 = load_v3_lab_10_strategy()
    cfg11 = load_v3_lab_11_config()
    assert len(cfg11["combos"]) == 30
    assert strat.get_spec_hash() != s10.get_spec_hash()

    for item in cfg11["combos"]:
        sym, cid = item["symbol"], int(item["combo_id"])
        assert strat.get_combo_config(sym, cid) == s10.get_combo_config(sym, cid)

    c1 = strat.get_combo_config("BTC-USDT", 1)
    assert c1["layer2"] == "SMC_STRICT"
    assert abs(float(c1["tp_pct"]) - 0.02) < 1e-9
    assert abs(float(c1["sl_pct"]) - 0.008) < 1e-9


def test_btc_c1_h2_atr_100_thresholds_and_no_lookahead() -> None:
    close_ms_1h = np.array([1000, 2000, 3000], dtype=np.int64)
    atr_ratio_50 = np.array([0.99, 1.00, 1.25], dtype=float)
    flt = BtcC1H2Atr100Filter(close_ms_1h, atr_ratio_50, min_atr_ratio_50=BTC_C1_MIN_ATR_RATIO_50)

    # Before first closed 1H bar -> warmup DROP
    assert flt.evaluate_signal(_make_c1_signal(999)).action == "DROP"
    # At exact close of bar 0 (0.99 < 1.00) -> DROP
    assert flt.evaluate_signal(_make_c1_signal(1000)).action == "DROP"
    # Before bar 1 closes (1999) still reads bar 0 (0.99) -> no lookahead to bar 1!
    assert flt.evaluate_signal(_make_c1_signal(1999)).action == "DROP"
    # At exact close of bar 1 (1.00 >= 1.00) -> KEEP
    assert flt.evaluate_signal(_make_c1_signal(2000)).action == "KEEP"
    # At bar 2 (1.25 >= 1.00) -> KEEP
    assert flt.evaluate_signal(_make_c1_signal(3000)).action == "KEEP"


def test_btc_c1_h2_atr_100_missing_and_nan_safe() -> None:
    # Empty / None dataframe
    flt_empty = BtcC1H2Atr100Filter.from_m15_dataframe(None)
    assert flt_empty.evaluate_signal(_make_c1_signal(5000)).action == "DROP"

    flt_empty_df = BtcC1H2Atr100Filter.from_m15_dataframe(pd.DataFrame())
    assert flt_empty_df.evaluate_signal(_make_c1_signal(5000)).action == "DROP"

    # NaN values in atr_ratio_50
    flt_nan = BtcC1H2Atr100Filter(np.array([1000, 2000], dtype=np.int64), np.array([np.nan, 1.10]))
    assert flt_nan.evaluate_signal(_make_c1_signal(1000)).action == "DROP"
    assert flt_nan.evaluate_signal(_make_c1_signal(2000)).action == "KEEP"


def test_btc_c1_h2_atr_100_exact_atr_formula_and_future_invariance() -> None:
    idx = pd.date_range("2024-01-01", periods=80 * 4, freq="15min", tz="UTC")
    rng = np.random.default_rng(42)
    closes = 40000.0 + np.cumsum(rng.normal(0.0, 50.0, size=len(idx)))
    df_m15 = pd.DataFrame({
        "open": closes - 10.0,
        "high": closes + 25.0,
        "low": closes - 25.0,
        "close": closes,
        "volume": 100.0,
    }, index=idx)

    c_ms, ratios = build_btc_c1_atr_ratio_lookup(df_m15)
    # Verify against manual 1H ATR(14)/SMA(50)
    d1 = df_m15.resample("1h", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    a14 = atr(d1, 14)
    expected = (a14 / a14.rolling(50).mean()).to_numpy(dtype=float)
    np.testing.assert_allclose(ratios, expected, equal_nan=True)

    # Future perturbation after bar 70 (1H) must not alter lookup up to bar 70
    df_mut = df_m15.copy()
    df_mut.iloc[70 * 4 :, df_mut.columns.get_loc("high")] *= 5.0
    df_mut.iloc[70 * 4 :, df_mut.columns.get_loc("low")] *= 0.2
    _, ratios_mut = build_btc_c1_atr_ratio_lookup(df_mut)
    np.testing.assert_allclose(ratios[:70], ratios_mut[:70], equal_nan=True)
