"""Loader and registry validator for the V3_LAB_6 research configuration (30 combos, C12 V3_ATR065_NO_EXT24H)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy.v3_lab_6 import V3Lab6Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256

V3_LAB_6_CONFIG_PATH = Path(__file__).resolve().with_name("v3_lab_6_config.json")
V3_LAB_6_ACTIVE_VERSION = "6.0.0"
V3_LAB_6_STRATEGY_ID = f"V3_LAB_6@{V3_LAB_6_ACTIVE_VERSION}"


def load_v3_lab_6_config(path: Path | None = None, version: str | None = None) -> dict[str, Any]:
    """Load and validate the versioned V3_LAB_6 configuration JSON (30 unique combos)."""
    ver = version or V3_LAB_6_ACTIVE_VERSION
    if ver != V3_LAB_6_ACTIVE_VERSION:
        raise ValueError(f"Unsupported V3_LAB_6 config version: {ver!r}")

    cfg_path = path if path is not None else V3_LAB_6_CONFIG_PATH
    data: dict[str, Any] = json.loads(cfg_path.read_text(encoding="utf-8"))

    if data.get("config_id") != "V3_LAB_6":
        raise ValueError(f"Expected config_id='V3_LAB_6', got {data.get('config_id')!r}")
    if data.get("version") != ver:
        raise ValueError(f"Expected version={ver!r} in {cfg_path.name}, got {data.get('version')!r}")

    combos = data.get("combos", [])
    if int(data.get("total_combos", -1)) != 30 or len(combos) != 30:
        raise ValueError(f"V3_LAB_6 must contain exactly 30 combos, got {len(combos)}")

    c12_combo = next((c for c in combos if c["symbol"] == "ETH-USDT" and int(c["combo_id"]) == 102), None)
    if c12_combo is None or c12_combo.get("execution_version") != "V3_ATR065_NO_EXT24H":
        raise ValueError("ETH-USDT/C12 combo entry in V3_LAB_6 config must use execution_version='V3_ATR065_NO_EXT24H'")

    expected_cfg_hash = canonical_json_sha256({k: v for k, v in data.items() if k != "config_hash"})
    if str(data.get("config_hash", "")) != expected_cfg_hash:
        raise ValueError(f"V3_LAB_6 config_hash mismatch: expected {expected_cfg_hash}, got {data.get('config_hash')!r}")
    return data


def load_v3_lab_6_strategy(version: str | None = None) -> V3Lab6Strategy:
    """Resolve V3_LAB_6@6.0.0 from StrategyRegistry."""
    ver = version or V3_LAB_6_ACTIVE_VERSION
    load_v3_lab_6_config(version=ver)
    strat = StrategyRegistry.get("V3_LAB_6", version=ver)
    if not isinstance(strat, V3Lab6Strategy):
        raise TypeError(f"Expected V3Lab6Strategy, got {type(strat)!r}")
    return strat
