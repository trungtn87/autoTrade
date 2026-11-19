"""Official versioned pure candle-only strategy implementation for V3_LAB_8 (30 combos, Fixed TP/SL).

Configuration (V3_LAB_8@8.0.0):
- Inherits all 30 combo-symbol pairs from V3_LAB_7@7.0.0 (including ETH-USDT/C7, C12, C15).
- Leaves 29/30 combos strictly identical in logic and Fixed TP/SL parameters to V3_LAB_7@7.0.0.
- Replaces ONLY `ETH-USDT/C6` (`combo_id=6`) with `R2_V4_COMBINED`:
  * BUY: BODY_1.8 H0 (Layer2 SMC_VETO, Fixed TP 2.0% / SL 0.666667%, trailing OFF) + `ATR14_1H / SMA50(ATR14_1H) >= 1.2`.
  * SELL: CCI Audit SELL (`C < -100`) + `ATR14_1H / SMA50(ATR14_1H) >= 0.9`, Fixed TP 2.0% / SL 0.666667%, trailing OFF.
"""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.c6_r2_v4_combined import (
    C6_COMBO_ID,
    C6_SYMBOL,
    C6R2V4CombinedFilter,
    build_c6_atr_ratio_lookup,
    generate_c6_cci_audit_sell_signals,
)
from autotrade_v3.strategy.v3_lab_7 import V3Lab7Strategy
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv


class V3Lab8Strategy(V3Lab7Strategy):
    """Official V3_LAB_8@8.0.0 strategy (V3_LAB_7 + ETH-USDT/C6 R2_V4_COMBINED)."""

    strategy_id: str = "V3_LAB_8"
    version: str = "8.0.0"
    SUPPORTED_VERSIONS = ("8.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "8.0.0") -> None:
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

        target_syms = set(symbols) if symbols is not None else None
        if target_syms is not None and C6_SYMBOL not in target_syms:
            return base_signals

        eth_df = data.get(C6_SYMBOL)
        if eth_df is None or eth_df.empty:
            return base_signals

        eth_dt = ensure_dt(eth_df)
        eth_1h = resample_ohlcv(eth_dt, "1h")
        close_ms_1h, atr_ratio_50 = build_c6_atr_ratio_lookup(eth_1h)
        c6_filter = C6R2V4CombinedFilter(close_ms_1h, atr_ratio_50)

        out: list[Signal] = []
        for sig in base_signals:
            if sig.symbol == C6_SYMBOL and int(sig.combo_id) == C6_COMBO_ID:
                dec = c6_filter.evaluate_signal(sig)
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

        # Generate CCI-Audit SELL signals for ETH-USDT/C6 and apply C6R2V4CombinedFilter
        # (C6R2V4CombinedFilter enforces 1H indicator warmup via ~isnan(atr_ratio_50), matching R2_V4_COMBINED.)
        cfg = self.get_combo_config(C6_SYMBOL, C6_COMBO_ID)
        ep = self.get_exit_policy(C6_SYMBOL, C6_COMBO_ID)
        sell_candidates = generate_c6_cci_audit_sell_signals(
            eth_dt,
            combo_name=str(cfg.get("name", "C6")),
            tp_pct=float(cfg["tp_pct"]),
            sl_pct=float(cfg["sl_pct"]),
            strategy_id=self.strategy_id,
            exit_policy=ep,
            exit_policy_hash=ep.get_policy_hash(),
            warmup_end_ms=None,
        )
        for s_sig in sell_candidates:
            dec = c6_filter.evaluate_signal(s_sig)
            if dec.action != "KEEP":
                if only_approved:
                    continue
                s_sig = Signal(
                    **{
                        **s_sig.__dict__,
                        "approved": False,
                        "rejection_reason": dec.reason,
                    }
                )
            out.append(s_sig)

        out.sort(key=lambda s: (int(s.signal_close_ms), s.symbol, int(s.combo_id), s.side))
        return out
