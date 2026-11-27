"""PLUS43_V2_1_TP_ONLY live registry: exactly 8 config changes vs PLUS44, rollback intact."""
from __future__ import annotations

import pytest

from autotrade_v3.live.config import V3Settings
from autotrade_v3.live.l2_lab5 import Lab5Engine
from autotrade_v3.live.l2_strategy import L2Strategy
from autotrade_v3.live.l2_tv21 import TV21_CONFIG_MAP, TV21_CONFIG_MAP_V2_1
from autotrade_v3.live.store import MemoryStore
from autotrade_v3.live.strategy_registry import (
    DEFAULT_REGISTRY,
    REGISTRY_LAB11_23,
    REGISTRY_PLUS43_V2_1,
    REGISTRY_PLUS44_44,
    _CACHE,
    load_registry,
)

EXPECTED = {
    "TV03": ("H4", 0.024, 0.015),
    "TV07": ("H4", 0.045, 0.015),
    "TV08": ("H4", 0.045, 0.015),
    "TV09": ("H4", 0.0375, 0.015),
    "TV12": ("H8", 0.0375, 0.0125),
    "TV20": ("H12", 0.0375, 0.0125),
    "TV24": ("H12", 0.045, 0.015),
    "TV28": ("H8", 0.03, 0.012),
}


def test_default_is_v2_1_and_has_43_combos() -> None:
    _CACHE.clear()
    assert DEFAULT_REGISTRY == REGISTRY_PLUS43_V2_1
    reg = load_registry()
    assert len(reg.combos) == 43
    assert len([c for c in reg.combos if c.signal_variant == "LAB_10"]) == 23
    assert len([c for c in reg.combos if c.signal_variant == "TV21"]) == 20


def test_exactly_eight_changes_vs_plus44_and_invariants() -> None:
    _CACHE.clear()
    new = load_registry(REGISTRY_PLUS43_V2_1)
    old = load_registry(REGISTRY_PLUS44_44)
    changed = []
    for c_new in new.combos:
        c_old = old.get(c_new.symbol, c_new.combo_id)
        assert c_old is not None
        assert c_new.sl_pct <= 0.015 + 1e-12
        if (c_new.native_timeframe, c_new.tp_pct, c_new.sl_pct, c_new.window_bars) != (
            c_old.native_timeframe, c_old.tp_pct, c_old.sl_pct, c_old.window_bars
        ):
            changed.append(c_new.token)
            tf, tp, sl = EXPECTED[c_new.token]
            assert c_new.native_timeframe == tf
            assert c_new.tp_pct == pytest.approx(tp)
            assert c_new.sl_pct == pytest.approx(sl)
            assert c_new.entry == c_old.entry and c_new.window_bars == c_old.window_bars
        else:
            assert c_new.spec_hash == c_old.spec_hash
    assert sorted(changed) == sorted(EXPECTED)
    tv21 = new.get("BTC-USDT", 31021)
    assert tv21.tp_pct == pytest.approx(0.036) and tv21.sl_pct == pytest.approx(0.015)
    assert old.get("BTC-USDT", 31028).native_timeframe == "D1"
    assert TV21_CONFIG_MAP["TV28"].timeframe == "D1"
    assert TV21_CONFIG_MAP_V2_1["TV28"].timeframe == "H8"


def test_engine_uses_v2_1_configs_and_old_registry_keeps_old() -> None:
    _CACHE.clear()
    class _Spy:
        def get_spec_hash(self): return "x"
        def spec(self):
            from types import SimpleNamespace
            reg = load_registry(REGISTRY_PLUS44_44)
            return SimpleNamespace(combos=[SimpleNamespace(symbol=c.symbol, combo_id=c.combo_id) for c in reg.combos if c.signal_variant == "LAB_10"])
    eng_new = Lab5Engine(load_registry(REGISTRY_PLUS43_V2_1), strategy=_Spy())
    eng_old = Lab5Engine(load_registry(REGISTRY_PLUS44_44), strategy=_Spy())
    for tok, (tf, tp, sl) in EXPECTED.items():
        cid = 31000 + int(tok[2:])
        cfg = next(c for (s, i), c in eng_new.tv21_configs.items() if i == cid)
        assert (cfg.timeframe, cfg.tp_pct, cfg.sl_pct) == (tf, pytest.approx(tp), pytest.approx(sl))
    old28 = next(c for (s, i), c in eng_old.tv21_configs.items() if i == 31028)
    assert old28.timeframe == "D1"


def test_env_rollbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, n in ((REGISTRY_PLUS44_44, 43), (REGISTRY_LAB11_23, 23)):
        monkeypatch.setenv("V3_STRATEGY_REGISTRY", name)
        assert V3Settings().strategy_registry == name
        l2 = L2Strategy(MemoryStore(), control=None, registry=name)
        assert len(l2.registry.combos) == n
