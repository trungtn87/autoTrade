"""Integration tests for V3_LAB_10@10.0.0 (V3_LAB_9 + ETH-USDT/C13 B1_VOL_REGIME)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.c13_b1_vol_regime import C13B1VolRegimeFilter
from autotrade_v3.lab import (
    V3_LAB_10_ACTIVE_VERSION,
    V3_LAB_10_STRATEGY_ID,
    load_v3_lab_9_strategy,
    load_v3_lab_10_config,
    load_v3_lab_10_strategy,
)
from autotrade_v3.strategy import StrategyRegistry, V3Lab10Strategy


def test_v3_lab_10_import_version_and_registry() -> None:
    strat = load_v3_lab_10_strategy()
    assert isinstance(strat, V3Lab10Strategy)
    assert strat.strategy_id == V3_LAB_10_STRATEGY_ID == "V3_LAB_10"
    assert strat.version == V3_LAB_10_ACTIVE_VERSION == "10.0.0"

    reg_strat = StrategyRegistry.get("V3_LAB_10", "10.0.0")
    assert isinstance(reg_strat, V3Lab10Strategy)


def test_v3_lab_10_30_combos_parity_with_lab_9() -> None:
    s9 = load_v3_lab_9_strategy()
    s10 = load_v3_lab_10_strategy()
    cfg10 = load_v3_lab_10_config()
    assert len(cfg10["combos"]) == 30
    assert s10.get_spec_hash() != s9.get_spec_hash()

    for item in cfg10["combos"]:
        sym, cid = item["symbol"], int(item["combo_id"])
        assert s10.get_combo_config(sym, cid) == s9.get_combo_config(sym, cid)

    c13 = s10.get_combo_config("ETH-USDT", 103)
    assert abs(float(c13["tp_pct"]) - 0.02) < 1e-9
    assert abs(float(c13["sl_pct"]) - 0.008) < 1e-9


def test_c13_b1_vol_regime_filter_thresholds_and_lookahead() -> None:
    close_ms_1h = np.array([1000, 2000, 3000], dtype=np.int64)
    atr_ratio_50 = np.array([1.20, 1.50, 1.51], dtype=float)
    flt = C13B1VolRegimeFilter(close_ms_1h, atr_ratio_50)

    def make_sig(ts: int) -> Signal:
        return Signal(
            symbol="ETH-USDT",
            combo_id=103,
            combo_name="C13",
            timeframe="1h",
            side="SELL",
            signal_close_ms=ts,
            signal_close_dt=pd.Timestamp(ts, unit="ms", tz="UTC"),
            bar_position=0,
            bar_open_ms=ts - 100,
            entry_price=2000.0,
            tp_pct=0.02,
            sl_pct=0.008,
            tp_price=1960.0,
            sl_price=2016.0,
            approved=True,
            warmup_ready=True,
            smc_dir=0,
            ob_blocked=False,
            provider="V3_LAB_10",
            is_new6=False,
            signal_id=f"test_SELL_{ts}",
        )

    assert flt.evaluate_signal(make_sig(999)).action == "DROP"
    assert flt.evaluate_signal(make_sig(1000)).action == "KEEP"
    assert flt.evaluate_signal(make_sig(2000)).action == "KEEP"
    assert flt.evaluate_signal(make_sig(2999)).action == "KEEP"
    assert flt.evaluate_signal(make_sig(3000)).action == "DROP"
