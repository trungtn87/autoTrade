"""Standard 20FINAL baseline strategy implementation for AutoTrade V3 Research Lab.

Delegates all indicator computation, signal generation, and hashing to the independent
`autotrade_v3.strategy_core` package while preserving full 23-column `Signal` schema
compatibility for the V3 execution simulator and run ledger.
"""
from __future__ import annotations

import dataclasses
from typing import Any
import pandas as pd

from autotrade_v3.engine.signal_engine import Signal, SignalEngine
from autotrade_v3.strategy.base import BaseStrategy, ExitPolicy
from autotrade_v3.strategy_core.api import SignalEvent, Strategy, StrategySpec
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry


class Baseline20FinalStrategy(BaseStrategy):
    """20FINAL Strategy adapter bridging Strategy Core to the V3 Research Lab."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        core_strategy: Strategy | None = None,
        strategy_id_override: str | None = None,
        version_override: str | None = None,
    ):
        self._core = core_strategy or StrategyCoreRegistry.get("20FINAL", "1")
        spec = self._core.spec()
        merged_cfg = dict(config or {})
        if not merged_cfg:
            merged_cfg = {
                "combos": [c.to_dict() for c in spec.combos],
                "warmup_bars": spec.warmup_bars,
                "timeframes": list(spec.timeframes),
            }
        super().__init__(config=merged_cfg)
        self._engine = SignalEngine(core_strategy=self._core)
        self._strategy_id_override = strategy_id_override
        self._version_override = version_override

    @property
    def core_strategy(self) -> Strategy:
        return self._core

    def spec(self) -> StrategySpec:
        return self._core.spec()

    def generate(
        self,
        bars: dict[str, pd.DataFrame] | pd.DataFrame,
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[SignalEvent]:
        return self._core.generate(
            bars=bars,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

    def on_bar_close(
        self,
        state: Any,
        bar: Any,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[SignalEvent]:
        return self._core.on_bar_close(
            state=state,
            bar=bar,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

    @property
    def strategy_id(self) -> str:
        if self._strategy_id_override is not None:
            return self._strategy_id_override
        sp = self._core.spec()
        if sp.strategy_id == "20FINAL" and sp.version == "1":
            return "20FINAL_BASELINE"
        return sp.strategy_id

    @property
    def version(self) -> str:
        if self._version_override is not None:
            return self._version_override
        sp = self._core.spec()
        if sp.strategy_id == "20FINAL" and sp.version == "1" and self._strategy_id_override != "20FINAL":
            return "1.0.0"
        return sp.version

    @property
    def required_timeframes(self) -> list[str]:
        return list(self._core.spec().timeframes)

    def get_signal_spec_hash(self) -> str:
        return self._core.spec().signal_spec_hash

    def get_exit_spec_hash(self) -> str:
        return self._core.spec().exit_spec_hash

    def get_spec_hash(self) -> str:
        return self._core.spec().spec_hash

    def get_config_hash(self) -> str:
        return self._core.spec().spec_hash

    def calculate_indicators(self, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
        """Indicator pass-through; indicators are evaluated dynamically during signal replay."""
        return {"status": "CALCULATED_ONLINE", "symbols": list(data.keys())}

    def get_combo_config(self, symbol: str, combo_id: int) -> dict[str, Any]:
        return self._engine.get_combo_config(symbol, combo_id)

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        c_spec = self._engine.get_combo_spec(symbol, combo_id)
        ep = c_spec.exit_policy
        return ExitPolicy(
            tp_pct=float(ep.tp_pct),
            sl_pct=float(ep.sl_pct),
            split_legs=bool(ep.split_legs),
            trailing_act_pct=float(ep.trailing_act_pct),
            trailing_cb_pct=float(ep.trailing_cb_pct),
            leg_weights=tuple(float(w) for w in ep.leg_weights),
            max_bars_held=ep.max_bars_held,
            force_close_on_window_end=bool(ep.force_close_on_window_end),
        )

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate signals across provided symbols."""
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
