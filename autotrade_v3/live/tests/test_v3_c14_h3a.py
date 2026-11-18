"""BTC-USDT/C14 H3a V1 admission (Kumo Thickness Band) — unit tests, no network.

Locked rule: keep iff 0.005 <= abs(span_a - span_b) / close < 0.010; C14 only.
Registry V3_LIVE_20_POSITIVE_FIXED_V1 has no combo 104, so the default engine is unaffected.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from autotrade_v2.app.strategy.final14_exact_strategy import _ichimoku
from autotrade_v3.live.c14_h3a import (
    H3A_MAX_KUMO_EXCLUSIVE,
    H3A_MIN_KUMO_INCLUSIVE,
    H3A_SL_PCT,
    H3A_TP_PCT,
    h3a_keep,
    h3a_kumo_pct,
    h3a_spec_matches,
    is_h3a_combo,
)
from autotrade_v3.live.strategy_registry import ComboSpec, StrategyRegistry, load_registry
from autotrade_v3.live.strategy_v18 import V18Engine


def _c14(**over) -> ComboSpec:
    base = dict(symbol="BTC-USDT", combo_id=104, native_timeframe="1h", is_new6=False,
                entry_variant="tkcross_12_30_60", entry={"indicator": "Ichimoku"}, layer2="OB",
                tp_pct=H3A_TP_PCT, sl_pct=H3A_SL_PCT)
    base.update(over)
    return ComboSpec(**base)


def test_band_boundaries():
    assert h3a_keep(0.005) is True            # inclusive lower bound
    assert h3a_keep(0.0099999) is True
    assert h3a_keep(0.010) is False           # exclusive upper bound
    assert h3a_keep(0.00499999) is False
    assert h3a_keep(float("nan")) is False    # fail closed
    assert h3a_keep(None) is False
    assert (H3A_MIN_KUMO_INCLUSIVE, H3A_MAX_KUMO_EXCLUSIVE) == (0.005, 0.010)


def test_only_btc_c14_is_targeted():
    assert is_h3a_combo("BTC-USDT", 104) is True
    assert is_h3a_combo("btc-usdt", 104) is True
    assert is_h3a_combo("ETH-USDT", 104) is False
    assert is_h3a_combo("BTC-USDT", 101) is False


def test_spec_guard_locked_parameters():
    assert h3a_spec_matches(_c14()) is True
    assert h3a_spec_matches(_c14(tp_pct=0.02, sl_pct=0.008)) is False
    assert h3a_spec_matches(_c14(layer2="OB_STRICT")) is False
    assert h3a_spec_matches(_c14(entry_variant="cross_n14_r1.2")) is False


def test_engine_refuses_registry_with_mismatched_c14():
    base = load_registry("V3_LIVE_20_POSITIVE_FIXED_V1")
    bad = StrategyRegistry(version="TEST_BAD_C14", combos=base.combos + (_c14(sl_pct=0.008),),
                           new6_signal_mode=base.new6_signal_mode, h1_both_1=base.h1_both_1)
    with pytest.raises(ValueError, match="H3a"):
        V18Engine(bad)


def test_default_registry_has_no_c14_so_filter_is_inert():
    reg = load_registry("V3_LIVE_20_POSITIVE_FIXED_V1")
    assert reg.get("BTC-USDT", 104) is None
    assert len(reg.combos) == 20
    V18Engine(reg)  # constructs without error


def test_kumo_pct_matches_manual_formula_on_last_bar():
    rng = np.random.default_rng(7)
    n = 150
    close = 60_000 + np.cumsum(rng.normal(0, 80, n))
    high = close + np.abs(rng.normal(0, 40, n))
    low = close - np.abs(rng.normal(0, 40, n))
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    d1 = pd.DataFrame({"open": close, "high": high, "low": low, "close": close}, index=idx)

    got = h3a_kumo_pct(d1)
    _, _, sa, sb = _ichimoku(high, low, 12, 30, 60)
    expect = np.abs(sa[-1] - sb[-1]) / close[-1]
    assert math.isclose(got.iloc[-1], expect, rel_tol=1e-12)
    assert got.index.equals(idx)
    # warm-up bars without full Ichimoku history are NaN and fail closed
    assert h3a_keep(got.iloc[0]) is False
