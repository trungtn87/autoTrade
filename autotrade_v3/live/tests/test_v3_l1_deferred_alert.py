"""Regression for delayed L1 REST failures: a late WebSocket bar must not page Discord."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from autotrade_v3.live import l1_data as mod


STEP = mod.STEP_15M_MS
BOUNDARY = STEP * 12
EXPECTED = BOUNDARY - STEP
PREVIOUS = EXPECTED - STEP


class PollClock:
    def __init__(self, count: int):
        self.max_polls = count
        self.polls = 0

    def wait(self, seconds: float) -> bool:
        assert seconds == mod.WATCHDOG_POLL_SEC
        self.polls += 1
        return self.polls > self.max_polls

    @property
    def now_ms(self) -> int:
        return BOUNDARY + mod.CANDLE_GRACE_MS + (self.polls - 1) * 5_000


class FakeStore:
    def __init__(self):
        self.latest = PREVIOUS

    def load(self, symbol, timeframe, limit):
        assert (symbol, timeframe, limit) == ("BTC-USDT", "15m", 1)
        return pd.DataFrame({"open_time": [self.latest]})

    def stats(self, symbol, timeframe):
        return {"count": 3400, "latest_open_time": self.latest}


class RecordingControl:
    def __init__(self):
        self.rows = []

    def report(self, layer, step, message, **details):
        self.rows.append((layer, step, message, details))


def _watchdog_fixture(monkeypatch, polls: int):
    l1 = mod.L1Data.__new__(mod.L1Data)
    clock = PollClock(polls)
    l1._stop = clock
    l1._attempts = {}
    l1.health = {}
    l1.settings = SimpleNamespace(symbols=("BTC-USDT",))
    l1.store = FakeStore()
    l1.control = RecordingControl()
    calls = []

    def fail_recovery(symbol, now_ms):
        calls.append((symbol, now_ms))
        raise RuntimeError("historical BingX error status=200 code=100410")

    l1.recover = fail_recovery
    monkeypatch.setattr(mod.time, "time", lambda: clock.now_ms / 1000)
    return l1, clock, calls


def test_late_ws_cursor_suppresses_false_discord_error(monkeypatch):
    l1, clock, calls = _watchdog_fixture(monkeypatch, polls=10)
    l1.watchdog(
        lambda _: EXPECTED if clock.polls >= 3 else PREVIOUS,
        lambda *_: None,
    )
    assert len(calls) == 1
    assert l1.control.rows == []
    assert "first_failed_ms" not in l1._attempts[("BTC-USDT", EXPECTED)]


def test_db_candle_arrives_even_if_processed_cursor_lags(monkeypatch):
    l1, clock, calls = _watchdog_fixture(monkeypatch, polls=10)
    original_load = l1.store.load

    def delayed_db_load(symbol, timeframe, limit):
        if clock.polls >= 3:
            l1.store.latest = EXPECTED
        return original_load(symbol, timeframe, limit)

    l1.store.load = delayed_db_load
    l1.watchdog(lambda _: PREVIOUS, lambda *_: None)
    assert len(calls) == 1
    assert l1.control.rows == []


def test_genuine_missing_candle_alerts_once_after_delay(monkeypatch):
    l1, clock, calls = _watchdog_fixture(monkeypatch, polls=16)
    l1.watchdog(lambda _: PREVIOUS, lambda *_: None)
    assert len(calls) == 2  # existing 60s retry policy is unchanged
    assert len(l1.control.rows) == 1
    layer, step, message, details = l1.control.rows[0]
    assert (layer, step) == ("L1", "candle_recovery")
    assert "sau 30s xác minh" in message
    assert details["details"]["expected_open_time"] == EXPECTED
    assert details["details"]["rest_attempts"] == 1
    assert l1._attempts[("BTC-USDT", EXPECTED)]["alert_sent"] is True


def test_rest_recovery_success_does_not_generate_error(monkeypatch):
    l1, clock, calls = _watchdog_fixture(monkeypatch, polls=8)
    snap = SimpleNamespace(latest_open_time=EXPECTED)
    saved = []

    def success(symbol, now_ms):
        calls.append((symbol, now_ms))
        return snap, [EXPECTED]

    l1.recover = success
    l1.watchdog(lambda _: EXPECTED if saved else PREVIOUS, lambda *args: saved.append(args))
    assert len(calls) == 1
    assert len(saved) == 1
    assert l1.control.rows == []
