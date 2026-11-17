from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from autotrade_v3.live.candles import service as svc
from autotrade_v3.live.candles.errors import DataLayerError
from autotrade_v3.live.l1_data import BACKFILL_RETRY_SEC, L1Data
from autotrade_v3.live.store import CandleStore

STEP = 15 * 60_000
LATEST = 1_794_917_700_000  # latest closed 15m bar in these offline tests
NOW = LATEST + STEP + 1


class FakeHistory:
    request_limit = 1000

    def __init__(self, bars: pd.DataFrame):
        self.bars = bars
        self.calls = 0
        self.windows = []

    def fetch_window(self, symbol, start_time, end_time, limit):
        self.calls += 1
        self.windows.append((symbol, start_time, end_time, limit))
        rows = self.bars[
            (self.bars.open_time >= start_time) & (self.bars.open_time <= end_time - STEP + 1)
        ]
        return rows.head(limit).reset_index(drop=True)


def _bars(n: int) -> pd.DataFrame:
    opens = [LATEST - (n - 1 - i) * STEP for i in range(n)]
    return pd.DataFrame({
        "open_time": opens,
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "volume": 1.0,
        "close_time": [o + STEP - 1 for o in opens],
    })


def _initial(n: int = 12_000, keep: int = 12_000):
    bars = _bars(n)
    store = CandleStore()
    store.upsert("ETH-USDT", "15m", bars.tail(3400))
    hist = FakeHistory(bars)
    service = svc.DataService(hist, store, keep=keep, required=3400, recover_window=keep)
    return store, hist, service


def test_each_backfill_call_saves_one_page_and_keeps_snapshot_contiguous():
    store, hist, service = _initial()
    first_existing = int(store.load("ETH-USDT", "15m").iloc[0].open_time)
    assert not service.backfill_complete("ETH-USDT", NOW)

    for page in range(1, 10):
        saved = service.backfill_one_page("ETH-USDT", NOW)
        assert saved == (1000 if page < 9 else 600)
        expected_total = 3400 + min(page * 1000, 8600)
        assert store.stats("ETH-USDT", "15m")["count"] == expected_total
        # All live strategies keep receiving a valid, contiguous recent window.
        snapshot = service.snapshot_from_store("ETH-USDT", NOW)
        assert len(snapshot.candles) == expected_total
        assert snapshot.validation["gap_count"] == 0
        assert hist.calls == page

    assert hist.windows[0][2] == first_existing - 1
    assert hist.windows[0][3] == 1000
    assert service.backfill_complete("ETH-USDT", NOW)
    assert service.backfill_one_page("ETH-USDT", NOW) == 0
    assert hist.calls == 9  # never make a tenth, redundant REST request


def test_six_request_budget_preserves_six_pages_and_resumes_after_cooldown():
    store, hist, service = _initial()
    for _ in range(svc.RECOVERY_REST_MAX_REQUESTS):
        assert service.backfill_one_page("ETH-USDT", NOW) == 1000
    assert store.stats("ETH-USDT", "15m")["count"] == 9400

    with pytest.raises(DataLayerError, match="budget exhausted"):
        service.backfill_one_page("ETH-USDT", NOW)
    assert hist.calls == svc.RECOVERY_REST_MAX_REQUESTS
    assert store.stats("ETH-USDT", "15m")["count"] == 9400

    service._recovery_rest_calls.clear()  # simulate the 15-minute rolling window elapsing
    assert [service.backfill_one_page("ETH-USDT", NOW) for _ in range(3)] == [1000, 1000, 600]
    assert service.backfill_complete("ETH-USDT", NOW)
    assert store.stats("ETH-USDT", "15m")["count"] == 12_000


def test_restart_continues_from_db_without_repeating_successful_pages():
    store, hist, service = _initial()
    assert service.backfill_one_page("ETH-USDT", NOW) == 1000
    assert service.backfill_one_page("ETH-USDT", NOW) == 1000
    first_after_two = int(store.load("ETH-USDT", "15m").iloc[0].open_time)
    replacement = svc.DataService(FakeHistory(_bars(12_000)), store,
                                  keep=12_000, required=3400, recover_window=12_000)
    assert replacement.backfill_one_page("ETH-USDT", NOW) == 1000
    assert store.stats("ETH-USDT", "15m")["count"] == 6400
    assert int(store.load("ETH-USDT", "15m").iloc[0].open_time) == first_after_two - 1000 * STEP
    assert replacement.historical.calls == 1


def test_100410_cooldown_does_not_change_database():
    store, _, service = _initial()

    class RateLimited(FakeHistory):
        def fetch_window(self, symbol, start_time, end_time, limit):
            self.calls += 1
            raise DataLayerError("historical BingX error status=200 code=100410 msg=retry")

    hist = RateLimited(_bars(12_000))
    service.historical = hist
    with pytest.raises(DataLayerError, match="code=100410"):
        service.backfill_one_page("ETH-USDT", NOW)
    assert store.stats("ETH-USDT", "15m")["count"] == 3400
    assert service.recovery_rest_status()["cooldown_remaining_sec"] > 0
    with pytest.raises(DataLayerError, match="cooldown active"):
        service.backfill_one_page("ETH-USDT", NOW)
    assert hist.calls == 1


@pytest.mark.parametrize("bad_kind", ["empty", "negative_volume", "misaligned_open"])
def test_invalid_rest_page_is_not_persisted(bad_kind):
    store, _, service = _initial()

    class Malformed(FakeHistory):
        def fetch_window(self, symbol, start_time, end_time, limit):
            result = super().fetch_window(symbol, start_time, end_time, limit)
            if bad_kind == "empty":
                return result.iloc[0:0].copy()
            if bad_kind == "negative_volume":
                result.loc[0, "volume"] = -1.0
            if bad_kind == "misaligned_open":
                result.loc[0, "open_time"] += 1
            return result

    service.historical = Malformed(_bars(12_000))
    with pytest.raises(DataLayerError):
        service.backfill_one_page("ETH-USDT", NOW)
    assert store.stats("ETH-USDT", "15m")["count"] == 3400


def test_watchdog_repairs_only_recent_3400_not_whole_12000():
    store, hist, service = _initial()
    snapshot, missing = service.recover_missing_closed("ETH-USDT", NOW)
    assert missing == []
    assert len(snapshot.candles) == 3400
    assert hist.calls == 0

    df = store.load("ETH-USDT", "15m")
    # Simulate one recent missed WebSocket bar. Preserve the original older 3399.
    missing_open = int(df.iloc[-5].open_time)
    store._run("DELETE FROM candles WHERE symbol=? AND timeframe=? AND open_time=?",
               ("ETH-USDT", "15m", missing_open))
    snapshot, missing = service.recover_missing_closed("ETH-USDT", NOW)
    assert missing == [missing_open]
    assert len(snapshot.candles) == 3400
    assert hist.calls == 1


def test_btc_keeps_3400_without_historical_backfill():
    store = CandleStore()
    bars = _bars(3400)
    store.upsert("BTC-USDT", "15m", bars)
    hist = FakeHistory(bars)
    service = svc.DataService(hist, store, keep=3400, required=3400)
    assert service.backfill_complete("BTC-USDT", NOW)
    assert service.backfill_one_page("BTC-USDT", NOW) == 0
    snap, missing = service.recover_missing_closed("BTC-USDT", NOW)
    assert missing == [] and len(snap.candles) == 3400 and hist.calls == 0


def test_keep_limit_can_be_raised_without_backfill_logic_changes():
    keep = 14_000
    store, hist, service = _initial(n=keep, keep=keep)
    for _ in range(11):
        assert service.backfill_one_page("ETH-USDT", NOW) > 0
        if service.recovery_rest_status()["used_requests"] == svc.RECOVERY_REST_MAX_REQUESTS:
            service._recovery_rest_calls.clear()
    assert service.backfill_complete("ETH-USDT", NOW)
    assert store.stats("ETH-USDT", "15m")["count"] == keep
    assert hist.calls == 11


def test_backfill_loop_calls_one_page_then_sleeps_15_minutes():
    assert BACKFILL_RETRY_SEC == 15 * 60

    class StopAfterFirstWait:
        stopped = False
        waits = []

        def is_set(self):
            return self.stopped

        def wait(self, sec):
            self.waits.append(sec)
            self.stopped = True
            return True

    class OnePage:
        calls = 0

        def backfill_complete(self, _):
            return False

        def backfill_one_page(self, _):
            self.calls += 1
            return 1000

    l1 = L1Data.__new__(L1Data)
    l1.services = {"ETH-USDT": OnePage()}
    l1.settings = SimpleNamespace(candle_keep_for=lambda _: 12_000)
    l1._stop = StopAfterFirstWait()
    l1._stats_count = lambda _: 4400
    l1.backfill_loop("ETH-USDT")
    assert l1.services["ETH-USDT"].calls == 1
    assert l1._stop.waits == [BACKFILL_RETRY_SEC]
