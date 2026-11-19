"""Unit and regression tests for Batch C P2 3-combo integration into V3_LAB_4@4.0.0."""
from __future__ import annotations

from autotrade_v3.lab import load_v3_lab_4_config, load_v3_lab_4_strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry


def test_v3_lab_4_has_15_upgraded_combos_and_batch_c_p2():
    cfg = load_v3_lab_4_config()
    assert cfg["total_combos"] == 30
    assert len(cfg["combos"]) == 30
    expected_hash = canonical_json_sha256({k: v for k, v in cfg.items() if k != "config_hash"})
    assert cfg["config_hash"] == expected_hash
    assert cfg["live_eligible"] is False
    assert cfg["top20_live_modified"] is False

    expected_15 = {
        "ETH-USDT/C1": "H1_BOTH_1",
        "BTC-USDT/C14": "BTC-USDT_C14_H3a_KUMO_BAND_V1",
        "ETH-USDT/C15": "H3_ASYMMETRIC",
        "ETH-USDT/C10": "H3_ASYMMETRIC",
        "ETH-USDT/C11": "H3_B1_DEMARKER",
        "ETH-USDT/C12": "H7_SELL_4H_TREND_ONLY",
        "BTC-USDT/C2": "H1_SELL_ONLY",
        "BTC-USDT/C4": "H1_BUY_ONLY",
        "BTC-USDT/C7": "H1_BUY_ONLY",
        "BTC-USDT/C13": "H4_SELL_ONLY_4H_TREND",
        "ETH-USDT/C13": "H5_SELL_ONLY_4H_TREND",
        "BTC-USDT/C16": "H3_VORTEX_RATIO_1_22",
        "BTC-USDT/C1": "H2_BUY_VOLUME_1_8",
        "BTC-USDT/C11": "H1_BUY_0_78",
        "ETH-USDT/C7": "H1_BUY_VOLUME_1_7",
    }
    assert cfg["upgraded_combos"] == expected_15
    assert cfg["batch_c_policy"]["portfolio_selection"] == "P2"

    by_key = {c["combo_key"]: c for c in cfg["combos"]}
    h0_count = sum(1 for c in cfg["combos"] if c["execution_version"] == "20FINAL_FIXED_V1@1")
    assert h0_count == 15

    # ETH-USDT/C3 and BTC-USDT/C10 remain H0
    assert by_key["ETH-USDT/C3"]["execution_version"] == "20FINAL_FIXED_V1@1"
    assert by_key["BTC-USDT/C10"]["execution_version"] == "20FINAL_FIXED_V1@1"

    # Fixed TP/SL untouched
    assert by_key["BTC-USDT/C1"]["tp_pct"] == 0.02 and by_key["BTC-USDT/C1"]["sl_pct"] == 0.008
    assert by_key["BTC-USDT/C11"]["tp_pct"] == 0.018 and by_key["BTC-USDT/C11"]["sl_pct"] == 0.009
    assert by_key["ETH-USDT/C7"]["tp_pct"] == 0.018 and by_key["ETH-USDT/C7"]["sl_pct"] == 0.009
    for c in cfg["combos"]:
        assert c["trailing_enabled"] is False
        assert c["split_legs"] is False


def test_v3_lab_4_strategy_batch_c_p2_specs_and_20final_untouched():
    strat = load_v3_lab_4_strategy()
    spec = strat.spec()
    assert len(spec.combos) == 30
    by_key = {(c.symbol, int(c.combo_id)): c for c in spec.combos}
    assert len(by_key) == 30

    btc_c11 = by_key[("BTC-USDT", 101)]
    assert btc_c11.entry["indicator"] == "DeMarker"
    assert btc_c11.entry["period"] == 60
    assert btc_c11.entry["long_min"] == 0.78
    assert btc_c11.entry["short_max"] == 0.25
    assert btc_c11.layer2 == "SMC_STRICT_OB"

    # 20FINAL_FIXED_V1@1 remains completely untouched
    base_core = StrategyCoreRegistry.get("20FINAL_FIXED_V1", "1")
    base_c11 = [c for c in base_core.spec().combos if int(c.combo_id) == 101]
    assert len(base_c11) == 1
    assert base_c11[0].entry["long_min"] == 0.75
