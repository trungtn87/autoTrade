"""Standard 20FINAL baseline strategy implementation for AutoTrade V3 Research Lab.

Encapsulates all 20 combos (14 FINAL14 M15 combos + 6 NEW6 H1 combos) exactly as frozen
in V2/V3-02 with no parameter tweaking or overfitting.
"""
from __future__ import annotations

import dataclasses
from typing import Any
import pandas as pd

from autotrade_v3.engine.signal_engine import Signal, SignalEngine
from autotrade_v3.strategy.base import BaseStrategy, ExitPolicy


class Baseline20FinalStrategy(BaseStrategy):
    """20FINAL Baseline Strategy (Strategy ID: '20FINAL_BASELINE', Version: '1.0.0')."""

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config=config or {})
        self._engine = SignalEngine()

    @property
    def strategy_id(self) -> str:
        return "20FINAL_BASELINE"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def required_timeframes(self) -> list[str]:
        return ["15m", "1h"]

    def calculate_indicators(self, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
        """Indicator pass-through; indicators are evaluated dynamically during signal replay."""
        return {"status": "CALCULATED_ONLINE", "symbols": list(data.keys())}

    def get_combo_config(self, symbol: str, combo_id: int) -> dict[str, Any]:
        return self._engine.get_combo_config(symbol, combo_id)

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        cfg = self.get_combo_config(symbol, combo_id)
        is_new6 = cfg.get("is_new6", False)
        # FINAL14 uses 50% TP + 50% Trailing. NEW6 uses 100% hard single leg.
        return ExitPolicy(
            tp_pct=float(cfg["tp_pct"]),
            sl_pct=float(cfg["sl_pct"]),
            split_legs=not is_new6,
            trailing_act_pct=0.005,
            trailing_cb_pct=0.005,
            leg_weights=(0.5, 0.5) if not is_new6 else (1.0,),
        )

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate signals across provided symbols.
        
        Args:
            data: dict mapping symbol (e.g. 'BTC-USDT') to M15 DataFrame.
            symbols: optional subset of symbols to evaluate.
            only_approved: filter for Layer 2 approved signals (SMC/OB/Veto).
            only_warmup_ready: filter for signals after 3,400 M15 bar warmup.
        """
        target_symbols = symbols or list(data.keys())
        all_signals: list[Signal] = []

        for sym in target_symbols:
            if sym not in data:
                continue
            df_m15 = data[sym]
            sigs = self._engine.generate_signals(
                symbol=sym,
                df_m15=df_m15,
                only_approved=only_approved,
                only_warmup_ready=only_warmup_ready,
            )
            for s in sigs:
                ep = self.get_exit_policy(sym, s.combo_id)
                enriched = dataclasses.replace(
                    s,
                    exit_policy=ep,
                    exit_policy_hash=ep.get_policy_hash(),
                )
                all_signals.append(enriched)

        all_signals.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return all_signals
