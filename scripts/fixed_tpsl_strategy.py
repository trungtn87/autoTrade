"""Local experiment wrapper for 20FINAL with trailing disabled (FIXED_TP_SL).

Does NOT modify 20FINAL@1 parameters, strategy_core, or V3 sim engine.
Preserves identical signal generation, TP%, SL%, and all other rules;
only disables trailing (split_legs=False, leg_weights=(1.0,), trailing_act_pct=0.0, trailing_cb_pct=0.0).
"""
from __future__ import annotations

from typing import Any

from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256, compute_spec_hash


class FixedTpSl20FinalStrategy(Baseline20FinalStrategy):
    """20FINAL variant with trailing completely disabled (100% fixed TP/SL)."""

    def __init__(self, **kwargs: Any):
        super().__init__(
            strategy_id_override="20FINAL_FIXED_TPSL",
            version_override="1.0.0",
            **kwargs,
        )

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        ep = super().get_exit_policy(symbol, combo_id)
        return ExitPolicy(
            tp_pct=ep.tp_pct,
            sl_pct=ep.sl_pct,
            split_legs=False,
            trailing_act_pct=0.0,
            trailing_cb_pct=0.0,
            leg_weights=(1.0,),
            max_bars_held=ep.max_bars_held,
            force_close_on_window_end=ep.force_close_on_window_end,
        )

    def get_exit_spec_hash(self) -> str:
        spec = self._core.spec()
        policies = {
            c.combo_key: self.get_exit_policy(c.symbol, c.combo_id).to_dict()
            for c in sorted(spec.combos, key=lambda x: x.combo_key)
        }
        return canonical_json_sha256(
            {
                "strategy_id": self.strategy_id,
                "version": self.version,
                "exit_policies": policies,
            }
        )

    def get_spec_hash(self) -> str:
        return compute_spec_hash(
            self.get_signal_spec_hash(),
            self.get_exit_spec_hash(),
            self.strategy_id,
            self.version,
        )

    def get_config_hash(self) -> str:
        return self.get_spec_hash()
