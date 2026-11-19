"""V3_LIVE_23_LAB_8_V1: registry, Lab 8 wiring, ETH-C6 filter thresholds and SELL mapping.

Offline checks only. Real 3-year M1 parity (ETH-C6 36 BUY / 81 SELL) is NOT reproduced here; see the PR.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.filters.c6_r2_v4_combined import C6R2V4CombinedFilter
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.live.l2_lab5 import BTC, ETH, M15_MS, Lab5Engine, to_lab_frame
from autotrade_v3.live.strategy_registry import REGISTRY_LAB5_23, REGISTRY_LAB8_23, load_registry
from autotrade_v3.lab.v3_lab_8_loader import load_v3_lab_8_strategy

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "autotrade_v3" / "tests" / "fixtures" / "v3_lab_5_l2_parity_fixture.json"
EXCLUDED = {("BTC-USDT", 3), ("BTC-USDT", 102), ("BTC-USDT", 105),
            ("ETH-USDT", 2), ("ETH-USDT", 9), ("ETH-USDT", 106), ("ETH-USDT", 11)}

_REG8 = load_registry(REGISTRY_LAB8_23)
_REG5 = load_registry(REGISTRY_LAB5_23)
_ALLOWED8 = {(c.symbol, c.combo_id) for c in _REG8.combos}


def test_lab8_registry_is_the_same_23_as_lab5():
    keys8 = {(c.symbol, c.combo_id) for c in _REG8.combos}
    assert len(_REG8.combos) == 23 and len(keys8) == 23
    assert keys8 == {(c.symbol, c.combo_id) for c in _REG5.combos}
    assert keys8.isdisjoint(EXCLUDED)


def test_lab8_registry_tp_sl_match_lab8_runtime_config():
    strat = load_v3_lab_8_strategy()
    for c in _REG8.combos:
        cfg = strat.get_combo_config(c.symbol, c.combo_id)
        assert c.tp_pct == pytest.approx(float(cfg["tp_pct"])), (c.symbol, c.combo_id)
        assert c.sl_pct == pytest.approx(float(cfg["sl_pct"])), (c.symbol, c.combo_id)
        assert c.layer2 == str(cfg["layer2"]), (c.symbol, c.combo_id)


def test_lab8_engine_restricted_to_23_on_fixture_and_matches_lab5_there():
    """Fixture has no ETH-C6/C7/C12 drops, so L5 and L8 agree here. This is NOT real-data parity."""
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    frames = {}
    for sym in (BTC, ETH):
        o = fx["ohlcv_m15"][sym]
        df = pd.DataFrame({"open_time": np.asarray(o["timestamps_open_ms"], dtype="int64"),
                           "open": o["open"], "high": o["high"], "low": o["low"],
                           "close": o["close"], "volume": o["volume"]})
        frames[sym] = to_lab_frame(df)
    s8 = load_v3_lab_8_strategy()
    got8 = s8.generate_signals_for_combos(frames, allowed_combos=_ALLOWED8, only_approved=True, only_warmup_ready=True)
    assert all((s.symbol, int(s.combo_id)) in _ALLOWED8 for s in got8)
    assert not any((s.symbol, int(s.combo_id)) in EXCLUDED for s in got8)
    from autotrade_v3.lab.v3_lab_5_loader import load_v3_lab_5_strategy
    got5 = load_v3_lab_5_strategy().generate_signals_for_combos(frames, allowed_combos=_ALLOWED8,
                                                               only_approved=True, only_warmup_ready=True)
    assert {s.signal_id for s in got8} == {s.signal_id for s in got5}


def test_c6_r2_v4_combined_filter_thresholds():
    close_ms = np.array([1000, 2000, 3000], dtype=np.int64)
    atr_ratio = np.array([0.85, 1.00, 1.25], dtype=float)
    flt = C6R2V4CombinedFilter(close_ms, atr_ratio)

    def make_sig(side, ts):
        return Signal(
            symbol="ETH-USDT", combo_id=6, combo_name="C6", timeframe="1h", side=side,
            signal_close_ms=ts, signal_close_dt=pd.Timestamp(ts, unit="ms", tz="UTC"),
            bar_position=0, bar_open_ms=ts - 100, entry_price=2000.0,
            tp_pct=0.02, sl_pct=0.00666667, tp_price=2040.0, sl_price=1986.67,
            approved=True, warmup_ready=True, smc_dir=0, ob_blocked=False,
            provider="V3_LAB_8", is_new6=False, signal_id=f"test_{side}_{ts}",
        )

    assert flt.evaluate_signal(make_sig("BUY", 1000)).action == "DROP"
    assert flt.evaluate_signal(make_sig("BUY", 2000)).action == "DROP"


def test_eth_c6_sell_maps_to_v3signal_with_fixed_tp_sl():
    btc = to_lab_frame(pd.DataFrame({"open_time": 1_700_000_000_000 + np.arange(300) * M15_MS,
                                     "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 1.0}))
    eth = btc.copy()
    close_ms = int(eth.index[-1].value // 1_000_000) + M15_MS - 1
    sig = SimpleNamespace(symbol="ETH-USDT", combo_id=6, combo_name="C6", timeframe="1h", side="SELL",
                          signal_close_ms=close_ms, entry_price=2000.0, tp_pct=0.02, sl_pct=0.00666667,
                          tp_price=1960.0, sl_price=2013.33, exit_policy_hash="x")
    fake = SimpleNamespace(get_spec_hash=lambda: "fake", spec=lambda: SimpleNamespace(combos=tuple(
        SimpleNamespace(symbol=s, combo_id=c) for s, c in sorted(_ALLOWED8))),
        generate_signals_for_combos=lambda *a, **k: [sig])
    out = Lab5Engine(_REG8, strategy=fake).calculate(ETH, {BTC: btc, ETH: eth})
    assert len(out) == 1
    v = out[0]
    assert (v.symbol, v.combo, v.side) == ("ETH-USDT", 6, "SELL")
    assert v.entry == 2000.0 and v.tp == 1960.0 and v.sl == 2013.33
    assert v.tp_pct == 0.02 and v.sl_pct == pytest.approx(0.00666667)
    assert v.event_id == f"ETH-USDT|1h|C6|SELL|{close_ms}"
    assert v.registry_version == _REG8.version
