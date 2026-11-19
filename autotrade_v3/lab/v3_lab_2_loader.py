"""Loader and registry validator for the official V3_LAB_2 research configuration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy.v3_lab_2 import V3Lab2Strategy

V3_LAB_2_CONFIG_PATH = Path(__file__).resolve().with_name("v3_lab_2_config.json")
DEFAULT_RESEARCH_STRATEGY_ID = "V3_LAB_2@2.0.0"


def load_v3_lab_2_config(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the versioned V3_LAB_2 configuration JSON."""
    cfg_path = path or V3_LAB_2_CONFIG_PATH
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if data.get("config_id") != "V3_LAB_2":
        raise ValueError(f"Expected config_id='V3_LAB_2', got {data.get('config_id')!r}")
    if int(data.get("total_combos", -1)) != 20 or len(data.get("combos", [])) != 20:
        raise ValueError(f"V3_LAB_2 must contain exactly 20 combos, got {len(data.get('combos', []))}")
    return data


def load_v3_lab_2_strategy() -> V3Lab2Strategy:
    """Resolve V3_LAB_2@2.0.0 from StrategyRegistry and verify combo/exit invariants."""
    strat = StrategyRegistry.get("V3_LAB_2", "2.0.0")
    if not isinstance(strat, V3Lab2Strategy):
        raise TypeError(f"Expected V3Lab2Strategy, got {type(strat)}")
    spec = strat.spec()
    if len(spec.combos) != 20:
        raise ValueError(f"V3_LAB_2 must have 20 combos, got {len(spec.combos)}")
    return strat
