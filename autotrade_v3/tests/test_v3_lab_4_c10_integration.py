"""Unit and regression tests for V3_LAB_4 (30 combos, 4 finalized upgrades including ETH-USDT/C10 H3_ASYMMETRIC)."""
from __future__ import annotations

import pandas as pd

from autotrade_v3.lab import (
    V3_LAB_4_STRATEGY_ID,
    load_v3_lab_2_config,
    load_v3_lab_2_strategy,
    load_v3_lab_4_config,
    load_v3_lab_4_strategy,
)
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy.v3_lab_4 import V3Lab4Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256


def test_v3_lab_4_config_has_30_unique_combos_and_4_upgrades():
    cfg = load_v3_lab_4_config()
    assert cfg["config_id"] == "V3_LAB_4"
    assert cfg["version"] == "4.0.0"
    assert cfg["total_combos"] == 30
    assert cfg["live_eligible"] is False
    assert cfg["top20_live_modified"] is False

    # Verify config_hash integrity
    expected_hash = canonical_json_sha256({k: v for k, v in cfg.items() if k != "config_hash"})
    assert cfg["config_hash"] == expected_hash

    combos = cfg["combos"]
    assert len(combos) == 30
    keys = {(c["symbol"], int(c["combo_id"])) for c in combos}
    assert len(keys) == 30

    by_key = {c["combo_key"]: c for c in combos}
    assert by_key["ETH-USDT/C1"]["execution_version"] == "H1_BOTH_1"
    assert by_key["BTC-USDT/C14"]["execution_version"] == "BTC-USDT_C14_H3a_KUMO_BAND_V1"
    assert by_key["ETH-USDT/C15"]["execution_version"] == "H3_ASYMMETRIC"
    assert by_key["ETH-USDT/C10"]["execution_version"] == "H3_ASYMMETRIC"
    assert by_key["ETH-USDT/C10"]["research_status"] == "RESEARCH_ACCEPTED"
    assert by_key["ETH-USDT/C10"]["live_eligible"] is False
    assert by_key["ETH-USDT/C10"]["override_details"]["sample_size"] == "23 trades / 3 years"
    assert by_key["ETH-USDT/C10"]["tp_pct"] == 0.018
    assert by_key["ETH-USDT/C10"]["sl_pct"] == 0.009

    # BTC/C10 and remaining 17 combos remain H0 baseline (12 upgraded combos total)
    assert by_key["BTC-USDT/C10"]["execution_version"] == "20FINAL_FIXED_V1@1"
    assert by_key["BTC-USDT/C10"]["tp_pct"] == 0.01
    assert by_key["BTC-USDT/C10"]["sl_pct"] == 0.008
    h0_count = sum(1 for c in combos if c["execution_version"] == "20FINAL_FIXED_V1@1")
    assert h0_count == 15


def test_v3_lab_4_strategy_registry_and_fixed_tpsl_invariant():
    strat = load_v3_lab_4_strategy()
    assert isinstance(strat, V3Lab4Strategy)
    assert f"{strat.strategy_id}@{strat.version}" == V3_LAB_4_STRATEGY_ID
    assert StrategyRegistry.get("V3_LAB_4", "4.0.0") is strat

    spec = strat.spec()
    assert len(spec.combos) == 30
    for c in spec.combos:
        ep = strat.get_exit_policy(c.symbol, int(c.combo_id))
        assert not ep.split_legs
        assert float(ep.trailing_act_pct) == 0.0


def test_v3_lab_2_remains_untouched():
    cfg2 = load_v3_lab_2_config()
    strat2 = load_v3_lab_2_strategy()
    assert cfg2["config_id"] == "V3_LAB_2"
    assert len(cfg2["combos"]) == 20
    assert len(strat2.spec().combos) == 20


def test_eth_c10_asymmetric_sell_filter_enforces_smc_strict(monkeypatch):
    class _FakeSig:
        def __init__(self, symbol: str, combo_id: int, side: str, open_ms: int) -> None:
            self.symbol = symbol
            self.combo_id = combo_id
            self.side = side
            self.signal_close_ms = open_ms + 900_000 - 1

    idx = pd.to_datetime(["2025-01-01 00:00:00+00:00", "2025-01-01 00:15:00+00:00", "2025-01-01 00:30:00+00:00"])
    df = pd.DataFrame({"open": [1.0, 1.0, 1.0], "high": [1.1, 1.1, 1.1], "low": [0.9, 0.9, 0.9], "close": [1.0, 1.0, 1.0]}, index=idx)
    fake_smc = pd.Series([0, -1, 1], index=idx)
    monkeypatch.setattr("autotrade_v3.strategy.v3_lab_4.smc_direction", lambda *args, **kwargs: fake_smc)

    t0 = int(idx[0].timestamp() * 1000)
    t1 = int(idx[1].timestamp() * 1000)
    t2 = int(idx[2].timestamp() * 1000)
    sigs = [
        _FakeSig("ETH-USDT", 10, "BUY", t0),   # BUY allowed with smc=0 (SMC_VETO)
        _FakeSig("ETH-USDT", 10, "SELL", t0),  # SELL blocked with smc=0 (SMC_STRICT requires -1)
        _FakeSig("ETH-USDT", 10, "SELL", t1),  # SELL allowed with smc=-1 (SMC_STRICT)
        _FakeSig("BTC-USDT", 10, "SELL", t0),  # BTC/C10 untouched
        _FakeSig("ETH-USDT", 10, "BUY", t2),   # BUY allowed with smc=1
    ]
    kept = V3Lab4Strategy._filter_eth_c10_asymmetric(sigs, df)
    assert [(s.symbol, s.combo_id, s.side, s.signal_close_ms) for s in kept] == [
        ("ETH-USDT", 10, "BUY", sigs[0].signal_close_ms),
        ("ETH-USDT", 10, "SELL", sigs[2].signal_close_ms),
        ("BTC-USDT", 10, "SELL", sigs[3].signal_close_ms),
        ("ETH-USDT", 10, "BUY", sigs[4].signal_close_ms),
    ]
