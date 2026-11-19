"""Official versioned pure candle-only strategy implementation for V3_LAB_6 (30 combos, Fixed TP/SL).

Configuration (V3_LAB_6@6.0.0):
- Inherits all 30 combo-symbol pairs from V3_LAB_5@5.0.0 (including ETH-USDT/C15 V5_SELL_ONLY_ATR140).
- Leaves 29/30 combos strictly identical in logic and Fixed TP/SL parameters to V3_LAB_5@5.0.0.
- Replaces ONLY `ETH-USDT/C12` (`combo_id=102`) `H7_SELL_4H_TREND_ONLY` with `V3_ATR065_NO_EXT24H`:
  * Keeps H7 base logic (Aroon(40) threshold 70, Layer 2 SMC_VETO_OB, H4 SELL trend, Fixed TP 1.8% / SL 0.9%)
  * Adds closed 1H candle-only filters: `atr_pct_1h >= 0.65%` AND `ret_24h_1h <= 2.20%`.
"""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.base import FilterContext
from autotrade_v3.filters.c12_v3_atr065_no_ext24h import (
    C12_COMBO_ID,
    C12_SYMBOL,
    C12V3Atr065NoExt24hFilter,
    build_c12_1h_features_lookup,
)
from autotrade_v3.strategy.v3_lab_5 import V3Lab5Strategy
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv


class V3Lab6Strategy(V3Lab5Strategy):
    """Official V3_LAB_6@6.0.0 strategy (V3_LAB_5 + ETH-USDT/C12 V3_ATR065_NO_EXT24H)."""

    strategy_id: str = "V3_LAB_6"
    version: str = "6.0.0"
    SUPPORTED_VERSIONS: tuple[str, ...] = ("6.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "6.0.0") -> None:
        super().__init__(params_dir=params_dir, version=version)

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: Collection[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
        allowed_combos: Collection[tuple[str, int]] | None = None,
    ) -> list[Signal]:
        base_signals = super().generate_signals(
            data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
            allowed_combos=allowed_combos,
        )

        eth_df = data.get(C12_SYMBOL)
        if eth_df is None or eth_df.empty:
            return base_signals

        eth_1h = resample_ohlcv(ensure_dt(eth_df), "1h")
        close_ms, atr_pct, ret_24h = build_c12_1h_features_lookup(eth_1h)
        c12_filter = C12V3Atr065NoExt24hFilter(close_ms, atr_pct, ret_24h)

        out: list[Signal] = []
        for sig in base_signals:
            if sig.symbol == C12_SYMBOL and int(sig.combo_id) == C12_COMBO_ID:
                dec = c12_filter.evaluate(FilterContext(signal=sig))
                if not dec.allow:
                    if only_approved:
                        continue
                    sig = Signal(
                        **{
                            **sig.__dict__,
                            "approved": False,
                            "rejection_reason": dec.reason,
                        }
                    )
            out.append(sig)
        return out
