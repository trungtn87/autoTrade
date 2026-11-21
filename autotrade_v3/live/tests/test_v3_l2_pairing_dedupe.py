"""Regression tests: independent M15 L2 calculation, idempotency and no stale replay."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pandas as pd
import pytest

from autotrade_v3.live.app import Runtime
from autotrade_v3.live.l2_lab5 import BTC, ETH, M15_MS, Lab5Engine
from autotrade_v3.live.l2_strategy import L2Strategy
from autotrade_v3.live.l3_execution import V3Signal
from autotrade_v3.live.store import MemoryStore


OPEN_MS = 1_800_000_000_000


def _frame(last_open: int, count: int = 3) -> pd.DataFrame:
    times = [last_open - (count - 1 - i) * M15_MS for i in range(count)]
    return pd.DataFrame({
        "open_time": times, "open": [100.] * count, "high": [101.] * count,
        "low": [99.] * count, "close": [100.] * count,
        "volume": [5.] * count, "close_time": [t + M15_MS - 1 for t in times],
    })


def _snapshot(symbol: str, opened: int = OPEN_MS, count: int = 3):
    return SimpleNamespace(
        symbol=symbol, latest_open_time=opened,
        latest_close_time=opened + M15_MS - 1, candles=_frame(opened, count),
    )


def _signal(symbol: str, opened: int = OPEN_MS, *, side: str = "BUY",
            combo: int = 1) -> V3Signal:
    close = opened + M15_MS - 1
    return V3Signal(
        event_id=f"{symbol}|15m|C{combo}|{side}|{close}",
        symbol=symbol, combo=combo, combo_name=f"C{combo}", side=side,
        timeframe="15m", close_time=close,
        entry=100., tp=102., sl=99., tp_pct=.02, sl_pct=.01,
    )


class FakeCore:
    def __init__(self):
        self.calls = []

    def generate_signals_for_combos(self, data, *, allowed_combos, symbols,
                                    only_approved, only_warmup_ready):
        assert len(symbols) == 1
        sym = symbols[0]
        assert list(data) == [sym]
        self.calls.append((sym, data[sym].index[-1], len(data[sym])))
        close_ms = int(data[sym].index[-1].value // 1_000_000) + M15_MS - 1
        return [SimpleNamespace(
            symbol=sym, combo_id=1, combo_name="C1", side="BUY",
            timeframe="15m", signal_close_ms=close_ms,
            entry_price=100., tp_price=102., sl_price=99., tp_pct=.02, sl_pct=.01,
        )]


def _lab5(fake_core: FakeCore) -> Lab5Engine:
    lab = object.__new__(Lab5Engine)
    lab.strategy = fake_core
    lab.registry_version = "test"
    lab.spec_hash = "test-spec"
    lab.allowed = {(BTC, 1), (ETH, 1)}
    return lab


@pytest.mark.parametrize("symbol,count", [(BTC, 3400), (ETH, 12000)])
def test_lab5_needs_only_its_own_candles_not_both_coin_frames(symbol, count):
    core = FakeCore()
    lab = _lab5(core)
    frame = _frame(OPEN_MS, count)
    got = lab.calculate(symbol, {symbol: frame})
    assert [(s.symbol, s.combo) for s in got] == [(symbol, 1)]
    assert got[0].close_time == OPEN_MS + M15_MS - 1
    assert core.calls == [(symbol, pd.Timestamp(OPEN_MS, unit="ms", tz="UTC"), count)]


def test_lab5_does_not_wait_for_other_symbol_or_compare_range_index_lengths():
    core = FakeCore()
    lab = _lab5(core)
    frames = {BTC: _frame(OPEN_MS, 3400), ETH: _frame(OPEN_MS - M15_MS, 12000)}
    assert frames[BTC].index[-1] != frames[ETH].index[-1]
    assert {s.symbol for s in lab.calculate(BTC, frames)} == {BTC}
    assert {s.symbol for s in lab.calculate(ETH, frames)} == {ETH}
    assert [x[0] for x in core.calls] == [BTC, ETH]


class FakeLab:
    def __init__(self):
        self.calls = []

    def calculate(self, symbol, frames):
        assert list(frames) == [symbol]
        opened = int(frames[symbol]["open_time"].iloc[-1])
        self.calls.append((symbol, opened))
        return [_signal(symbol, opened)]


class Recorder:
    def __init__(self):
        self.calls = []

    def submit(self, sig):
        self.calls.append(sig)
        return sig.event_id


class Control:
    def __init__(self):
        self.calls = []

    def report(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _l2() -> L2Strategy:
    # No real engine, exchange credentials, or database connection.
    l2 = object.__new__(L2Strategy)
    l2.registry = object()
    l2.lab5 = FakeLab()
    l2._calculate_locks = {BTC: threading.Lock(), ETH: threading.Lock()}
    l2._last_calculated_open = {}
    l2._dispatch_lock = threading.Lock()
    l2._last_dispatched_by_combo = {}
    l2.store = MemoryStore()
    l2.control = Control()
    l2.alert = None
    l2.executor = Recorder()
    l2.last_signals = []
    return l2


@pytest.mark.parametrize("first,second", [(BTC, ETH), (ETH, BTC)])
def test_each_coin_calculates_on_its_own_arrival(first, second):
    l2 = _l2()
    assert [s.symbol for s in l2.calculate(_snapshot(first))] == [first]
    assert [s.symbol for s in l2.calculate(_snapshot(second))] == [second]
    assert l2.lab5.calls == [(first, OPEN_MS), (second, OPEN_MS)]


def test_concurrent_callbacks_each_coin_calculates_once():
    l2 = _l2()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(l2.calculate, (_snapshot(BTC), _snapshot(ETH))))
    assert {s.symbol for batch in results for s in batch} == {BTC, ETH}
    assert sorted(l2.lab5.calls) == sorted([(BTC, OPEN_MS), (ETH, OPEN_MS)])
    # Repeat both callbacks concurrently: no second signal per symbol/bar.
    with ThreadPoolExecutor(max_workers=2) as pool:
        again = list(pool.map(l2.calculate, (_snapshot(BTC), _snapshot(ETH))))
    assert again == [[], []]
    assert len(l2.lab5.calls) == 2


def test_stale_duplicate_one_coin_cannot_block_new_bar_of_other():
    l2 = _l2()
    l2.calculate(_snapshot(BTC))
    l2.calculate(_snapshot(ETH))
    assert [s.symbol for s in l2.calculate(_snapshot(BTC, OPEN_MS + M15_MS))] == [BTC]
    assert l2.calculate(_snapshot(BTC, OPEN_MS)) == []
    assert [s.symbol for s in l2.calculate(_snapshot(ETH, OPEN_MS + M15_MS))] == [ETH]
    assert len(l2.lab5.calls) == 4


def test_dispatch_dedupes_same_side_and_rejects_opposing_sides():
    l2 = _l2()
    a = _signal(BTC)
    b = _signal(BTC, side="SELL")
    # Conflicting sides on the same combo/bar must never choose an arbitrary Entry.
    assert l2.dispatch([a, b]) == []
    assert len(l2.executor.calls) == 0
    assert l2.control.calls[0][0][1] == "signal_conflict"

    assert len(l2.dispatch([a, a])) == 1
    assert l2.dispatch([a, b]) == []
    assert len(l2.executor.calls) == 1
    assert len(l2.store.signals) == 1
    assert len(l2.dispatch([_signal(BTC, OPEN_MS + M15_MS)])) == 1
    assert l2.dispatch([_signal(BTC, OPEN_MS - M15_MS)]) == []
    assert len(l2.executor.calls) == 2


def test_runtime_ignores_prestart_and_superseded_websocket_candles():
    rt = object.__new__(Runtime)
    rt._symbol_locks = {BTC: threading.Lock()}
    rt._processed = {}
    rt.started_ms = OPEN_MS + M15_MS
    rt.l1 = SimpleNamespace(
        ingest=lambda symbol, frame: _snapshot(BTC),
        set_health=lambda *args, **kwargs: None,
    )
    rt.control = Control()
    called = []
    rt._run_strategy = lambda symbol, snapshot: called.append(symbol)
    rt.on_closed(BTC, _frame(OPEN_MS).tail(1))
    assert called == []

    newer = _snapshot(BTC, OPEN_MS + M15_MS)
    rt.started_ms = OPEN_MS
    rt.l1.ingest = lambda symbol, frame: newer
    rt.on_closed(BTC, _frame(OPEN_MS + M15_MS).tail(1))
    assert called == [BTC]
    # Simulate a delayed WebSocket frame when the store already has newer data.
    rt._processed = {}
    rt.on_closed(BTC, _frame(OPEN_MS).tail(1))
    assert called == [BTC]
