"""V3_LIVE_23_LAB_5_V1: registry, lab wiring, latest-bar filter, mapping and lab parity fixture.

MOCK / offline tests: no network, no broker, no DB. The lab strategy is the real V3_LAB_5 code; only the
latest-bar and mapping tests replace it with fake Signals.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.live.l2_lab5 import BTC, ETH, M15_MS, Lab5Engine, to_lab_frame
from autotrade_v3.live.strategy_registry import DEFAULT_REGISTRY, REGISTRY_LAB5_23, load_registry

ROOT = Path(__file__).resolve().parents[3]
LAB_CONFIG = ROOT / "autotrade_v3" / "lab" / "v3_lab_5_config.json"
FIXTURE = ROOT / "autotrade_v3" / "tests" / "fixtures" / "v3_lab_5_l2_parity_fixture.json"

EXCLUDED = {("BTC-USDT", 3), ("BTC-USDT", 102), ("BTC-USDT", 105),
            ("ETH-USDT", 2), ("ETH-USDT", 9), ("ETH-USDT", 106), ("ETH-USDT", 11)}


def _lab_cfg() -> dict:
    return {(c["symbol"], int(c["combo_id"])): c for c in json.loads(LAB_CONFIG.read_text(encoding="utf-8"))["combos"]}


def _candles(seed: int, n: int, start_ms: int = 1_700_000_100_000 // M15_MS * M15_MS, p0: float = 30_000.0):
    rng = np.random.default_rng(seed)
    close = p0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    return pd.DataFrame({"open_time": start_ms + np.arange(n) * M15_MS, "open": open_, "high": high,
                         "low": low, "close": close, "volume": np.ones(n)})


class _FakeLab:
    """Stands in for the lab strategy only in mapping tests."""

    def __init__(self, sigs):
        self._sigs = sigs

    def get_spec_hash(self):
        return "fake-spec-hash"

    def spec(self):
        return SimpleNamespace(combos=tuple(SimpleNamespace(symbol=s, combo_id=c) for s, c in sorted(_ALLOWED)))

    def generate_signals_for_combos(self, data, allowed_combos=None, symbols=None, only_approved=True,
                                    only_warmup_ready=True):
        return list(self._sigs)


_REG = load_registry(REGISTRY_LAB5_23)
_ALLOWED = {(c.symbol, c.combo_id) for c in _REG.combos}


def _sig(symbol, combo_id, close_ms, side="BUY"):
    return SimpleNamespace(symbol=symbol, combo_id=combo_id, combo_name=f"C{combo_id}", timeframe="1h",
                           side=side, signal_close_ms=close_ms, entry_price=100.0, tp_pct=0.02, sl_pct=0.0066,
                           tp_price=102.0, sl_price=99.34, exit_policy_hash="x")


# ---------------------------------------------------------------- registry
def test_lab5_registry_has_23_unique_combos_and_excludes_seven():
    keys = {(c.symbol, c.combo_id) for c in _REG.combos}
    assert len(_REG.combos) == 23 and len(keys) == 23
    assert keys.isdisjoint(EXCLUDED)
    assert {s for s, _ in keys} == {BTC, ETH}
    assert sum(1 for s, _ in keys if s == BTC) == 12 and sum(1 for s, _ in keys if s == ETH) == 11


def test_lab5_registry_matches_lab_config_per_combo():
    cfg = _lab_cfg()
    for c in _REG.combos:
        lab = cfg[(c.symbol, c.combo_id)]
        assert c.native_timeframe == lab["timeframe"], (c.symbol, c.combo_id)
        assert c.entry_variant == lab["execution_version"], (c.symbol, c.combo_id)
        assert c.layer2 == lab["layer2"], (c.symbol, c.combo_id)
        assert c.tp_pct == pytest.approx(float(lab["tp_pct"])), (c.symbol, c.combo_id)
        assert c.sl_pct == pytest.approx(float(lab["sl_pct"])), (c.symbol, c.combo_id)


def test_default_registry_is_unchanged():
    assert DEFAULT_REGISTRY == "V3_LIVE_20_POSITIVE_FIXED_V1"
    assert len(load_registry(DEFAULT_REGISTRY).combos) == 20


# ---------------------------------------------------------------- engine wiring
def test_engine_needs_both_symbols_and_aligned_bars():
    eng = Lab5Engine(_REG, strategy=_FakeLab([]))
    btc, eth = _candles(1, 300), _candles(2, 300)
    assert eng.calculate(BTC, {BTC: to_lab_frame(btc)}) == []  # no ETH history: fail closed
    shifted = to_lab_frame(eth.iloc[:-1].copy())
    assert eng.calculate(BTC, {BTC: to_lab_frame(btc), ETH: shifted}) == []


def test_latest_bar_filter_and_field_mapping():
    btc, eth = _candles(1, 300), _candles(2, 300)
    frames = {BTC: to_lab_frame(btc), ETH: to_lab_frame(eth)}
    latest_close = int(btc["open_time"].iloc[-1]) + M15_MS - 1
    old_close = latest_close - 3600_000
    sigs = [_sig("BTC-USDT", 1, latest_close), _sig("BTC-USDT", 1, old_close), _sig("BTC-USDT", 3, latest_close)]
    out = Lab5Engine(_REG, strategy=_FakeLab(sigs)).calculate(BTC, frames)
    assert len(out) == 1  # old bar dropped; combo 3 is excluded and never handed to L3
    v = out[0]
    assert (v.symbol, v.combo, v.combo_name, v.side) == ("BTC-USDT", 1, "C1", "BUY")
    assert v.close_time == latest_close and v.entry == 100.0 and v.tp == 102.0 and v.sl == 99.34
    assert v.tp_pct == 0.02 and v.sl_pct == 0.0066
    assert v.registry_version == _REG.version and v.spec_hash == "fake-spec-hash"
    assert v.event_id == f"BTC-USDT|1h|C1|BUY|{latest_close}"


def test_symbol_outside_registry_never_emitted():
    btc, eth = _candles(1, 300), _candles(2, 300)
    frames = {BTC: to_lab_frame(btc), ETH: to_lab_frame(eth)}
    latest_close = int(eth["open_time"].iloc[-1]) + M15_MS - 1
    out = Lab5Engine(_REG, strategy=_FakeLab([_sig("ETH-USDT", 2, latest_close)])).calculate(ETH, frames)
    assert out == []  # ETH-C2 is excluded from the 23


def test_to_lab_frame_utc_index_and_columns():
    c = _candles(3, 50)
    f = to_lab_frame(c)
    assert list(f.columns) == ["open", "high", "low", "close", "volume"]
    assert str(f.index.tz) == "UTC" and f.index.is_monotonic_increasing
    assert int(f.index[0].value // 1_000_000) == int(c["open_time"].iloc[0])


# ---------------------------------------------------------------- lab parity on the fixture
def test_lab5_restricted_output_matches_fixture_for_23_combos():
    """Real V3_LAB_5 code on the 5000-bar synthetic fixture: restriction to 23 loses/adds nothing."""
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    frames = {}
    for sym in (BTC, ETH):
        o = fx["ohlcv_m15"][sym]
        df = pd.DataFrame({"open_time": np.asarray(o["timestamps_open_ms"], dtype="int64"),
                           "open": o["open"], "high": o["high"], "low": o["low"],
                           "close": o["close"], "volume": o["volume"]})
        frames[sym] = to_lab_frame(df)
    from autotrade_v3.lab.v3_lab_5_loader import load_v3_lab_5_strategy
    lab = load_v3_lab_5_strategy()
    assert lab.get_spec_hash() == Lab5Engine(_REG, strategy=lab).spec_hash
    got = lab.generate_signals_for_combos(frames, allowed_combos=_ALLOWED, only_approved=True,
                                          only_warmup_ready=True)
    got_ids = {s.signal_id for s in got}
    expected_ids = {x["signal_id"] for x in fx["expected"]["approved_signals"]
                    if (x["symbol"], int(x["combo_id"])) in _ALLOWED}
    assert got_ids == expected_ids
    assert all((s.symbol, int(s.combo_id)) in _ALLOWED for s in got)
    assert not any((s.symbol, int(s.combo_id)) in EXCLUDED for s in got)
