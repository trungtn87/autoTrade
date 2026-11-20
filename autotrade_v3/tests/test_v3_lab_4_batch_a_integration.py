"""Unit and regression tests for Batch A P3 6-combo integration into V3_LAB_4@4.0.0."""
from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade_v3.lab import load_v3_lab_4_config, load_v3_lab_4_strategy
from autotrade_v3.strategy.v3_lab_4 import V3Lab4Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry


def test_v3_lab_4_has_12_upgraded_combos_and_batch_a_p3():
    cfg = load_v3_lab_4_config()
    assert cfg["total_combos"] == 30
    assert len(cfg["combos"]) == 30
    expected_hash = canonical_json_sha256({k: v for k, v in cfg.items() if k != "config_hash"})
    assert cfg["config_hash"] == expected_hash
    assert cfg["live_eligible"] is False
    assert cfg["top20_live_modified"] is False

    expected_12 = {
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
    }
    assert cfg["upgraded_combos"] == expected_12

    by_key = {c["combo_key"]: c for c in cfg["combos"]}
    h0_count = sum(1 for c in cfg["combos"] if c["execution_version"] == "20FINAL_FIXED_V1@1")
    assert h0_count == 18

    batch_a_6 = {
        "BTC-USDT/C2": "H1_SELL_ONLY",
        "BTC-USDT/C4": "H1_BUY_ONLY",
        "BTC-USDT/C7": "H1_BUY_ONLY",
        "BTC-USDT/C13": "H4_SELL_ONLY_4H_TREND",
        "ETH-USDT/C13": "H5_SELL_ONLY_4H_TREND",
        "BTC-USDT/C16": "H3_VORTEX_RATIO_1_22",
    }
    for k, ver in expected_12.items():
        c = by_key[k]
        assert c["execution_version"] == ver
        assert c["trailing_enabled"] is False
        assert c["split_legs"] is False
        if k in batch_a_6:
            assert c["research_status"] == "RESEARCH_ACCEPTED"
            assert c["live_eligible"] is False

    assert by_key["BTC-USDT/C2"]["tp_pct"] == 0.012 and by_key["BTC-USDT/C2"]["sl_pct"] == 0.008
    assert by_key["BTC-USDT/C4"]["tp_pct"] == 0.018 and by_key["BTC-USDT/C4"]["sl_pct"] == 0.009
    assert by_key["BTC-USDT/C7"]["tp_pct"] == 0.02 and by_key["BTC-USDT/C7"]["sl_pct"] == 0.008
    assert by_key["BTC-USDT/C7"]["timeframe"] == "15m"
    assert by_key["BTC-USDT/C13"]["tp_pct"] == 0.02 and by_key["BTC-USDT/C13"]["sl_pct"] == 0.008
    assert by_key["ETH-USDT/C13"]["tp_pct"] == 0.02 and by_key["ETH-USDT/C13"]["sl_pct"] == 0.008
    assert by_key["BTC-USDT/C16"]["tp_pct"] == 0.011 and by_key["BTC-USDT/C16"]["sl_pct"] == 0.009
    assert by_key["ETH-USDT/C16"]["execution_version"] == "20FINAL_FIXED_V1@1"
    assert by_key["ETH-USDT/C7"]["execution_version"] == "20FINAL_FIXED_V1@1"
    assert cfg["batch_a_policy"]["portfolio_selection"] == "P3"


def test_v3_lab_4_strategy_batch_a_p3_specs_and_20final_untouched():
    strat = load_v3_lab_4_strategy()
    spec = strat.spec()
    assert len(spec.combos) == 30
    by_key = {(c.symbol, int(c.combo_id)): c for c in spec.combos}
    assert len(by_key) == 30

    btc_c16 = by_key[("BTC-USDT", 106)]
    assert btc_c16.entry["indicator"] == "Vortex"
    assert btc_c16.entry["period"] == 14
    assert btc_c16.entry["ratio"] == 1.22
    assert btc_c16.layer2 == "OB"

    eth_c16 = by_key[("ETH-USDT", 106)]
    assert eth_c16.entry.get("ratio", 1.2) == 1.2

    # 20FINAL_FIXED_V1@1 remains completely untouched
    base_core = StrategyCoreRegistry.get("20FINAL_FIXED_V1", "1")
    base_c16 = [c for c in base_core.spec().combos if int(c.combo_id) == 106]
    assert len(base_c16) == 1
    assert base_c16[0].entry.get("ratio", 1.2) == 1.2


def test_batch_a_one_sided_and_4h_no_lookahead_filters():
    class _FakeSig:
        def __init__(self, symbol: str, combo_id: int, side: str, close_ms: int) -> None:
            self.symbol = symbol
            self.combo_id = combo_id
            self.side = side
            self.signal_close_ms = close_ms

    sigs = [
        _FakeSig("BTC-USDT", 2, "BUY", 1000),
        _FakeSig("BTC-USDT", 2, "SELL", 1000),
        _FakeSig("BTC-USDT", 4, "BUY", 1000),
        _FakeSig("BTC-USDT", 4, "SELL", 1000),
        _FakeSig("BTC-USDT", 7, "BUY", 1000),
        _FakeSig("BTC-USDT", 7, "SELL", 1000),
        _FakeSig("BTC-USDT", 103, "BUY", 1000),
        _FakeSig("BTC-USDT", 103, "SELL", 1000),
        _FakeSig("ETH-USDT", 103, "BUY", 1000),
        _FakeSig("ETH-USDT", 103, "SELL", 1000),
        _FakeSig("ETH-USDT", 2, "BUY", 1000),
    ]
    kept = V3Lab4Strategy._filter_batch_a_one_sided(sigs)
    assert [(s.symbol, s.combo_id, s.side) for s in kept] == [
        ("BTC-USDT", 2, "SELL"),
        ("BTC-USDT", 4, "BUY"),
        ("BTC-USDT", 7, "BUY"),
        ("BTC-USDT", 103, "SELL"),
        ("ETH-USDT", 103, "SELL"),
        ("ETH-USDT", 2, "BUY"),
    ]

    # Fail-closed 4H filter when insufficient 4H bars (<100)
    idx = pd.date_range("2025-01-01", periods=200, freq="15min", tz="UTC")
    df_short = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10.0},
        index=idx,
    )
    c_ms = int(idx[-1].timestamp() * 1000) + 900_000 - 1
    out = V3Lab4Strategy._filter_4h_sell_downtrend([_FakeSig("BTC-USDT", 103, "SELL", c_ms)], df_short, "BTC-USDT", 103)
    assert out == []
