"""Loader and registry validator for the V3_LAB_5 research configuration (30 combos, C15 V5_SELL_ONLY_ATR140)."""
from __future__ import annotations

from collections.abc import Collection
import json
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy.v3_lab_5 import V3Lab5Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256

V3_LAB_5_CONFIG_PATH = Path(__file__).resolve().with_name("v3_lab_5_config.json")
V3_LAB_5_ACTIVE_VERSION = "5.0.0"
V3_LAB_5_STRATEGY_ID = f"V3_LAB_5@{V3_LAB_5_ACTIVE_VERSION}"


def load_v3_lab_5_config(path: Path | None = None, version: str | None = None) -> dict[str, Any]:
    """Load and validate the versioned V3_LAB_5 configuration JSON (30 unique combos)."""
    ver = version or V3_LAB_5_ACTIVE_VERSION
    if ver != V3_LAB_5_ACTIVE_VERSION:
        raise ValueError(f"Unsupported V3_LAB_5 config version: {ver!r}")

    cfg_path = path if path is not None else V3_LAB_5_CONFIG_PATH
    data: dict[str, Any] = json.loads(cfg_path.read_text(encoding="utf-8"))

    if data.get("config_id") != "V3_LAB_5":
        raise ValueError(f"Expected config_id='V3_LAB_5', got {data.get('config_id')!r}")
    if data.get("version") != ver:
        raise ValueError(f"Expected version={ver!r} in {cfg_path.name}, got {data.get('version')!r}")

    combos = data.get("combos", [])
    if int(data.get("total_combos", -1)) != 30 or len(combos) != 30:
        raise ValueError(f"V3_LAB_5 must contain exactly 30 combos, got {len(combos)}")
    keys = {(c["symbol"], int(c["combo_id"])) for c in combos}
    if len(keys) != 30:
        raise ValueError(f"V3_LAB_5 contains duplicate (symbol, combo_id) entries: {len(keys)} unique")

    c15_combo = next((c for c in combos if c["symbol"] == "ETH-USDT" and int(c["combo_id"]) == 105), None)
    if c15_combo is None or c15_combo.get("execution_version") != "V5_SELL_ONLY_ATR140":
        raise ValueError("ETH-USDT/C15 combo entry in V3_LAB_5 config must use execution_version='V5_SELL_ONLY_ATR140'")

    c15_override = data.get("overrides", {}).get("ETH-USDT/C15", {})
    if c15_override.get("execution_version") != "V5_SELL_ONLY_ATR140":
        raise ValueError("overrides['ETH-USDT/C15'] in V3_LAB_5 config must use execution_version='V5_SELL_ONLY_ATR140'")

    expected_cfg_hash = canonical_json_sha256({k: v for k, v in data.items() if k != "config_hash"})
    if str(data.get("config_hash", "")) != expected_cfg_hash:
        raise ValueError(
            f"V3_LAB_5 config_hash mismatch: expected {expected_cfg_hash}, got {data.get('config_hash')!r}"
        )
    return data


def load_v3_lab_5_strategy(version: str | None = None) -> V3Lab5Strategy:
    """Resolve V3_LAB_5@5.0.0 from StrategyRegistry and verify all 30-combo Fixed TP/SL invariants."""
    ver = version or V3_LAB_5_ACTIVE_VERSION
    strat = StrategyRegistry.get("V3_LAB_5", ver)
    if not isinstance(strat, V3Lab5Strategy):
        raise TypeError(f"Expected V3Lab5Strategy, got {type(strat)}")
    spec = strat.spec()
    if len(spec.combos) != 30:
        raise ValueError(f"V3_LAB_5 must have 30 combos, got {len(spec.combos)}")
    for c in spec.combos:
        ep = strat.get_exit_policy(c.symbol, int(c.combo_id))
        if ep.split_legs or float(ep.trailing_act_pct) != 0.0:
            raise ValueError(f"V3_LAB_5 requires Fixed TP/SL for {c.symbol}/{c.combo_id}: {ep}")
    return strat


def generate_v3_lab_5_signals(
    data: dict[str, pd.DataFrame],
    *,
    symbols: list[str] | None = None,
    allowed_combos: Collection[tuple[str, int]] | None = None,
    only_approved: bool = True,
    only_warmup_ready: bool = True,
    version: str | None = None,
) -> list[Signal]:
    """Convenience entrypoint loading V3_LAB_5@5.0.0 and generating pure candle-only L2 signals."""
    strat = load_v3_lab_5_strategy(version=version)
    return strat.generate_signals_for_combos(
        data=data,
        allowed_combos=allowed_combos,
        symbols=symbols,
        only_approved=only_approved,
        only_warmup_ready=only_warmup_ready,
    )
