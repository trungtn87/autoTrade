"""Official versioned pure candle-only strategy implementation for V3_LAB_9 (30 combos, Fixed TP/SL).

Configuration (V3_LAB_9@9.0.0):
- Inherits all 30 combo-symbol pairs from V3_LAB_8@8.0.0.
- Leaves 29/30 combos strictly identical in logic and Fixed TP/SL parameters to V3_LAB_8@8.0.0.
- Upgrades ONLY `ETH-USDT/C3` (`combo_id=3`) from H0 to `B2_NOT_OVEREXTENDED_4H`:
  * Keeps ADX_34 1H entry, Layer 2 SMC_STRICT, and Fixed TP 1.8% / SL 0.60% (no trailing).
  * Adds closed 4H candle-only filter (`close_ms_4h <= signal_close_ms`):
    `ema_trend_4h_dir <= 3.0`, where `ema_trend_4h = (EMA20_4H - EMA50_4H) / close_4H * 100`
    signed by direction (+1 BUY, -1 SELL).
"""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.c3_b2_not_overextended_4h import (
    C3_COMBO_ID,
    C3_MAX_EMA_TREND_4H_DIR,
    C3_SYMBOL,
    C3B2NotOverextended4hFilter,
)
from autotrade_v3.strategy.eth_c1_h1_both_1 import (
    H1_BUY_MAX_SLOPE_4H_PCT,
    H1_SELL_MIN_DIST_EMA150_4H_PCT,
)
from autotrade_v3.strategy.v3_lab_8 import V3Lab8Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256


class V3Lab9Strategy(V3Lab8Strategy):
    """Official V3_LAB_9@9.0.0 strategy (V3_LAB_8 + ETH-USDT/C3 B2_NOT_OVEREXTENDED_4H)."""

    strategy_id: str = "V3_LAB_9"
    version: str = "9.0.0"
    SUPPORTED_VERSIONS = ("9.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "9.0.0") -> None:
        super().__init__(params_dir=params_dir, version=version)

    def get_signal_spec_hash(self) -> str:
        spec_dict: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "version": self._version_value,
            "eth_c1": f"H1_BOTH_1({H1_BUY_MAX_SLOPE_4H_PCT},{H1_SELL_MIN_DIST_EMA150_4H_PCT})",
            "btc_c1": "H2_BUY_VOLUME_1_8",
            "eth_c3": f"B2_NOT_OVEREXTENDED_4H(ema_trend_4h_dir<={C3_MAX_EMA_TREND_4H_DIR})",
            "eth_c6": "R2_V4_COMBINED(buy>=1.2,sell>=0.9)",
            "eth_c7": "R2_V2_ASYM_BUY_VOL20_SELL_TREND",
            "btc_c11": "H1_BUY_0_78",
            "eth_c11": "H3_B1_DEMARKER",
            "eth_c12": "V3_ATR065_NO_EXT24H",
            "btc_c14": "H3a_KUMO_BAND_V1",
            "eth_c14": "A-R2-05_ATR_SL_ADJUSTMENT",
            "eth_c15": "V5_SELL_ONLY_ATR140",
            "btc_c16": "H3_VORTEX_RATIO_1_22",
        }
        return canonical_json_sha256(spec_dict)

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: Collection[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate all 30-combo V3_LAB_9 signals strictly from closed OHLCV bars."""
        base_signals = super().generate_signals(
            data=data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        eth_df = data.get(C3_SYMBOL)
        if eth_df is None or eth_df.empty:
            return base_signals

        c3_filter = C3B2NotOverextended4hFilter.from_m15_dataframe(eth_df)

        out: list[Signal] = []
        for sig in base_signals:
            if sig.symbol == C3_SYMBOL and int(sig.combo_id) == C3_COMBO_ID:
                dec = c3_filter.evaluate_signal(sig)
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
