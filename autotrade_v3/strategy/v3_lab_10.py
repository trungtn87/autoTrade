"""Official versioned pure candle-only strategy implementation for V3_LAB_10 (30 combos, Fixed TP/SL).

Configuration (V3_LAB_10@10.0.0):
- Inherits all 30 combo-symbol pairs from V3_LAB_9@9.0.0 (including ETH-USDT/C3 B2_NOT_OVEREXTENDED_4H
  and ETH-USDT/C4 KEEP_H0).
- Leaves 29/30 combos strictly identical in logic and Fixed TP/SL parameters to V3_LAB_9@9.0.0.
- Upgrades ONLY `ETH-USDT/C13` (`combo_id=103`) from H5_SELL_ONLY_4H_TREND to `B1_VOL_REGIME`:
  * Keeps SELL-only momcross_n40_t40 + 4H EMA100 downtrend, Layer 2 OFF, Fixed TP 2.0% / SL 0.80% (no trailing).
  * Adds closed 1H candle-only filter (`close_ms_1h <= signal_close_ms`):
    `atr_ratio_50 = ATR(14) / SMA(ATR(14), 50) <= 1.50`.
"""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.btc_c4_b1_vol_confirm import (
    BTC_C4_COMBO_ID,
    BTC_C4_MIN_VOL_RATIO_20,
    BTC_C4_SYMBOL,
    BtcC4B1VolConfirmFilter,
)
from autotrade_v3.filters.btc_c7_b1_no_chase_1h import (
    BTC_C7_COMBO_ID,
    BTC_C7_MAX_EMA_TREND_1H_DIR,
    BTC_C7_SYMBOL,
    BtcC7B1NoChase1HFilter,
)
from autotrade_v3.filters.c13_b1_vol_regime import (
    C13_COMBO_ID,
    C13_MAX_ATR_RATIO_50,
    C13_SYMBOL,
    C13B1VolRegimeFilter,
)
from autotrade_v3.filters.c3_b2_not_overextended_4h import C3_MAX_EMA_TREND_4H_DIR
from autotrade_v3.strategy.eth_c1_h1_both_1 import (
    H1_BUY_MAX_SLOPE_4H_PCT,
    H1_SELL_MIN_DIST_EMA150_4H_PCT,
)
from autotrade_v3.strategy.v3_lab_9 import V3Lab9Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256


class V3Lab10Strategy(V3Lab9Strategy):
    """Official V3_LAB_10@10.0.0 strategy (V3_LAB_9 + ETH-USDT/C13 B1_VOL_REGIME)."""

    strategy_id: str = "V3_LAB_10"
    version: str = "10.0.0"
    SUPPORTED_VERSIONS = ("10.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "10.0.0") -> None:
        super().__init__(params_dir=params_dir, version=version)

    def get_signal_spec_hash(self) -> str:
        spec_dict: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "version": self._version_value,
            "eth_c1": f"H1_BOTH_1({H1_BUY_MAX_SLOPE_4H_PCT},{H1_SELL_MIN_DIST_EMA150_4H_PCT})",
            "btc_c1": "H2_BUY_VOLUME_1_8",
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
        """Generate all 30-combo V3_LAB_10 signals strictly from closed OHLCV bars."""
        base_signals = super().generate_signals(
            data=data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        eth_df = data.get(C13_SYMBOL)
        btc_df = data.get(BTC_C4_SYMBOL)

        c13_filter = C13B1VolRegimeFilter.from_m15_dataframe(eth_df) if eth_df is not None and not eth_df.empty else None
        btc_c4_filter = BtcC4B1VolConfirmFilter.from_m15_dataframe(btc_df) if btc_df is not None and not btc_df.empty else None
        btc_c7_filter = BtcC7B1NoChase1HFilter.from_m15_dataframe(btc_df) if btc_df is not None and not btc_df.empty else None

        out: list[Signal] = []
        for sig in base_signals:
            dec = None
            if c13_filter is not None and sig.symbol == C13_SYMBOL and int(sig.combo_id) == C13_COMBO_ID:
                dec = c13_filter.evaluate_signal(sig)
            elif btc_c4_filter is not None and sig.symbol == BTC_C4_SYMBOL and int(sig.combo_id) == BTC_C4_COMBO_ID:
                dec = btc_c4_filter.evaluate_signal(sig)
            elif btc_c7_filter is not None and sig.symbol == BTC_C7_SYMBOL and int(sig.combo_id) == BTC_C7_COMBO_ID:
                dec = btc_c7_filter.evaluate_signal(sig)

            if dec is not None and dec.action != "KEEP":
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
