"""Official versioned pure candle-only strategy implementation for V3_LAB_7 (30 combos, Fixed TP/SL).

Configuration (V3_LAB_7@7.0.0):
- Inherits all 30 combo-symbol pairs from V3_LAB_6@6.0.0 (including ETH-USDT/C12 and ETH-USDT/C15).
- Leaves 29/30 combos strictly identical in logic and Fixed TP/SL parameters to V3_LAB_6@6.0.0.
- Replaces ONLY `ETH-USDT/C7` (`combo_id=7`) with `R2_V2_ASYM_BUY_VOL20_SELL_TREND`:
  * BUY: keeps H0 conditions (BODY1.3, Layer2 OB, Fixed TP 1.8% / SL 0.9%) with `vol_mult_15m >= 2.0`.
  * SELL: keeps H0 conditions (`vol_mult_15m >= 1.5`, BODY1.3, Layer2 OB, Fixed TP 1.8% / SL 0.9%) + `ema_trend_1h <= 0.0`.
"""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.c7_r2_v2_asym import (
    C7_COMBO_ID,
    C7_SYMBOL,
    C7R2V2AsymFilter,
    build_c7_features_lookup,
)
from autotrade_v3.strategy.v3_lab_6 import V3Lab6Strategy
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv


class V3Lab7Strategy(V3Lab6Strategy):
    """Official V3_LAB_7@7.0.0 strategy (V3_LAB_6 + ETH-USDT/C7 R2_V2_ASYM_BUY_VOL20_SELL_TREND)."""

    strategy_id: str = "V3_LAB_7"
    version: str = "7.0.0"
    SUPPORTED_VERSIONS: tuple[str, ...] = ("7.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "7.0.0") -> None:
        super().__init__(params_dir=params_dir, version=version)

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: Collection[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        base_signals = super().generate_signals(
            data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        eth_df = data.get(C7_SYMBOL)
        if eth_df is None or eth_df.empty:
            return base_signals

        eth_dt = ensure_dt(eth_df)
        eth_15m = resample_ohlcv(eth_dt, "15m") if len(eth_dt) > 0 else eth_dt
        eth_1h = resample_ohlcv(eth_dt, "1h")
        c15_ms, vol_mult_15m, c1h_ms, ema_trend_1h = build_c7_features_lookup(eth_15m, eth_1h)
        c7_filter = C7R2V2AsymFilter(c15_ms, vol_mult_15m, c1h_ms, ema_trend_1h)

        out: list[Signal] = []
        for sig in base_signals:
            if sig.symbol == C7_SYMBOL and int(sig.combo_id) == C7_COMBO_ID:
                dec = c7_filter.evaluate_signal(sig)
                if dec.action != "KEEP":
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
