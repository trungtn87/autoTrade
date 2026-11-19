"""V3_LAB_4_TOP23 live handoff (L2 -> lab strategy -> V3Signal) — MOCK tests, no network, no broker.

The lab strategy is not re-implemented: these tests check the registry, the wiring, the
latest-bar filter and the field mapping. Strategy output is replaced by fake lab Signals
where the test is about mapping, never about trading logic.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.live.l2_top23 import BTC, ETH, M15_MS, TOP23_VERSION, Top23Engine, to_lab_frame
from autotrade_v3.live.strategy_registry import DEFAULT_REGISTRY, REGISTRY_TOP23, load_registry

EXCLUDED = {("BTC-USDT", 3), ("BTC-USDT", 102), ("BTC-USDT", 105), ("ETH-USDT", 2), ("ETH-USDT", 9),
            ("ETH-USDT", 106), ("ETH-USDT", 11)}


def _candles(seed: int, n: int, start_ms: int = 1_700_000_100_000 // M15_MS * M15_MS, p0: float = 30_000.0):
    rng = np.random.default_rng(seed)
    close = p0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    return pd.DataFrame({"open_time": start_ms + np.arange(n) * M15_MS, "open": open_, "high": high,
                         "low": low, "close": close, "volume": np.ones(n)})


class _FakeStrategy:
    """Stands in for the lab strategy only in mapping tests."""

    def __init__(self, sigs):
        self._sigs = sigs

    def get_spec_hash(self):
        return "fake-spec-hash"

    def spec(self):
        return SimpleNamespace(combos=tuple(SimpleNamespace(symbol=s, combo_id=c)
                                            for s, c in sorted(_ALLOWED)))

    def generate_signals(self, data, symbols=None, only_approved=True, only_warmup_ready=True):
        return list(self._sigs)


_ALLOWED = {(c.symbol, c.combo_id) for c in load_registry(REGISTRY_TOP23).combos}


def _sig(symbol, combo_id, close_ms, side="BUY"):
    return SimpleNamespace(symbol=symbol, combo_id=combo_id, combo_name=f"C{combo_id}", timeframe="1h",
                           side=side, signal_close_ms=close_ms, entry_price=100.0, tp_pct=0.02, sl_pct=0.0066,
                           tp_price=102.0, sl_price=99.34, exit_policy_hash="x")


# ---------------------------------------------------------------- registry
def test_top23_registry_has_23_unique_combos_and_excludes_seven():
    reg = load_registry(REGISTRY_TOP23)
    keys = {(c.symbol, c.combo_id) for c in reg.combos}
    assert len(reg.combos) == 23 and len(keys) == 23
    assert keys.isdisjoint(EXCLUDED)
    assert ("BTC-USDT", 104) in keys  # C14 kept as combo 104
    assert ("ETH-USDT", 104) in keys  # ETH C14 ATR-SL


def test_default_registry_is_unchanged():
    assert DEFAULT_REGISTRY == "V3_LIVE_20_POSITIVE_FIXED_V1"
    assert len(load_registry(DEFAULT_REGISTRY).combos) == 20


# ---------------------------------------------------------------- engine wiring
def test_engine_needs_both_symbols_and_aligned_bars():
    eng = Top23Engine(strategy=_FakeStrategy([]))
    btc = _candles(1, 300)
    eth = _candles(2, 300)
    assert eng.calculate(BTC, {BTC: btc}) == []                      # no ETH history: fail closed
    shifted = eth.iloc[:-1].copy()                                     # ETH one bar behind BTC
    assert eng.calculate(BTC, {BTC: btc, ETH: shifted}) == []


def test_latest_bar_filter_and_field_mapping():
    btc, eth = _candles(1, 300), _candles(2, 300)
    latest_close = int(btc["open_time"].iloc[-1]) + M15_MS - 1
    old_close = latest_close - 3600_000
    sigs = [_sig("BTC-USDT", 1, latest_close), _sig("BTC-USDT", 1, old_close), _sig("BTC-USDT", 3, latest_close)]
    out = Top23Engine(strategy=_FakeStrategy(sigs)).calculate(BTC, {BTC: btc, ETH: eth})
    assert len(out) == 1                    # old bar dropped; combo 3 is not allowed (excluded)
    v = out[0]
    assert (v.symbol, v.combo, v.combo_name, v.side) == ("BTC-USDT", 1, "C1", "BUY")
    assert v.close_time == latest_close and v.entry == 100.0 and v.tp == 102.0 and v.sl == 99.34
    assert v.tp_pct == 0.02 and v.sl_pct == 0.0066
    assert v.registry_version == TOP23_VERSION and v.spec_hash == "fake-spec-hash"
    assert v.event_id == f"BTC-USDT|1h|C1|BUY|{latest_close}"


def test_symbol_not_in_the_23_never_emitted():
    btc, eth = _candles(1, 300), _candles(2, 300)
    latest_close = int(eth["open_time"].iloc[-1]) + M15_MS - 1
    out = Top23Engine(strategy=_FakeStrategy([_sig("ETH-USDT", 2, latest_close)])).calculate(ETH, {BTC: btc, ETH: eth})
    assert out == []


def test_to_lab_frame_utc_index_and_columns():
    c = _candles(3, 50)
    f = to_lab_frame(c)
    assert list(f.columns) == ["open", "high", "low", "close", "volume"]
    assert str(f.index.tz) == "UTC" and f.index.is_monotonic_increasing
    assert int(f.index[0].value // 1_000_000) == int(c["open_time"].iloc[0])


@pytest.mark.parametrize("combo", [104])
def test_btc_c14_spec_is_locked_h3a(combo):
    spec = load_registry(REGISTRY_TOP23).get("BTC-USDT", combo)
    assert spec is not None
    assert (spec.tp_pct, spec.sl_pct, spec.layer2, spec.entry_variant) == (
        0.02, 0.006666666666666667, "OB", "BTC-USDT_C14_H3a_KUMO_BAND_V1")
