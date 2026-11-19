"""Integration tests for V3_LAB_8@8.0.0 (V3_LAB_7 + ETH-USDT/C6 R2_V4_COMBINED)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.c6_r2_v4_combined import C6R2V4CombinedFilter
from autotrade_v3.lab import (
    V3_LAB_8_ACTIVE_VERSION,
    V3_LAB_8_STRATEGY_ID,
    load_v3_lab_7_strategy,
    load_v3_lab_8_config,
    load_v3_lab_8_strategy,
)
from autotrade_v3.strategy import StrategyRegistry, V3Lab8Strategy


def test_v3_lab_8_import_version_and_registry() -> None:
    strat = load_v3_lab_8_strategy()
    assert isinstance(strat, V3Lab8Strategy)
    assert strat.strategy_id == V3_LAB_8_STRATEGY_ID == "V3_LAB_8"
    assert strat.version == V3_LAB_8_ACTIVE_VERSION == "8.0.0"

    reg_strat = StrategyRegistry.get("V3_LAB_8", "8.0.0")
    assert isinstance(reg_strat, V3Lab8Strategy)


def test_v3_lab_8_30_combos_parity_with_lab_7() -> None:
    s7 = load_v3_lab_7_strategy()
    s8 = load_v3_lab_8_strategy()
    cfg8 = load_v3_lab_8_config()
    assert len(cfg8["combos"]) == 30
    for item in cfg8["combos"]:
        sym, cid = item["symbol"], int(item["combo_id"])
        assert s8.get_combo_config(sym, cid) == s7.get_combo_config(sym, cid)
    c6 = s8.get_combo_config("ETH-USDT", 6)
    assert abs(float(c6["tp_pct"]) - 0.02) < 1e-9
    assert abs(float(c6["sl_pct"]) - 0.00666667) < 1e-6


def test_c6_r2_v4_combined_filter_thresholds() -> None:
    close_ms = np.array([1000, 2000, 3000], dtype=np.int64)
    atr_ratio = np.array([0.85, 1.00, 1.25], dtype=float)
    flt = C6R2V4CombinedFilter(close_ms, atr_ratio)

    def make_sig(side: str, ts: int) -> Signal:
        return Signal(
            symbol="ETH-USDT", combo_id=6, combo_name="C6", timeframe="1h", side=side,
            signal_close_ms=ts, signal_close_dt=pd.Timestamp(ts, unit="ms", tz="UTC"),
            bar_position=0, bar_open_ms=ts - 100, entry_price=2000.0,
            tp_pct=0.02, sl_pct=0.00666667, tp_price=2040.0, sl_price=1986.67,
            approved=True, warmup_ready=True, smc_dir=0, ob_blocked=False,
            provider="V3_LAB_8", is_new6=False, signal_id=f"test_{side}_{ts}",
        )

    assert flt.evaluate_signal(make_sig("BUY", 1000)).action == "DROP"
    assert flt.evaluate_signal(make_sig("BUY", 2000)).action == "DROP"
    assert flt.evaluate_signal(make_sig("BUY", 3000)).action == "KEEP"
    assert flt.evaluate_signal(make_sig("SELL", 1000)).action == "DROP"
    assert flt.evaluate_signal(make_sig("SELL", 2000)).action == "KEEP"
    assert flt.evaluate_signal(make_sig("SELL", 3000)).action == "KEEP"
