"""BTC filters ported from v3_lab (V3_LAB_11_PRUNE23): C1 H2_ATR_1.00, C4 B1_VOL_CONFIRM, C7 B1_NO_CHASE_1H.

Deterministic lookup arrays are injected directly, so the test checks thresholds and closed-bar lookup
semantics (last 1H bar whose close_ms <= signal close_ms) without depending on market data.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from autotrade_v3.live.l2_filters import (
    BTC_C4_MIN_VOL_RATIO_20,
    BTC_C7_MAX_EMA_TREND_1H_DIR,
    BtcC1H2Atr100Filter,
    BtcC4B1VolConfirmFilter,
    BtcC7B1NoChase1HFilter,
)

CLOSE_MS = np.array([1_000, 2_000, 3_000], dtype=np.int64)


def _sig(ms: int, side: str = "BUY") -> SimpleNamespace:
    return SimpleNamespace(signal_close_ms=ms, side=side)


def test_c1_keeps_only_when_closed_1h_atr_ratio_at_least_1():
    flt = BtcC1H2Atr100Filter(CLOSE_MS, np.array([np.nan, 0.99, 1.00]))
    assert flt.evaluate_signal(_sig(500)).action == "DROP"        # no closed 1H bar yet
    assert flt.evaluate_signal(_sig(1_500)).action == "DROP"      # NaN warmup bar
    assert flt.evaluate_signal(_sig(2_500)).action == "DROP"      # 0.99 < 1.00
    assert flt.evaluate_signal(_sig(3_000)).action == "KEEP"      # 1.00 >= 1.00
    assert flt.evaluate_signal(_sig(3_000, "SELL")).action == "KEEP"  # applies to both sides


def test_c4_requires_closed_1h_volume_ratio_at_least_threshold():
    flt = BtcC4B1VolConfirmFilter(CLOSE_MS, np.array([1.0, BTC_C4_MIN_VOL_RATIO_20 - 1e-9, BTC_C4_MIN_VOL_RATIO_20]))
    assert flt.evaluate_signal(_sig(2_500)).action == "DROP"
    assert flt.evaluate_signal(_sig(3_000)).action == "KEEP"


def test_c7_blocks_directional_1h_overextension_only():
    flt = BtcC7B1NoChase1HFilter(CLOSE_MS, np.array([0.0, 0.0, BTC_C7_MAX_EMA_TREND_1H_DIR + 0.1]))
    # Positive trend: BUY is overextended (blocked), SELL is with the move on the other side (kept).
    assert flt.evaluate_signal(_sig(3_000, "BUY")).action == "DROP"
    assert flt.evaluate_signal(_sig(3_000, "SELL")).action == "KEEP"
    assert flt.evaluate_signal(_sig(2_000, "BUY")).action == "KEEP"
