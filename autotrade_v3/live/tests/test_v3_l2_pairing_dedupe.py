"""Regression tests: aligned dual-symbol L2 dispatch, idempotency, no stale replay."""
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
        self.calls.append((tuple(symbols), data[BTC].index[-1], data[ETH].index[-1]))
        close_ms = int(data[BTC].index[-1].value // 1_000_000) + M15_MS - 1
        return [
            SimpleNamespace(
                symbol=sym, combo_id=1, combo_name="C1", side="BUY",
                timeframe="15m", signal_close_ms=close_ms,
                entry_price=100., tp_price=102., sl_price=99., tp_pct=.02, sl_pct=.01,
            ) for sym in symbols
        ]


def _lab5(fake_core: FakeCore) -> Lab5Engine:
    lab = object.__new__(Lab5Engine)
    lab.strategy = fake_core
    lab.registry_version = "test"
    lab.spec_hash = "test-spec"
    lab.allowed = {(BTC, 1), (ETH, 1)}
    return lab


def test_lab5_aligns_actual_timestamp_not_different_candle_row_indexes():
    core = FakeCore()
    lab = _lab5(core)
    frames = {BTC: _frame(OPEN_MS, 3), ETH: _frame(OPEN_MS, 5)}
    assert frames[BTC].index[-1] != frames[ETH].index[-1]
    result = lab.calculate_for_symbols(frames, symbols=[BTC, ETH])
    assert {(s.symbol, s.combo) for s in result} == {(BTC, 1), (ETH, 1)}
    assert len(core.calls) == 1
    assert core.calls[0][1] == core.calls[0][2]

    # Both frames are required to finish at the SAME open timestamp.
    assert lab.calculate_for_symbols({BTC: frames[BTC], ETH: _frame(OPEN_MS - M15_MS, 5)},
                                     symbols=[BTC, ETH]) == []
    assert len(core.calls) == 1


class FakeLab:
    def __init__(self):
        self.calls = []

    def calculate_for_symbols(self, frames, *, symbols):
        opened = int(frames[BTC]["open_time"].iloc[-1])
        assert opened == int(frames[ETH]["open_time"].iloc[-1])
        self.calls.append((opened, tuple(symbols)))
        return [_signal(sym, opened) for sym in symbols]


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
    # Do not instantiate the live engine or load any private exchange credentials.
    l2 = object.__new__(L2Strategy)
    l2.registry = object()
    l2.lab5 = FakeLab()
    l2._lab5_frames = {}
    l2._frame_lock = threading.Lock()
    l2._last_evaluated_m15_open = -1
    l2._dispatch_lock = threading.Lock()
    l2._last_dispatched_by_combo = {}
    l2.store = MemoryStore()
    l2.control = Control()
    l2.alert = None
    l2.executor = Recorder()
    l2.last_signals = []
    return l2


@pytest.mark.parametrize("first,second", [(BTC, ETH), (ETH, BTC)])
def test_second_arrival_evaluates_both_symbols_once(first, second):
    l2 = _l2()
    assert l2.calculate(_snapshot(first, count=3 if first == BTC else 5)) == []
    signals = l2.calculate(_snapshot(second, count=3 if second == BTC else 5))
    assert {(s.symbol, s.combo) for s in signals} == {(BTC, 1), (ETH, 1)}
    assert l2.lab5.calls == [(OPEN_MS, (BTC, ETH))]
    assert l2.calculate(_snapshot(first)) == []
    assert l2.calculate(_snapshot(second)) == []
    assert len(l2.lab5.calls) == 1


def test_concurrent_callbacks_do_not_compute_the_pair_twice():
    l2 = _l2()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(l2.calculate, (_snapshot(BTC), _snapshot(ETH))))
    assert sum(map(len, results)) == 2
    assert len(l2.lab5.calls) == 1


def test_lagging_symbol_and_old_snapshot_do_not_replay():
    l2 = _l2()
    l2.calculate(_snapshot(BTC))
    l2.calculate(_snapshot(ETH))
    assert l2.calculate(_snapshot(BTC, OPEN_MS + M15_MS)) == []
    assert l2.calculate(_snapshot(BTC, OPEN_MS)) == []  # older callback
    got = l2.calculate(_snapshot(ETH, OPEN_MS + M15_MS))
    assert {s.symbol for s in got} == {BTC, ETH}
    assert [x[0] for x in l2.lab5.calls] == [OPEN_MS, OPEN_MS + M15_MS]


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
    assert called == []  # pre-start closed candle

    # Even a current snapshot must not be fired by a replayed older WS frame.
    newer = _snapshot(BTC, OPEN_MS + M15_MS)
    rt.started_ms = OPEN_MS
    rt.l1.ingest = lambda symbol, frame: newer
    rt.on_closed(BTC, _frame(OPEN_MS + M15_MS).tail(1))
    assert called == [BTC]
    # A new Runtime simulates a delayed WS backfill with a DB snapshot ahead.
    rt._processed = {}
    rt.on_closed(BTC, _frame(OPEN_MS).tail(1))
    assert called == [BTC]
