"""Tests for per-combo M15 window_bars configuration and L2 slicing."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from autotrade_v3.live.l2_lab5 import BTC, ETH, M15_MS, Lab5Engine
from autotrade_v3.live.l2_strategy import L2Strategy
from autotrade_v3.live.store import MemoryStore
from autotrade_v3.live.strategy_registry import (
    REGISTRY_LAB11_23,
    ComboSpec,
    StrategyRegistry,
    default_window_bars_for,
    load_registry,
    max_window_bars_for,
)

LATEST_OPEN_MS = 1_795_521_600_000


def _candles(n: int, last_open_ms: int = LATEST_OPEN_MS) -> pd.DataFrame:
    opens = [last_open_ms - (n - 1 - i) * M15_MS for i in range(n)]
    return pd.DataFrame({
        "open_time": opens,
        "open": [100.0 + i * 0.01 for i in range(n)],
        "high": [101.0 + i * 0.01 for i in range(n)],
        "low": [99.0 + i * 0.01 for i in range(n)],
        "close": [100.5 + i * 0.01 for i in range(n)],
        "volume": [500.0] * n,
        "close_time": [o + M15_MS - 1 for o in opens],
    })


def _snapshot(symbol: str, count: int, last_open_ms: int = LATEST_OPEN_MS):
    return SimpleNamespace(
        symbol=symbol,
        latest_open_time=last_open_ms,
        latest_close_time=last_open_ms + M15_MS - 1,
        candles=_candles(count, last_open_ms),
    )


def _combo(symbol: str, combo_id: int, window_bars: int | None = None) -> ComboSpec:
    return ComboSpec(
        symbol=symbol,
        combo_id=combo_id,
        native_timeframe="1h",
        is_new6=False,
        entry_variant="20FINAL_FIXED_V1@1",
        entry={},
        layer2="OFF",
        tp_pct=0.02,
        sl_pct=0.008,
        signal_variant="LAB_10",
        window_bars=window_bars,
    )


class _RecordingLabStrategy:
    def __init__(self, combos: tuple[ComboSpec, ...]):
        self._combos = combos
        self.calls: list[tuple[str, int, set[tuple[str, int]], int, int]] = []

    def get_spec_hash(self) -> str:
        return "recording-spec-hash"

    def spec(self):
        return SimpleNamespace(
            combos=[SimpleNamespace(symbol=c.symbol, combo_id=c.combo_id) for c in self._combos]
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
        assert len(symbols) == 1
        sym = symbols[0]
        df = data[sym]
        first_open_ms = int(df.index[0].value // 1_000_000)
        last_open_ms = int(df.index[-1].value // 1_000_000)
        self.calls.append((sym, len(df), set(allowed_combos), first_open_ms, last_open_ms))
        close_ms = last_open_ms + M15_MS - 1
        out = []
        for s, cid in sorted(allowed_combos):
            if s != sym:
                continue
            out.append(SimpleNamespace(
                symbol=sym,
                combo_id=cid,
                combo_name=f"C{cid}",
                side="BUY",
                timeframe="1h",
                signal_close_ms=close_ms,
                entry_price=float(df["close"].iloc[-1]),
                tp_price=float(df["close"].iloc[-1]) * 1.02,
                sl_price=float(df["close"].iloc[-1]) * 0.992,
                tp_pct=0.02,
                sl_pct=0.008,
            ))
        return out


def test_live23_registry_declares_prune23_window_bars() -> None:
    reg = load_registry(REGISTRY_LAB11_23)
    btc = reg.for_symbol(BTC)
    eth = reg.for_symbol(ETH)
    assert len(btc) == 12
    assert len(eth) == 11
    assert all(c.window_bars == 3_400 and c.effective_window_bars == 3_400 for c in btc)
    assert all(c.window_bars == 12_000 and c.effective_window_bars == 12_000 for c in eth)


def test_combo_spec_window_bars_default_and_bounds() -> None:
    assert default_window_bars_for(BTC) == 3_400
    assert default_window_bars_for(ETH) == 12_000
    assert max_window_bars_for(BTC) == 13_440
    assert max_window_bars_for(ETH) == 25_920

    assert _combo(BTC, 1, None).effective_window_bars == 3_400
    assert _combo(ETH, 1, None).effective_window_bars == 12_000
    assert _combo(BTC, 2, 13_440).effective_window_bars == 13_440
    assert _combo(ETH, 3, 25_920).effective_window_bars == 25_920

    for bad_btc in (0, -1, 13_441):
        with pytest.raises(ValueError, match="window_bars"):
            _combo(BTC, 1, bad_btc)
    for bad_eth in (0, -5, 25_921):
        with pytest.raises(ValueError, match="window_bars"):
            _combo(ETH, 1, bad_eth)


def test_lab5_slices_per_combo_window_from_single_snapshot() -> None:
    combos = (
        _combo(BTC, 1, 3_400),
        _combo(BTC, 2, 13_440),
        _combo(BTC, 4, None),
        _combo(BTC, 6, 1_500),
        _combo(ETH, 1, 12_000),
        _combo(ETH, 3, 25_920),
        _combo(ETH, 4, None),
        _combo(ETH, 6, 5_000),
    )
    reg = StrategyRegistry(version="TEST_MIXED_WINDOW@1.0.0", combos=combos)
    fake_strat = _RecordingLabStrategy(combos)
    eng = Lab5Engine(reg, strategy=fake_strat)

    btc_frame = _candles(13_440)
    btc_before = btc_frame.copy(deep=True)
    eth_frame = _candles(25_920)
    eth_before = eth_frame.copy(deep=True)

    btc_sigs = eng.calculate(BTC, {BTC: btc_frame})
    eth_sigs = eng.calculate(ETH, {ETH: eth_frame})

    # Input frames remain completely untouched in order and timestamps.
    pd.testing.assert_frame_equal(btc_frame, btc_before)
    pd.testing.assert_frame_equal(eth_frame, eth_before)

    assert {(s.symbol, s.combo) for s in btc_sigs} == {(BTC, 1), (BTC, 2), (BTC, 4), (BTC, 6)}
    assert {(s.symbol, s.combo) for s in eth_sigs} == {(ETH, 1), (ETH, 3), (ETH, 4), (ETH, 6)}

    btc_calls = {n: (allowed, first_ms, last_ms) for sym, n, allowed, first_ms, last_ms in fake_strat.calls if sym == BTC}
    assert btc_calls[1_500][0] == {(BTC, 6)}
    assert btc_calls[1_500][1] == int(btc_frame.iloc[-1_500]["open_time"])
    assert btc_calls[1_500][2] == int(btc_frame.iloc[-1]["open_time"])
    assert btc_calls[3_400][0] == {(BTC, 1), (BTC, 4)}
    assert btc_calls[3_400][1] == int(btc_frame.iloc[-3_400]["open_time"])
    assert btc_calls[3_400][2] == int(btc_frame.iloc[-1]["open_time"])
    assert btc_calls[13_440][0] == {(BTC, 2)}
    assert btc_calls[13_440][1] == int(btc_frame.iloc[0]["open_time"])
    assert btc_calls[13_440][2] == int(btc_frame.iloc[-1]["open_time"])

    eth_calls = {n: (allowed, first_ms, last_ms) for sym, n, allowed, first_ms, last_ms in fake_strat.calls if sym == ETH}
    assert eth_calls[5_000][0] == {(ETH, 6)}
    assert eth_calls[5_000][1] == int(eth_frame.iloc[-5_000]["open_time"])
    assert eth_calls[5_000][2] == int(eth_frame.iloc[-1]["open_time"])
    assert eth_calls[12_000][0] == {(ETH, 1), (ETH, 4)}
    assert eth_calls[12_000][1] == int(eth_frame.iloc[-12_000]["open_time"])
    assert eth_calls[12_000][2] == int(eth_frame.iloc[-1]["open_time"])
    assert eth_calls[25_920][0] == {(ETH, 3)}
    assert eth_calls[25_920][1] == int(eth_frame.iloc[0]["open_time"])
    assert eth_calls[25_920][2] == int(eth_frame.iloc[-1]["open_time"])


def test_l2_strategy_calculate_and_preview_preserve_full_snapshot_for_per_combo_slicing() -> None:
    combos = (
        _combo(BTC, 1, 3_400),
        _combo(BTC, 2, 13_440),
        _combo(ETH, 1, 12_000),
        _combo(ETH, 3, 25_920),
    )
    reg = StrategyRegistry(version="TEST_L2_WINDOW@1.0.0", combos=combos)
    l2 = L2Strategy(MemoryStore(), control=None, registry=reg)
    fake_strat = _RecordingLabStrategy(combos)
    l2.lab5 = Lab5Engine(reg, strategy=fake_strat)

    btc_snap = _snapshot(BTC, 13_440)
    eth_snap = _snapshot(ETH, 25_920)

    # Preview does not truncate ETH to 12,000 and does not advance the live cursor.
    prev_eth = l2.preview(eth_snap)
    assert {(s.symbol, s.combo) for s in prev_eth} == {(ETH, 1), (ETH, 3)}
    assert [c[:2] for c in fake_strat.calls] == [(ETH, 12_000), (ETH, 25_920)]

    fake_strat.calls.clear()
    calc_eth = l2.calculate(eth_snap)
    calc_btc = l2.calculate(btc_snap)
    assert {(s.symbol, s.combo) for s in calc_eth} == {(ETH, 1), (ETH, 3)}
    assert {(s.symbol, s.combo) for s in calc_btc} == {(BTC, 1), (BTC, 2)}
    assert [c[:2] for c in fake_strat.calls] == [
        (ETH, 12_000), (ETH, 25_920), (BTC, 3_400), (BTC, 13_440),
    ]
    # Duplicate candle open_time is skipped by L2Strategy.calculate.
    assert l2.calculate(eth_snap) == []
