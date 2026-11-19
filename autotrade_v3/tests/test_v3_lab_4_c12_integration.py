"""Unit and regression tests for ETH-USDT/C12 H7_SELL_4H_TREND_ONLY integration into V3_LAB_4@4.0.0."""
from __future__ import annotations

from autotrade_v3.lab import load_v3_lab_4_config, load_v3_lab_4_strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry


def test_v3_lab_4_has_6_upgraded_combos_and_eth_c12_h7():
    cfg = load_v3_lab_4_config()
    assert cfg["total_combos"] == 30
    assert len(cfg["combos"]) == 30
    expected_hash = canonical_json_sha256({k: v for k, v in cfg.items() if k != "config_hash"})
    assert cfg["config_hash"] == expected_hash

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
    c12 = by_key["ETH-USDT/C12"]
    assert c12["execution_version"] == "H7_SELL_4H_TREND_ONLY"
    assert c12["research_status"] == "RESEARCH_ACCEPTED"
    assert c12["live_eligible"] is False
    assert c12["layer2"] == "SMC_VETO_OB"
    assert c12["tp_pct"] == 0.018
    assert c12["sl_pct"] == 0.009
    assert c12["trailing_enabled"] is False
    assert cfg["eth_c12_policy"]["research_status"] == "RESEARCH_ACCEPTED"
    assert cfg["eth_c12_policy"]["live_eligible"] is False

    # BTC-USDT/C12 remains H0 baseline
    btc_c12 = by_key["BTC-USDT/C12"]
    assert btc_c12["execution_version"] == "20FINAL_FIXED_V1@1"
    assert btc_c12["tp_pct"] == 0.018
    assert btc_c12["sl_pct"] == 0.009


def test_v3_lab_4_strategy_eth_c12_spec_and_20final_unchanged():
    strat = load_v3_lab_4_strategy()
    spec = strat.spec()
    assert len(spec.combos) == 30
    by_key = {(c.symbol, int(c.combo_id)): c for c in spec.combos}
    assert len(by_key) == 30

    eth_c12 = by_key[("ETH-USDT", 102)]
    assert eth_c12.entry["indicator"] == "Aroon"
    assert eth_c12.entry["period"] == 40
    assert eth_c12.entry["threshold"] == 70.0
    assert eth_c12.layer2 == "SMC_VETO_OB"
    ep_eth = strat.get_exit_policy("ETH-USDT", 102)
    assert abs(float(ep_eth.tp_pct) - 0.018) < 1e-9
    assert abs(float(ep_eth.sl_pct) - 0.009) < 1e-9
    assert float(ep_eth.trailing_act_pct) == 0.0

    # 20FINAL_FIXED_V1@1 baseline remains untouched
    base_core = StrategyCoreRegistry.get("20FINAL_FIXED_V1", "1")
    base_c12 = [c for c in base_core.spec().combos if int(c.combo_id) == 102]
    assert len(base_c12) == 1
    assert base_c12[0].layer2 == "SMC_VETO_OB"
    assert base_c12[0].entry["threshold"] == 70.0
