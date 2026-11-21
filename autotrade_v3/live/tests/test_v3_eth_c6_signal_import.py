"""Regression for ETH-USDT/C6 CCI SELL signal construction in the live L2 module.

The candidate mask is injected to exercise the constructor. Indicator formulas,
strategy thresholds, TP/SL and live trading paths are not modified by this test.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade_v3.live import l2_filters
from autotrade_v3.live.l2_lab10 import Signal


def test_eth_c6_cci_sell_signal_constructor_is_available_at_runtime(monkeypatch) -> None:
    # Closed M15 input covering 80 complete 1H candles (enough indicator warmup).
    idx = pd.date_range("2026-01-01", periods=320, freq="15min", tz="UTC")
    close = np.linspace(100.0, 98.0, len(idx))
    m15 = pd.DataFrame(
        {
            "open": close + 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.full(len(idx), 100.0),
        },
        index=idx,
    )

    # Reproduce the failing path: one candidate ETH-C6 SELL reaches Signal(...).
    # This mocks only the event gate; all preceding OHLCV calculations still run.
    def one_sell_candidate(mask: pd.Series) -> pd.Series:
        forced = pd.Series(False, index=mask.index)
        forced.iloc[-1] = True
        return forced

    monkeypatch.setattr(l2_filters, "edge_event", one_sell_candidate)

    signals = l2_filters.generate_c6_cci_audit_sell_signals(
        m15,
        combo_name="C6",
        tp_pct=0.02,
        sl_pct=0.008,
        strategy_id="V3_LAB_10",
    )

    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, Signal)
    assert (sig.symbol, sig.combo_id, sig.side, sig.timeframe) == ("ETH-USDT", 6, "SELL", "1h")
    assert sig.signal_close_ms == int(idx[-1].value // 1_000_000) + 900_000 - 1
    assert sig.tp_price == sig.entry_price * (1.0 - sig.tp_pct)
    assert sig.sl_price == sig.entry_price * (1.0 + sig.sl_pct)
    assert sig.approved and sig.warmup_ready
