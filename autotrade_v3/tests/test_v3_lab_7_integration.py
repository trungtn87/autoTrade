"""Integration tests for V3_LAB_7@7.0.0 (V3_LAB_6 + ETH-USDT/C7 R2_V2_ASYM_BUY_VOL20_SELL_TREND)."""
from __future__ import annotations

from autotrade_v3.lab import (
    V3_LAB_7_ACTIVE_VERSION,
    V3_LAB_7_STRATEGY_ID,
    load_v3_lab_6_strategy,
    load_v3_lab_7_config,
    load_v3_lab_7_strategy,
)
from autotrade_v3.strategy import V3Lab7Strategy


def test_v3_lab_7_import_and_version() -> None:
    strat = load_v3_lab_7_strategy()
    assert isinstance(strat, V3Lab7Strategy)
    assert strat.strategy_id == V3_LAB_7_STRATEGY_ID == "V3_LAB_7"
    assert strat.version == V3_LAB_7_ACTIVE_VERSION == "7.0.0"


def test_v3_lab_7_30_combos_parity_with_lab_6() -> None:
    s6 = load_v3_lab_6_strategy()
    s7 = load_v3_lab_7_strategy()
    cfg7 = load_v3_lab_7_config()
    assert len(cfg7["combos"]) == 30
    for item in cfg7["combos"]:
        sym, cid = item["symbol"], int(item["combo_id"])
        assert s7.get_combo_config(sym, cid) == s6.get_combo_config(sym, cid)
