"""Tests for PLUS44 live integration (V3_LIVE_44_PLUS44_V1 = PRUNE23 + TV21)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.live.config import V3Settings
from autotrade_v3.live.l2_lab5 import BTC, ETH, M15_MS, Lab5Engine
from autotrade_v3.live.l2_resampler import TimeframeCloseGate
from autotrade_v3.live.l2_strategy import L2Strategy, signal_row
from autotrade_v3.live.l2_tv21 import TV21_CONFIG_MAP, TV21_CONFIGS
from autotrade_v3.live.l3_execution import V3Signal
from autotrade_v3.live.store import MemoryStore
from autotrade_v3.live.strategy_registry import (
    DEFAULT_REGISTRY,
    KNOWN_REGISTRIES,
    REGISTRY_LAB11_23,
    REGISTRY_PLUS43_V2_1,
    REGISTRY_PLUS44_44,
    _CACHE,
    case_name,
    load_registry,
)

# Midnight UTC 2026-01-02 (aligned to M15, H4, H8, H12, D1)
UTC_MIDNIGHT_MS = 1_767_312_000_000


def _m15_frame(n: int, end_open_ms: int) -> pd.DataFrame:
    opens = [end_open_ms - (n - 1 - i) * M15_MS for i in range(n)]
    rng = np.random.default_rng(7)
    close = 50_000.0 + np.cumsum(rng.normal(0.0, 20.0, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 15.0
    low = np.minimum(open_, close) - 15.0
    return pd.DataFrame(
        {
            "open_time": opens,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 100.0),
            "close_time": [o + M15_MS - 1 for o in opens],
        }
    )


class _SpyLabStrategy:
    """Records combos passed to V3_LAB_5 to prove TV21 never enters PRUNE23 lab path."""

    def __init__(self, lab_combos: tuple[tuple[str, int], ...]):
        self._combos = lab_combos
        self.allowed_calls: list[set[tuple[str, int]]] = []

    def get_spec_hash(self) -> str:
        return "spy-lab-hash"

    def spec(self):
        return SimpleNamespace(
            combos=[SimpleNamespace(symbol=s, combo_id=cid) for s, cid in self._combos]
        )

    def generate_signals_for_combos(
        self,
        data,
        *,
        allowed_combos,
        symbols,
        only_approved,
        only_warmup_ready,
    ):
        self.allowed_calls.append(set(allowed_combos))
        return []


def test_plus44_registry_has_44_combos_stable_hash_and_no_token_collisions() -> None:
    # Original PLUS44 registry stays intact (rollback target) and matches the unmodified TV21_CONFIGS.
    assert DEFAULT_REGISTRY == REGISTRY_PLUS43_V2_1
    assert REGISTRY_PLUS44_44 in KNOWN_REGISTRIES
    assert REGISTRY_LAB11_23 in KNOWN_REGISTRIES

    _CACHE.clear()
    reg1 = load_registry(REGISTRY_PLUS44_44)
    _CACHE.clear()
    reg2 = load_registry(REGISTRY_PLUS44_44)

    assert len(reg1.combos) == 43
    assert reg1.spec_hash == reg2.spec_hash

    prune23 = [c for c in reg1.combos if c.signal_variant == "LAB_10"]
    tv21 = [c for c in reg1.combos if c.signal_variant == "TV21"]
    assert len(prune23) == 23
    assert len(tv21) == 20

    # No token collisions between PRUNE23 (C1..C16, TIER) and TV21 (TV03..TV31).
    prune_tokens = {(c.symbol, c.token) for c in prune23}
    tv_tokens = {(c.symbol, c.token) for c in tv21}
    assert prune_tokens.isdisjoint(tv_tokens)
    assert {c.token for c in tv21} == {cfg.display_id for cfg in TV21_CONFIGS}

    for cfg in TV21_CONFIGS:
        spec = reg1.get(cfg.symbol, cfg.combo_id)
        assert spec is not None
        assert spec.token == cfg.display_id
        assert case_name(cfg.combo_id) == cfg.display_id
        assert spec.native_timeframe == cfg.timeframe
        assert spec.effective_window_bars == cfg.window_bars
        assert spec.tp_pct == pytest.approx(cfg.tp_pct)
        assert spec.sl_pct == pytest.approx(cfg.sl_pct)


def test_env_rollback_to_live23_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V3_STRATEGY_REGISTRY", REGISTRY_LAB11_23)
    settings = V3Settings()
    assert settings.strategy_registry == REGISTRY_LAB11_23

    reg = load_registry(settings.strategy_registry)
    assert len(reg.combos) == 23
    assert all(c.signal_variant == "LAB_10" for c in reg.combos)

    l2 = L2Strategy(MemoryStore(), control=None, registry=settings.strategy_registry)
    assert l2.registry is not None
    assert len(l2.registry.combos) == 23
    assert l2.lab5 is not None
    assert len(l2.lab5.tv21_configs) == 0


def test_lab5_dispatches_tv21_separately_from_v3_lab5_and_gates_htf_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reg = load_registry(REGISTRY_PLUS44_44)
    prune_keys = tuple((c.symbol, c.combo_id) for c in reg.combos if c.signal_variant == "LAB_10")
    spy = _SpyLabStrategy(prune_keys)
    eng = Lab5Engine(reg, strategy=spy)

    # 1. TV21 combos are never passed to V3_LAB_5 generate_signals_for_combos.
    seen_tv21_calls: list[tuple[str, str, bool, bool]] = []

    def fake_gen_tv21(
        candles_15m,
        config,
        *,
        only_latest_closed=False,
        gate=None,
        registry_version=None,
        spec_hash=None,
    ):
        assert only_latest_closed is True
        assert isinstance(gate, TimeframeCloseGate)
        seen_tv21_calls.append((config.display_id, config.timeframe, only_latest_closed, gate is eng.tv21_gate))
        res = gate.resample_if_closed(config.symbol, candles_15m, config.timeframe, scope=config.display_id)
        if res is None:
            return []
        _, close_ms = res
        return [
            V3Signal(
                event_id=f"{config.symbol}|{config.timeframe}|{config.display_id}|BUY|{close_ms}",
                symbol=config.symbol,
                combo=config.combo_id,
                combo_name=config.display_id,
                side="BUY",
                timeframe=config.timeframe,
                close_time=close_ms,
                entry=50_000.0,
                tp=50_000.0 * (1.0 + config.tp_pct),
                sl=50_000.0 * (1.0 - config.sl_pct),
                tp_pct=config.tp_pct,
                sl_pct=config.sl_pct,
                registry_version=registry_version,
                spec_hash=spec_hash,
                signal_variant=f"{config.strategy_id}:{config.internal_variant}:{config.wrapper_variant}",
            )
        ]

    monkeypatch.setattr("autotrade_v3.live.l2_lab5.generate_tv21_signals_from_m15", fake_gen_tv21)

    # End at 03:30 UTC (close_ms = 03:44:59.999) -> NOT an H4/H8/H12/D1 close.
    mid_h4_open = UTC_MIDNIGHT_MS + 14 * M15_MS
    mid_frame = _m15_frame(16 * 10, mid_h4_open)
    assert eng.calculate(BTC, {BTC: mid_frame}) == []
    assert all(cid < 31000 for call in spy.allowed_calls for _, cid in call)
    assert len(seen_tv21_calls) == len([c for c in TV21_CONFIGS if c.symbol == BTC])

    # End at 03:45 UTC (close_ms = 03:59:59.999) -> exact H4 close, not H8/H12/D1.
    h4_close_open = UTC_MIDNIGHT_MS + 15 * M15_MS
    h4_frame = _m15_frame(16 * 10, h4_close_open)
    sigs_h4 = eng.calculate(BTC, {BTC: h4_frame})
    assert [(s.combo_name, s.timeframe) for s in sigs_h4] == [("TV03", "H4"), ("TV04", "H4")]
    expected_close_ms = h4_close_open + M15_MS - 1
    assert sigs_h4[0].event_id == f"BTC-USDT|H4|TV03|BUY|{expected_close_ms}"
    assert signal_row(sigs_h4[0])["combo"] == "TV03"

    # Re-evaluating the same H4 close must NOT replay the signal (gate blocks duplicate).
    assert eng.calculate(BTC, {BTC: h4_frame}) == []

    # End at 23:45 UTC (close_ms = 23:59:59.999) -> closes H4, H8, H12, and D1 simultaneously.
    d1_close_open = UTC_MIDNIGHT_MS + 95 * M15_MS
    d1_frame = _m15_frame(96 * 2, d1_close_open)
    sigs_d1 = eng.calculate(BTC, {BTC: d1_frame})
    btc_tv_tokens = {c.display_id for c in TV21_CONFIGS if c.symbol == BTC}
    assert {s.combo_name for s in sigs_d1} == btc_tv_tokens


def test_l2_preview_does_not_consume_tv21_close_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = load_registry(REGISTRY_PLUS44_44)
    l2 = L2Strategy(MemoryStore(), control=None, registry=reg)
    prune_keys = tuple((c.symbol, c.combo_id) for c in reg.combos if c.signal_variant == "LAB_10")
    l2.lab5 = Lab5Engine(reg, strategy=_SpyLabStrategy(prune_keys))

    cfg = TV21_CONFIG_MAP["TV03"]
    h4_close_open = UTC_MIDNIGHT_MS + 15 * M15_MS
    frame = _m15_frame(32, h4_close_open)

    def fake_gen_tv21(
        candles_15m,
        config,
        *,
        only_latest_closed=False,
        gate=None,
        registry_version=None,
        spec_hash=None,
    ):
        if config.display_id != "TV03":
            return []
        if gate is not None:
            res = gate.resample_if_closed(config.symbol, candles_15m, config.timeframe, scope=config.display_id)
            if res is None:
                return []
            close_ms = res[1]
        else:
            close_ms = h4_close_open + M15_MS - 1
        return [
            V3Signal(
                event_id=f"{config.symbol}|{config.timeframe}|{config.display_id}|BUY|{close_ms}",
                symbol=config.symbol,
                combo=config.combo_id,
                combo_name=config.display_id,
                side="BUY",
                timeframe=config.timeframe,
                close_time=close_ms,
                entry=50_000.0,
                tp=51_500.0,
                sl=49_250.0,
                tp_pct=cfg.tp_pct,
                sl_pct=cfg.sl_pct,
                registry_version=registry_version,
                spec_hash=spec_hash,
                signal_variant="L1_S1:B0:V6_ATR_VOL_FILTER",
            )
        ]

    monkeypatch.setattr("autotrade_v3.live.l2_lab5.generate_tv21_signals_from_m15", fake_gen_tv21)
    snap = SimpleNamespace(
        symbol=BTC,
        latest_open_time=h4_close_open,
        latest_close_time=h4_close_open + M15_MS - 1,
        candles=frame,
    )

    prev = l2.preview(snap)
    assert [s.combo_name for s in prev] == ["TV03"]

    # Live calculate on the same closed bar still emits once because preview() did not consume gate.
    calc = l2.calculate(snap)
    assert [s.combo_name for s in calc] == ["TV03"]
    assert l2.calculate(snap) == []
