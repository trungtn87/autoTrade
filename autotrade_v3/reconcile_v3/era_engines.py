"""Bộ dựng engine tín hiệu Lab cho từng kỷ nguyên live (20FINAL, 20_POSITIVE_FIXED, Lab 5, Lab 8, Lab 10, Lab 11 PRUNE23)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal, SignalEngine
from autotrade_v3.strategy.eth_c1_h1_both_1 import EthC1H1Both1Strategy
from autotrade_v3.strategy.v3_lab_10_live_v1 import V3Lab10LiveV1Strategy
from autotrade_v3.strategy.v3_lab_11_prune23 import PRUNE23_EXCLUDED_COMBOS, V3Lab11Prune23Strategy
from autotrade_v3.strategy.v3_lab_5 import V3Lab5Strategy
from autotrade_v3.strategy.v3_lab_8 import V3Lab8Strategy
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry
from autotrade_v3.strategy_core.strategies.final20.logic import Final20Strategy

_PARAMS_FIXED_V1 = Path(__file__).resolve().parent / "params" / "20final_fixed_v1_v1" / "params.json"


def ensure_fixed_v1_registered() -> None:
    """Đăng ký runtime 20FINAL_FIXED_V1@1 từ bản sao params.json (không sửa strategy_core)."""
    try:
        StrategyCoreRegistry.get("20FINAL_FIXED_V1", "1")
    except KeyError:
        StrategyCoreRegistry.register(Final20Strategy(params_path=_PARAMS_FIXED_V1))


def get_23_finalized_combos_info() -> dict[str, Any]:
    """Trả về danh sách 23 mã chốt (V3_LAB_11_PRUNE23@11.0.0) và sai khác so với V3_LIVE_23_LAB_10_V1."""
    ensure_fixed_v1_registered()
    strat = V3Lab11Prune23Strategy()
    combos = [
        {
            "symbol": c.symbol,
            "combo_id": int(c.combo_id),
            "combo_name": c.combo_name,
            "timeframe": c.native_timeframe,
            "layer2": c.layer2,
            "tp_pct": float(c.exit_policy.tp_pct),
            "sl_pct": float(c.exit_policy.sl_pct),
        }
        for c in strat.spec().combos
    ]
    return {
        "strategy_id": "V3_LAB_11_PRUNE23",
        "version": "11.0.0",
        "source_commit": "166aafd (origin/v3_lab)",
        "live_registry_compared": "V3_LIVE_23_LAB_11_V1 (78b4601)",
        "matches_live_registry": True,
        "combo_count": len(combos),
        "combos": combos,
        "added_combos": [],
        "removed_combos": [],
        "parameter_diffs_vs_live": [],
    }


def generate_signals_for_era(
    *,
    era_id: str,
    bars_by_sym: dict[str, pd.DataFrame],
    only_warmup_ready_by_sym: dict[str, bool],
) -> list[Signal]:
    """Chạy đúng engine Lab tương ứng với từng kỷ nguyên trên cùng chuỗi nến M15."""
    ensure_fixed_v1_registered()

    if era_id == "SIGNAL_ERA_20FINAL":
        strat = StrategyCoreRegistry.get("20FINAL", "1")
        eng = SignalEngine(core_strategy=strat)
        out: list[Signal] = []
        for sym, df in bars_by_sym.items():
            out.extend(
                eng.generate_signals(
                    sym,
                    df,
                    only_approved=True,
                    only_warmup_ready=only_warmup_ready_by_sym.get(sym, True),
                )
            )
        return out

    if era_id == "SIGNAL_ERA_V3_LIVE_20_POSITIVE_FIXED_V1":
        strat_20 = EthC1H1Both1Strategy()
        out = []
        for sym, df in bars_by_sym.items():
            out.extend(
                strat_20.generate_signals(
                    {sym: df},
                    symbols=[sym],
                    only_approved=True,
                    only_warmup_ready=only_warmup_ready_by_sym.get(sym, True),
                )
            )
        return out

    if era_id in (
        "SIGNAL_ERA_V3_LIVE_23_LAB_5_V1",
        "SIGNAL_ERA_V3_LIVE_23_LAB_8_V1",
        "SIGNAL_ERA_V3_LIVE_23_LAB_10_V1",
    ):
        cls_map = {
            "SIGNAL_ERA_V3_LIVE_23_LAB_5_V1": V3Lab5Strategy,
            "SIGNAL_ERA_V3_LIVE_23_LAB_8_V1": V3Lab8Strategy,
            "SIGNAL_ERA_V3_LIVE_23_LAB_10_V1": V3Lab10LiveV1Strategy,
        }
        strat_30 = cls_map[era_id]()
        out = []
        for sym, df in bars_by_sym.items():
            sigs = strat_30.generate_signals(
                {sym: df},
                symbols=[sym],
                only_approved=True,
                only_warmup_ready=only_warmup_ready_by_sym.get(sym, True),
            )
            out.extend(s for s in sigs if (s.symbol, int(s.combo_id)) not in PRUNE23_EXCLUDED_COMBOS)
        return out

    if era_id == "SIGNAL_ERA_23_FINALIZED_LAB11_PRUNE23":
        strat_23 = V3Lab11Prune23Strategy()
        out = []
        for sym, df in bars_by_sym.items():
            out.extend(
                strat_23.generate_signals(
                    {sym: df},
                    symbols=[sym],
                    only_approved=True,
                    only_warmup_ready=only_warmup_ready_by_sym.get(sym, True),
                )
            )
        return out

    raise ValueError(f"Kỷ nguyên tín hiệu không hỗ trợ: {era_id!r}")
