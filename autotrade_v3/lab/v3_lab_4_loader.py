"""Loader and registry validator for the V3_LAB_4 research configuration (30 combos, 4 upgraded)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy.v3_lab_4 import V3Lab4Strategy

V3_LAB_4_CONFIG_PATH = Path(__file__).resolve().with_name("v3_lab_4_config.json")
V3_LAB_4_STRATEGY_ID = "V3_LAB_4@4.0.0"


def load_v3_lab_4_config(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the versioned V3_LAB_4 configuration JSON (30 unique combos)."""
    cfg_path = path or V3_LAB_4_CONFIG_PATH
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if data.get("config_id") != "V3_LAB_4":
        raise ValueError(f"Expected config_id='V3_LAB_4', got {data.get('config_id')!r}")
    combos = data.get("combos", [])
    if int(data.get("total_combos", -1)) != 30 or len(combos) != 30:
        raise ValueError(f"V3_LAB_4 must contain exactly 30 combos, got {len(combos)}")
    keys = {(c["symbol"], int(c["combo_id"])) for c in combos}
    if len(keys) != 30:
        raise ValueError(f"V3_LAB_4 contains duplicate (symbol, combo_id) entries: {len(keys)} unique")
    return data


def load_v3_lab_4_strategy() -> V3Lab4Strategy:
    """Resolve V3_LAB_4@4.0.0 from StrategyRegistry and verify 30-combo & Fixed TP/SL invariants."""
    strat = StrategyRegistry.get("V3_LAB_4", "4.0.0")
    if not isinstance(strat, V3Lab4Strategy):
        raise TypeError(f"Expected V3Lab4Strategy, got {type(strat)}")
    spec = strat.spec()
    if len(spec.combos) != 30:
        raise ValueError(f"V3_LAB_4 must have 30 combos, got {len(spec.combos)}")
    for c in spec.combos:
        ep = strat.get_exit_policy(c.symbol, int(c.combo_id))
        if ep.split_legs or float(ep.trailing_act_pct) != 0.0:
            raise ValueError(f"V3_LAB_4 requires Fixed TP/SL for {c.symbol}/{c.combo_id}: {ep}")
    return strat
