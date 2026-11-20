"""Integration tests for V3_LAB_9@9.0.0 (V3_LAB_8 + ETH-USDT/C3 B2_NOT_OVEREXTENDED_4H)."""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.c3_b2_not_overextended_4h import C3B2NotOverextended4hFilter
from autotrade_v3.lab import (
    V3_LAB_9_ACTIVE_VERSION,
    V3_LAB_9_STRATEGY_ID,
    load_v3_lab_8_strategy,
    load_v3_lab_9_config,
    load_v3_lab_9_strategy,
)
from autotrade_v3.strategy import StrategyRegistry, V3Lab9Strategy


def test_v3_lab_9_import_version_and_registry() -> None:
    strat = load_v3_lab_9_strategy()
    assert isinstance(strat, V3Lab9Strategy)
    assert strat.strategy_id == V3_LAB_9_STRATEGY_ID == "V3_LAB_9"
    assert strat.version == V3_LAB_9_ACTIVE_VERSION == "9.0.0"

    reg_strat = StrategyRegistry.get("V3_LAB_9", "9.0.0")
    assert isinstance(reg_strat, V3Lab9Strategy)


def test_v3_lab_9_30_combos_parity_with_lab_8() -> None:
    s8 = load_v3_lab_8_strategy()
    s9 = load_v3_lab_9_strategy()
    cfg9 = load_v3_lab_9_config()
    assert len(cfg9["combos"]) == 30
    assert s9.get_spec_hash() != s8.get_spec_hash()
    assert s9.get_exit_spec_hash() != s8.get_exit_spec_hash() or s9.get_signal_spec_hash() != s8.get_signal_spec_hash()

    for item in cfg9["combos"]:
        sym, cid = item["symbol"], int(item["combo_id"])
        assert s9.get_combo_config(sym, cid) == s8.get_combo_config(sym, cid)

    c3 = s9.get_combo_config("ETH-USDT", 3)
    assert abs(float(c3["tp_pct"]) - 0.018) < 1e-9
    assert abs(float(c3["sl_pct"]) - 0.006) < 1e-9
    assert c3["layer2"] == "SMC_STRICT"


def test_c3_b2_not_overextended_4h_filter_thresholds_and_lookahead() -> None:
    # 4H closed bars at ts=1000, 2000, 3000
    close_ms_4h = np.array([1000, 2000, 3000], dtype=np.int64)
    ema_trend_4h = np.array([2.5, 3.01, -3.5], dtype=float)
    flt = C3B2NotOverextended4hFilter(close_ms_4h, ema_trend_4h)

    def make_sig(side: Literal["BUY", "SELL"], ts: int) -> Signal:
        return Signal(
            symbol="ETH-USDT",
            combo_id=3,
            combo_name="C3",
            timeframe="1h",
            side=side,
            signal_close_ms=ts,
            signal_close_dt=pd.Timestamp(ts, unit="ms", tz="UTC"),
            bar_position=0,
            bar_open_ms=ts - 100,
            entry_price=2000.0,
            tp_pct=0.018,
            sl_pct=0.006,
            tp_price=2036.0,
            sl_price=1988.0,
            approved=True,
            warmup_ready=True,
            smc_dir=0,
            ob_blocked=False,
            provider="V3_LAB_9",
            is_new6=False,
            signal_id=f"test_{side}_{ts}",
        )

    # Before any 4H bar closes -> DROP (warmup)
    assert flt.evaluate_signal(make_sig("BUY", 999)).action == "DROP"
    # At ts=1000: ema_trend_4h = +2.5 <= 3.0 -> BUY KEEP, SELL (-2.5 <= 3.0) KEEP
    assert flt.evaluate_signal(make_sig("BUY", 1000)).action == "KEEP"
    assert flt.evaluate_signal(make_sig("SELL", 1000)).action == "KEEP"
    # At ts=2000: ema_trend_4h = +3.01 > 3.0 -> BUY DROP, SELL (-3.01 <= 3.0) KEEP
    assert flt.evaluate_signal(make_sig("BUY", 2000)).action == "DROP"
    assert flt.evaluate_signal(make_sig("SELL", 2000)).action == "KEEP"
    # At ts=2999: must still read bar ts=2000 (not future bar ts=3000) -> BUY DROP
    assert flt.evaluate_signal(make_sig("BUY", 2999)).action == "DROP"
    # At ts=3000: ema_trend_4h = -3.5 -> BUY (-3.5 <= 3.0) KEEP, SELL (+3.5 > 3.0) DROP
    assert flt.evaluate_signal(make_sig("BUY", 3000)).action == "KEEP"
    assert flt.evaluate_signal(make_sig("SELL", 3000)).action == "DROP"
