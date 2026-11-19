"""Loader and registry validator for the V3_LAB_4 research configuration (30 combos, 4 upgraded)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy.v3_lab_4 import V3Lab4Strategy

V3_LAB_4_CONFIG_PATH = Path(__file__).resolve().with_name("v3_lab_4_config.json")
V3_LAB_4_1_CONFIG_PATH = Path(__file__).resolve().with_name("v3_lab_4_1_config.json")
V3_LAB_4_ACTIVE_VERSION = "4.1.0"
V3_LAB_4_BASELINE_VERSION = "4.0.0"
V3_LAB_4_STRATEGY_ID = f"V3_LAB_4@{V3_LAB_4_ACTIVE_VERSION}"


def load_v3_lab_4_config(path: Path | None = None, version: str | None = None) -> dict[str, Any]:
    """Load and validate the versioned V3_LAB_4 configuration JSON (30 unique combos)."""
    ver = version or V3_LAB_4_ACTIVE_VERSION
    if path is not None:
        cfg_path = path
    elif ver == "4.0.0":
        cfg_path = V3_LAB_4_CONFIG_PATH
    elif ver == "4.1.0":
        cfg_path = V3_LAB_4_1_CONFIG_PATH
    else:
        raise ValueError(f"Unsupported V3_LAB_4 config version: {ver!r}")
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    if data.get("config_id") != "V3_LAB_4":
        raise ValueError(f"Expected config_id='V3_LAB_4', got {data.get('config_id')!r}")
    if path is None and data.get("version") != ver:
        raise ValueError(f"Expected version={ver!r} in {cfg_path.name}, got {data.get('version')!r}")
    combos = data.get("combos", [])
    if int(data.get("total_combos", -1)) != 30 or len(combos) != 30:
        raise ValueError(f"V3_LAB_4 must contain exactly 30 combos, got {len(combos)}")
    keys = {(c["symbol"], int(c["combo_id"])) for c in combos}
    if len(keys) != 30:
        raise ValueError(f"V3_LAB_4 contains duplicate (symbol, combo_id) entries: {len(keys)} unique")
    return data


def load_v3_lab_4_strategy(version: str | None = None) -> V3Lab4Strategy:
    """Resolve V3_LAB_4@<version> (default 4.1.0; pass 4.0.0 for rollback) and verify invariants."""
    ver = version or V3_LAB_4_ACTIVE_VERSION
    strat = StrategyRegistry.get("V3_LAB_4", ver)
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
