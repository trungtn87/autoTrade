"""V3_30_FIXED_TPSL_C1_H1 (v1.0.0): Default 30-combo-symbol Fixed TP/SL + C1-ETH H1_BOTH_1 strategy."""
from __future__ import annotations

import copy
import dataclasses

from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry
from scripts.p3_c1_research_strategy import P3C1ResearchStrategy


def _build_30_combos_core():
    base_core = copy.deepcopy(StrategyCoreRegistry.get("20FINAL", "1"))
    base_spec = base_core.spec()
    combos = list(base_spec.combos)
    existing_pairs = {(c.combo_id, c.symbol) for c in combos}

    by_id = {}
    for c in combos:
        by_id.setdefault(c.combo_id, []).append(c)

    for cid in sorted(by_id.keys()):
        items = by_id[cid]
        if len(items) == 1:
            src = items[0]
            target_sym = "ETH-USDT" if src.symbol == "BTC-USDT" else "BTC-USDT"
            if (cid, target_sym) not in existing_pairs:
                cloned = copy.deepcopy(src)
                if hasattr(cloned, "__dataclass_fields__"):
                    cloned = dataclasses.replace(cloned, symbol=target_sym, combo_key=f"{target_sym}/C{cid}")
                else:
                    cloned.symbol = target_sym
                    cloned.combo_key = f"{target_sym}/C{cid}"
                combos.append(cloned)

    new_spec = dataclasses.replace(base_spec, strategy_id="V3_30_FIXED_TPSL_C1_H1", combos=tuple(combos))
    base_core._spec = new_spec
    return base_core


class V330FixedTpSlC1H1Strategy(Baseline20FinalStrategy):
    """Default 30-combo-symbol Fixed TP/SL strategy with ETH-USDT/C1 = H1_BOTH_1."""

    def __init__(self):
        core = _build_30_combos_core()
        super().__init__(
            core_strategy=core,
            strategy_id_override="V3_30_FIXED_TPSL_C1_H1",
            version_override="1.0.0",
        )
        self._h1_both_1 = P3C1ResearchStrategy(
            variant_id="H1_BOTH_1",
            buy_max_slope_4h_pct=1.5,
            sell_min_dist_ema150_4h_pct=-6.0,
            atr_tpsl_config=None,
        )

    def generate_signals(self, *args, **kwargs):
        all_sigs = super().generate_signals(*args, **kwargs)
        kept = [s for s in all_sigs if not (s.symbol == "ETH-USDT" and s.combo_id == 1)]
        h1_kwargs = dict(kwargs)
        h1_kwargs["symbols"] = ["ETH-USDT"]
        h1_sigs = self._h1_both_1.generate_signals(*args, **h1_kwargs)
        c1_eth = [s for s in h1_sigs if s.symbol == "ETH-USDT" and s.combo_id == 1]
        combined = kept + c1_eth
        combined.sort(key=lambda s: (s.signal_close_ms, s.symbol, s.combo_id))
        return combined

    def get_exit_policy(self, symbol: str, combo_id: int):
        if symbol == "ETH-USDT" and combo_id == 1:
            return self._h1_both_1.get_exit_policy(symbol, combo_id)
        ep = super().get_exit_policy(symbol, combo_id)
        return dataclasses.replace(
            ep,
            split_legs=False,
            trailing_act_pct=None,
            trailing_cb_pct=None,
            leg_weights=(1.0,),
        )
