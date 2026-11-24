"""Offline regression: persisted L1 candles, automatic L2 retry, and safe history review.

The fake L2 never connects to BingX; tests assert the runtime makes exactly
the permitted handoffs. Test clocks are explicit so M15 expiry is deterministic.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from autotrade_v3.live.app import L2_RETRY_INTERVAL_MS, Runtime
from autotrade_v3.live.l1_data import L1Data
from autotrade_v3.live.l2_strategy import L2Strategy

M15 = 900_000
OPEN = 1_800_000_000_000
BTC = "BTC-USDT"
ETH = "ETH-USDT"


def _snapshot(symbol=BTC, opened=OPEN):
    return SimpleNamespace(
        symbol=symbol, latest_open_time=opened, latest_close_time=opened + M15 - 1,
        candles=SimpleNamespace(tail=lambda count: SimpleNamespace(symbol=symbol, opened=opened)),
    )


class Clock:
    def __init__(self, ms=OPEN + M15 + 45_000):
        self.ms = ms


class Control:
    def __init__(self):
        self.calls = []

    def report(self, layer, step, message, **kwargs):
        self.calls.append((layer, step, message, kwargs))


class FakeL2:
    def __init__(self):
        self.registry = SimpleNamespace(version="test")
        self.calculate_calls = []
        self.dispatch_calls = []
        self.preview_calls = []
        self.fail_calculate = set()
        self.fail_dispatch_once = False

    def calculate(self, snapshot):
        self.calculate_calls.append((snapshot.symbol, snapshot.latest_open_time))
        if snapshot.symbol in self.fail_calculate:
            self.fail_calculate.remove(snapshot.symbol)
            raise RuntimeError("mock transient L2 error")
        return [SimpleNamespace(event_id=f"{snapshot.symbol}:{snapshot.latest_open_time}")]

    def dispatch(self, signals):
        self.dispatch_calls.append([x.event_id for x in signals])
        if self.fail_dispatch_once:
            self.fail_dispatch_once = False
            raise RuntimeError("mock transient handoff error")
        return []

    def preview(self, snapshot):
        self.preview_calls.append((snapshot.symbol, snapshot.latest_open_time))
        return [SimpleNamespace(event_id=f"preview:{snapshot.symbol}:{snapshot.latest_open_time}")]


def _runtime(monkeypatch, clock=None, *, started_ms=OPEN):
    clock = clock or Clock()
    monkeypatch.setattr("autotrade_v3.live.app.time.time", lambda: clock.ms / 1000)
    rt = object.__new__(Runtime)
    rt.started_ms = started_ms
    rt._symbol_locks = {BTC: threading.Lock(), ETH: threading.Lock()}
    rt._processed = {}
    rt._l2_last_success = {}
    rt._l2_pending = {}
    rt._l2_pending_signals = {}
    rt._l2_retry_after = {}
    rt._history_preview = {}
    rt.last_run = {"status": "not_started"}
    rt.settings = SimpleNamespace(execution_enabled=True)
    rt.control = Control()
    rt.l2 = FakeL2()
    rt.l1 = SimpleNamespace(services={
        BTC: SimpleNamespace(snapshot_from_store=lambda symbol, now_ms=None: _snapshot(BTC)),
        ETH: SimpleNamespace(snapshot_from_store=lambda symbol, now_ms=None: _snapshot(ETH)),
    })
    return rt, clock


def test_l1_persisted_but_l2_error_retries_without_new_ws_or_rest(monkeypatch):
    rt, clock = _runtime(monkeypatch)
    rt.l2.fail_calculate.add(BTC)
    rt.on_recovered(BTC, _snapshot())
    assert rt.processed_open(BTC) == OPEN   # L1 marked persisted
    assert BTC not in rt._l2_last_success    # L2 was NOT successful
    assert BTC in rt._l2_pending
    assert len(rt.l2.calculate_calls) == 1

    rt.retry_pending(BTC)
    assert len(rt.l2.calculate_calls) == 1  # 60-second retry spacing
    clock.ms += L2_RETRY_INTERVAL_MS
    rt.retry_pending(BTC)
    assert len(rt.l2.calculate_calls) == 2
    assert rt._l2_last_success[BTC] == OPEN
    assert BTC not in rt._l2_pending
    assert rt.l2.dispatch_calls == [[f"{BTC}:{OPEN}"]]
    rt.retry_pending(BTC)
    assert len(rt.l2.dispatch_calls) == 1


def test_l2_error_on_btc_does_not_delay_eth(monkeypatch):
    rt, clock = _runtime(monkeypatch)
    rt.l2.fail_calculate.add(BTC)
    rt.on_recovered(BTC, _snapshot(BTC))
    rt.on_recovered(ETH, _snapshot(ETH))
    assert rt._l2_last_success[ETH] == OPEN
    assert BTC in rt._l2_pending
    assert rt.l2.dispatch_calls == [[f"{ETH}:{OPEN}"]]
    clock.ms += L2_RETRY_INTERVAL_MS
    rt.retry_pending(BTC)
    assert rt._l2_last_success[BTC] == OPEN
    assert len(rt.l2.dispatch_calls) == 2


def test_dispatch_exception_retries_cached_signals_not_recalculation(monkeypatch):
    rt, clock = _runtime(monkeypatch)
    rt.l2.fail_dispatch_once = True
    rt.on_recovered(BTC, _snapshot())
    assert len(rt.l2.calculate_calls) == 1
    assert BTC in rt._l2_pending_signals
    clock.ms += L2_RETRY_INTERVAL_MS
    rt.retry_pending(BTC)
    assert len(rt.l2.calculate_calls) == 1
    assert len(rt.l2.dispatch_calls) == 2
    assert rt._l2_last_success[BTC] == OPEN


def test_unresolved_previous_bar_expires_without_late_entry(monkeypatch):
    rt, clock = _runtime(monkeypatch)
    rt.l2.fail_calculate.add(BTC)
    rt.on_recovered(BTC, _snapshot())
    clock.ms = OPEN + 2 * M15 + 45_000
    rt.retry_pending(BTC)
    assert BTC not in rt._l2_pending
    assert rt.l2.dispatch_calls == []
    assert any(step == "strategy_retry_expired" for _, step, _, _ in rt.control.calls)
    # Recovery of an old bar after the next close is never executed.
    rt.on_recovered(BTC, _snapshot())
    assert rt.l2.dispatch_calls == []


def test_history_backfill_preview_cannot_call_live_l2_or_l3(monkeypatch):
    rt, _ = _runtime(monkeypatch, started_ms=OPEN + M15 + 2)
    rt.on_backfilled(BTC)
    assert rt.l2.calculate_calls == []
    assert rt.l2.dispatch_calls == []
    assert rt.l2.preview_calls == [(BTC, OPEN)]
    assert rt._history_preview[BTC]["mode"] == "read_only"
    assert rt._l2_last_success == {}


def test_backfill_of_new_current_bar_triggers_live_once(monkeypatch):
    rt, _ = _runtime(monkeypatch)
    rt.on_backfilled(BTC)
    assert rt._l2_last_success[BTC] == OPEN
    assert len(rt.l2.dispatch_calls) == 1
    # The second callback may re-check history, but cannot send the same bar.
    rt.on_backfilled(BTC)
    assert len(rt.l2.dispatch_calls) == 1
    assert rt.l2.preview_calls == [(BTC, OPEN), (BTC, OPEN)]


def test_l1_exposes_no_autonomous_deep_backfill_loop():
    assert not hasattr(L1Data, "backfill_loop")


def test_preview_on_real_l2_does_not_advance_cursor_or_dispatch():
    l2 = object.__new__(L2Strategy)
    l2.registry = object()
    l2._calculate_locks = {BTC: threading.Lock(), ETH: threading.Lock()}
    l2._last_calculated_open = {}
    l2._last_dispatched_by_combo = {}
    calls = []

    class Lab:
        def calculate(self, symbol, frames):
            calls.append((symbol, list(frames)))
            return [SimpleNamespace(event_id="preview-only")]

    l2.lab5 = Lab()
    snap = _snapshot(BTC)
    assert len(l2.preview(snap)) == 1
    assert l2._last_calculated_open == {}
    assert calls == [(BTC, [BTC])]
    assert len(l2.calculate(snap)) == 1  # same bar is still eligible for live L2

def test_watchdog_retries_l2_even_when_l1_candle_is_already_persisted(monkeypatch):
    # Watchdog must not call REST once L1 has the current candle, but it must
    # still offer the pending L2 calculation a retry.
    now_ms = OPEN + M15 + 45_000
    monkeypatch.setattr("autotrade_v3.live.l1_data.time.time", lambda: now_ms / 1000)

    class TwoPolls:
        calls = 0
        def wait(self, _seconds):
            self.calls += 1
            return self.calls > 1

    l1 = L1Data.__new__(L1Data)
    l1._stop = TwoPolls()
    l1._attempts = {}
    l1.settings = SimpleNamespace(symbols=(BTC, ETH))
    recovered = []
    retries = []
    l1.watchdog(lambda _symbol: OPEN, lambda *args: recovered.append(args), retries.append)
    assert recovered == []  # current bars already stored: no REST recovery
    assert retries == [BTC, ETH]


def test_backfill_forces_retry_when_new_history_repairs_l2_input(monkeypatch):
    rt, _ = _runtime(monkeypatch)
    rt.l2.fail_calculate.add(ETH)
    rt.on_recovered(ETH, _snapshot(ETH))
    assert ETH in rt._l2_pending
    # Newly persisted history triggers a fresh attempt even inside the
    # standard 60-second cooldown, without replaying other bars.
    rt.on_backfilled(ETH)
    assert rt._l2_last_success[ETH] == OPEN
    assert rt.l2.calculate_calls == [(ETH, OPEN), (ETH, OPEN)]
    assert rt.l2.dispatch_calls == [[f"{ETH}:{OPEN}"]]
