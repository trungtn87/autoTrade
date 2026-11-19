"""Unit and regression tests for ETH-USDT/C11 H3_B1_DEMARKER integration into V3_LAB_4@4.0.0."""
from __future__ import annotations

from autotrade_v3.lab import load_v3_lab_4_config, load_v3_lab_4_strategy
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry


def test_v3_lab_4_has_upgraded_combos_and_eth_c11_h3_b1():
    cfg = load_v3_lab_4_config()
    expected_pre_batch_a = {
        "ETH-USDT/C1": "H1_BOTH_1",
        "BTC-USDT/C14": "BTC-USDT_C14_H3a_KUMO_BAND_V1",
        "ETH-USDT/C15": "H3_ASYMMETRIC",
        "ETH-USDT/C10": "H3_ASYMMETRIC",
        "ETH-USDT/C11": "H3_B1_DEMARKER",
        "ETH-USDT/C12": "H7_SELL_4H_TREND_ONLY",
    }
    for k, v in expected_pre_batch_a.items():
        assert cfg["upgraded_combos"][k] == v
    by_key = {c["combo_key"]: c for c in cfg["combos"]}
    c11 = by_key["ETH-USDT/C11"]
    assert c11["execution_version"] == "H3_B1_DEMARKER"
    assert c11["research_status"] == "RESEARCH_ACCEPTED"
    assert c11["live_eligible"] is False
    assert c11["layer2"] == "SMC_STRICT_OB"
    assert c11["tp_pct"] == 0.018
    assert c11["sl_pct"] == 0.009
    assert c11["override_details"]["entry"] == {
        "indicator": "DeMarker",
        "period": 60,
        "long_min": 0.78,
        "short_max": 0.20,
    }
    assert cfg["eth_c11_policy"]["research_status"] == "RESEARCH_ACCEPTED"
    assert cfg["eth_c11_policy"]["live_eligible"] is False


def test_v3_lab_4_strategy_eth_c11_spec_and_btc_c11_unchanged():
    strat = load_v3_lab_4_strategy()
    spec = strat.spec()
    assert len(spec.combos) == 30
    by_key = {(c.symbol, int(c.combo_id)): c for c in spec.combos}
    assert len(by_key) == 30

    eth_c11 = by_key[("ETH-USDT", 101)]
    assert eth_c11.entry["period"] == 60
    assert eth_c11.entry["long_min"] == 0.78
    assert eth_c11.entry["short_max"] == 0.20
    assert eth_c11.layer2 == "SMC_STRICT_OB"
    ep_eth = strat.get_exit_policy("ETH-USDT", 101)
    assert abs(float(ep_eth.tp_pct) - 0.018) < 1e-9
    assert abs(float(ep_eth.sl_pct) - 0.009) < 1e-9
    assert float(ep_eth.trailing_act_pct) == 0.0

    # BTC-USDT/C11 remains baseline (no long_min=0.78 / short_max=0.20 override)
    btc_c11 = by_key[("BTC-USDT", 101)]
    assert btc_c11.entry.get("long_min", 0.75) == 0.75
    assert btc_c11.entry.get("short_max", 0.25) == 0.25

    # 20FINAL_FIXED_V1@1 baseline remains untouched
    base_core = StrategyCoreRegistry.get("20FINAL_FIXED_V1", "1")
    base_c11 = [c for c in base_core.spec().combos if int(c.combo_id) == 101]
    assert len(base_c11) == 1
    assert base_c11[0].entry.get("long_min", 0.75) == 0.75
    assert base_c11[0].entry.get("short_max", 0.25) == 0.25
