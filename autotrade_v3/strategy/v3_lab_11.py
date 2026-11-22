"""Official versioned pure candle-only strategy implementation for V3_LAB_11 (30 combos, Fixed TP/SL).

Configuration (V3_LAB_11@11.0.0):
- Inherits all 30 combo-symbol pairs from V3_LAB_10@10.0.0 (including BTC-USDT/C4 B1_VOL_CONFIRM
  and BTC-USDT/C7 B1_NO_CHASE_1H).
- Leaves 29/30 combos strictly identical in logic and Fixed TP/SL parameters to V3_LAB_10@10.0.0.
- Upgrades ONLY `BTC-USDT/C1` (`combo_id=1`) with `H2_ATR_1.00`:
  * Keeps MFI_L50_S50 + H2_BUY_VOLUME_1_8, 1H timeframe, Layer 2 SMC_STRICT, Fixed TP 2.0% / SL 0.80%.
  * Adds closed 1H candle-only filter (`close_ms_1h <= signal_close_ms`):
    `atr_ratio_50 = ATR(14) / SMA(ATR(14), 50) >= 1.00`.
"""
from __future__ import annotations

from collections.abc import Collection
import dataclasses
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.btc_c1_h2_atr_100 import (
    BTC_C1_COMBO_ID,
    BTC_C1_MIN_ATR_RATIO_50,
    BTC_C1_SYMBOL,
    BtcC1H2Atr100Filter,
)
from autotrade_v3.filters.btc_c4_b1_vol_confirm import (
    BTC_C4_MIN_VOL_RATIO_20,
)
from autotrade_v3.filters.btc_c7_b1_no_chase_1h import (
    BTC_C7_MAX_EMA_TREND_1H_DIR,
)
from autotrade_v3.filters.c13_b1_vol_regime import C13_MAX_ATR_RATIO_50
from autotrade_v3.filters.c3_b2_not_overextended_4h import C3_MAX_EMA_TREND_4H_DIR
from autotrade_v3.strategy.eth_c1_h1_both_1 import (
    H1_BUY_MAX_SLOPE_4H_PCT,
    H1_SELL_MIN_DIST_EMA150_4H_PCT,
)
from autotrade_v3.strategy.v3_lab_10 import V3Lab10Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256


class V3Lab11Strategy(V3Lab10Strategy):
    """Official V3_LAB_11@11.0.0 strategy (V3_LAB_10 + BTC-USDT/C1 H2_ATR_1.00)."""

    strategy_id: str = "V3_LAB_11"
    version: str = "11.0.0"
    SUPPORTED_VERSIONS = ("11.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "11.0.0") -> None:
        super().__init__(params_dir=params_dir, version=version)

    def get_signal_spec_hash(self) -> str:
        spec_dict: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "version": self._version_value,
            "eth_c1": f"H1_BOTH_1({H1_BUY_MAX_SLOPE_4H_PCT},{H1_SELL_MIN_DIST_EMA150_4H_PCT})",
            "btc_c1": f"H2_BUY_VOLUME_1_8+H2_ATR_1_00(atr_ratio_50>={BTC_C1_MIN_ATR_RATIO_50})",
            "eth_c3": f"B2_NOT_OVEREXTENDED_4H(ema_trend_4h_dir<={C3_MAX_EMA_TREND_4H_DIR})",
            "btc_c4": f"B1_VOL_CONFIRM(vol_ratio_20>={BTC_C4_MIN_VOL_RATIO_20})",
            "eth_c6": "R2_V4_COMBINED(buy>=1.2,sell>=0.9)",
            "btc_c7": f"B1_NO_CHASE_1H(ema_trend_1h_dir<={BTC_C7_MAX_EMA_TREND_1H_DIR})",
            "eth_c7": "R2_V2_ASYM_BUY_VOL20_SELL_TREND",
            "btc_c11": "H1_BUY_0_78",
            "eth_c11": "H3_B1_DEMARKER",
            "eth_c12": "V3_ATR065_NO_EXT24H",
            "eth_c13": f"B1_VOL_REGIME(atr_ratio_50<={C13_MAX_ATR_RATIO_50})",
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
        """Generate all 30-combo V3_LAB_11 signals strictly from closed OHLCV bars."""
        base_signals = super().generate_signals(
            data=data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        btc_df = data.get(BTC_C1_SYMBOL)
        if btc_df is None or btc_df.empty:
            return base_signals

        c1_filter = BtcC1H2Atr100Filter.from_m15_dataframe(btc_df)
        out: list[Signal] = []
        for sig in base_signals:
            if sig.symbol == BTC_C1_SYMBOL and int(sig.combo_id) == BTC_C1_COMBO_ID:
                dec = c1_filter.evaluate_signal(sig)
                if dec.action != "KEEP":
                    if only_approved:
                        continue
                    sig = dataclasses.replace(sig, approved=False)
            out.append(sig)
        return out
