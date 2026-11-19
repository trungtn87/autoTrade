"""Verification tests for V3_LAB_4_TOP23@1.0.0 (23-combo L2 handoff for V3 Live)."""
from __future__ import annotations

from autotrade_v3.lab.v3_lab_4_loader import (
    load_v3_lab_4_top23_config,
    load_v3_lab_4_top23_strategy,
)
from autotrade_v3.strategy.v3_lab_4_top23 import TOP23_COMBO_KEYS, V3Lab4Top23Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256


def test_top23_config_and_registry_match_23_combos():
    cfg = load_v3_lab_4_top23_config()
    assert cfg["total_combos"] == 23
    assert len(cfg["combos"]) == 23
    assert cfg["live_eligible"] is False
    expected_hash = canonical_json_sha256({k: v for k, v in cfg.items() if k != "config_hash"})
    assert cfg["config_hash"] == expected_hash

    keys = {c["combo_key"] for c in cfg["combos"]}
    assert keys == TOP23_COMBO_KEYS
    assert sum(1 for c in cfg["combos"] if c["symbol"] == "BTC-USDT") == 12
    assert sum(1 for c in cfg["combos"] if c["symbol"] == "ETH-USDT") == 11
    assert len(cfg["upgraded_combos"]) == 16
    assert cfg["upgraded_combos"]["ETH-USDT/C14"] == "A-R2-05_ATR_SL_ADJUSTMENT"

    strat = load_v3_lab_4_top23_strategy()
    assert isinstance(strat, V3Lab4Top23Strategy)
    assert {c.combo_key for c in strat.spec().combos} == TOP23_COMBO_KEYS
    assert hasattr(strat, "build_c15_h3_controller")
    assert hasattr(strat, "build_c14_h3a_filter")
