"""Registry V3_LIVE_V3_LAB_2_V1 (20 combos taken from 20FINAL) — MOCK tests, no network.

Owner 2026-11-18: 20FINAL combo set with ETH-USDT/C1 = H1_BOTH_1 and BTC-USDT/C14 = H3a V1.
"""
from __future__ import annotations

from autotrade_v3.live.c14_h3a import h3a_spec_matches
from autotrade_v3.live.strategy_v18 import V18Engine


def test_lab2_registry_has_20_combos_from_20final():
    from autotrade_v3.live.strategy_registry import load_registry as lr
    lab2, final = lr("V3_LIVE_V3_LAB_2_V1"), lr("20FINAL")
    assert len(lab2.combos) == 20
    assert {(c.symbol, c.combo_id) for c in lab2.combos} == {(c.symbol, c.combo_id) for c in final.combos}
    c14 = lab2.get("BTC-USDT", 104)
    assert c14 is not None and h3a_spec_matches(c14)
    eth1 = lab2.get("ETH-USDT", 1)
    assert eth1.signal_variant == "H1_BOTH_1"
    assert [c.key for c in lab2.combos if c.signal_variant != "BASELINE"] == ["ETH-USDT/1"]
    V18Engine(lab2)
