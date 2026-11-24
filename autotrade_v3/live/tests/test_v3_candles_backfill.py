from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from autotrade_v3.live.candles import service as svc
from autotrade_v3.live.candles.errors import DataLayerError, StrategyInsufficientCandlesError
from autotrade_v3.live.config import V3Settings
from autotrade_v3.live.l1_data import L1Data
from autotrade_v3.live.store import CandleStore

STEP = 15 * 60_000
LATEST = 1_794_917_700_000
NOW = LATEST + STEP + 1
BTC = "BTC-USDT"
ETH = "ETH-USDT"


class FakeHistory:
    request_limit = 1000

    def __init__(self, bars: pd.DataFrame):
        self.bars = bars
        self.calls = 0
        self.windows: list[tuple] = []

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


def _service(symbol: str, stored: int | None = None):
    retention = 13_440 if symbol == BTC else 25_920
    snapshot_window = retention
    bars = _bars(retention)
    store = CandleStore()
    store.upsert(symbol, "15m", bars.tail(stored or retention))
    history = FakeHistory(bars)
    service = svc.DataService(
        history,
        store,
        retention=retention,
        signal_window=snapshot_window,
        required=snapshot_window,
    )
    return bars, store, history, service


def test_plus44_retention_defaults_and_signal_windows_are_separate(monkeypatch):
    monkeypatch.delenv("V3_CANDLE_KEEP_ETH_15M", raising=False)
    settings = V3Settings()
    assert settings.candle_retention_for(BTC) == 13_440
    assert settings.candle_retention_for(ETH) == 25_920
    assert settings.snapshot_window_for(BTC) == 13_440
    assert settings.snapshot_window_for(ETH) == 25_920
    assert settings.required_15m_for(BTC) == 13_440
    assert settings.required_15m_for(ETH) == 25_920
    assert settings.signal_window_for(BTC) == 3_400
    assert settings.signal_window_for(ETH) == 12_000
    assert not hasattr(settings, "required_15m")


def test_l1_data_configures_per_coin_snapshot_and_required_windows():
    settings = V3Settings(database_url="")
    l1 = L1Data(settings, CandleStore(), SimpleNamespace(report=lambda *a, **kw: None))
    assert l1.services[BTC].retention == 13_440
    assert l1.services[BTC].signal_window == 13_440
    assert l1.services[BTC].required == 13_440
    assert l1.services[BTC].recovery_window == 3_400
    assert l1.services[ETH].retention == 25_920
    assert l1.services[ETH].signal_window == 25_920
    assert l1.services[ETH].required == 25_920
    assert l1.services[ETH].recovery_window == 3_400


def test_stale_live_eth_environment_value_cannot_downgrade_retention(monkeypatch):
    monkeypatch.setenv("V3_CANDLE_KEEP_ETH_15M", "12000")
    settings = V3Settings()
    assert settings.candle_retention_for(BTC) == 13_440
    assert settings.candle_retention_for(ETH) == 25_920


def test_eth_environment_can_raise_but_not_lower_approved_retention(monkeypatch):
    monkeypatch.setenv("V3_CANDLE_KEEP_ETH_15M", "30000")
    settings = V3Settings()
    assert settings.candle_retention_for(BTC) == 13_440
    assert settings.candle_retention_for(ETH) == 30_000


@pytest.mark.parametrize(
    "symbol,retention,snapshot_window",
    [(BTC, 13_440, 13_440), (ETH, 25_920, 25_920)],
)
def test_snapshot_uses_full_per_coin_window(symbol, retention, snapshot_window):
    bars, store, history, service = _service(symbol)
    snapshot = service.snapshot_from_store(symbol, NOW)
    assert len(snapshot.candles) == snapshot_window
    assert store.stats(symbol, "15m")["count"] == retention
    assert int(snapshot.candles.iloc[0].open_time) == int(bars.iloc[-snapshot_window].open_time)
    assert history.calls == 0


@pytest.mark.parametrize(
    "symbol,stored,required",
    [
        (BTC, 3_400, 13_440),
        (BTC, 13_439, 13_440),
        (ETH, 12_000, 25_920),
        (ETH, 25_919, 25_920),
    ],
)
def test_snapshot_raises_insufficient_candles_per_coin(symbol, stored, required):
    _, _, history, service = _service(symbol, stored=stored)
    with pytest.raises(StrategyInsufficientCandlesError) as exc_info:
        service.snapshot_from_store(symbol, NOW)
    assert exc_info.value.symbol == symbol
    assert exc_info.value.available == stored
    assert exc_info.value.required == required
    assert exc_info.value.missing == required - stored
    assert history.calls == 0


@pytest.mark.parametrize(
    "symbol,retention,snapshot_window",
    [(BTC, 13_440, 13_440), (ETH, 25_920, 25_920)],
)
def test_ingest_over_retention_deletes_only_oldest(symbol, retention, snapshot_window):
    _, store, _, service = _service(symbol)
    before = store.load(symbol, "15m")
    first_before = int(before.iloc[0].open_time)
    second_before = int(before.iloc[1].open_time)
    next_open = int(before.iloc[-1].open_time) + STEP
    new_candle = pd.DataFrame({
        "open_time": [next_open], "open": [1.0], "high": [1.0],
        "low": [1.0], "close": [1.0], "volume": [1.0],
        "close_time": [next_open + STEP - 1],
    })

    snapshot = service.ingest_closed_candle(symbol, new_candle, now_ms=next_open + STEP + 1)
    after = store.load(symbol, "15m")
    assert len(snapshot.candles) == snapshot_window
    assert len(after) == retention
    assert first_before not in set(after.open_time.astype(int))
    assert int(after.iloc[0].open_time) == second_before
    assert int(after.iloc[-1].open_time) == next_open


@pytest.mark.parametrize("symbol,retention", [(BTC, 13_440), (ETH, 25_920)])
def test_ingest_below_retention_does_not_delete_existing_candles(symbol, retention):
    stored = retention - 1
    _, store, _, service = _service(symbol, stored=stored)
    before = store.load(symbol, "15m")
    first_before = int(before.iloc[0].open_time)
    next_open = int(before.iloc[-1].open_time) + STEP
    new_candle = pd.DataFrame({
        "open_time": [next_open], "open": [1.0], "high": [1.0],
        "low": [1.0], "close": [1.0], "volume": [1.0],
        "close_time": [next_open + STEP - 1],
    })

    service.ingest_closed_candle(symbol, new_candle, now_ms=next_open + STEP + 1)
    after = store.load(symbol, "15m")
    assert len(after) == retention
    assert int(after.iloc[0].open_time) == first_before


def test_retention_growth_never_requests_deep_bingx_history():
    _, store, history, service = _service(ETH, stored=12_000)
    with pytest.raises(StrategyInsufficientCandlesError) as exc_info:
        service.recover_missing_closed(ETH, NOW)
    assert exc_info.value.available == 12_000
    assert exc_info.value.required == 25_920
    assert store.stats(ETH, "15m")["count"] == 12_000
    assert history.calls == 0
    assert not hasattr(service, "backfill_one_page")
    assert not hasattr(L1Data, "backfill_loop")


def test_gap_older_than_realtime_window_is_not_backfilled_from_bingx():
    _, store, history, service = _service(ETH, stored=25_920)
    older_open = int(store.load(ETH, "15m").iloc[-5_000].open_time)
    store._run(
        "DELETE FROM candles WHERE symbol=? AND timeframe=? AND open_time=?",
        (ETH, "15m", older_open),
    )
    with pytest.raises(StrategyInsufficientCandlesError) as exc_info:
        service.recover_missing_closed(ETH, NOW)
    assert exc_info.value.available == 25_919
    assert exc_info.value.required == 25_920
    assert history.calls == 0

    # Even when retention exceeds 25,920 so 25,920 rows still load, validator catches the gap without REST.
    bars = _bars(25_930)
    extra_store = CandleStore()
    extra_store.upsert(ETH, "15m", bars)
    extra_history = FakeHistory(bars)
    extra_service = svc.DataService(
        extra_history,
        extra_store,
        retention=25_930,
        signal_window=25_920,
        required=25_920,
    )
    extra_open = int(extra_store.load(ETH, "15m").iloc[-5_000].open_time)
    extra_store._run(
        "DELETE FROM candles WHERE symbol=? AND timeframe=? AND open_time=?",
        (ETH, "15m", extra_open),
    )
    with pytest.raises(DataLayerError, match="validation failed"):
        extra_service.recover_missing_closed(ETH, NOW)
    assert extra_history.calls == 0


def test_watchdog_repairs_one_recent_missing_candle_only():
    _, store, history, service = _service(ETH, stored=25_920)
    missing_open = int(store.load(ETH, "15m").iloc[-5].open_time)
    store._run(
        "DELETE FROM candles WHERE symbol=? AND timeframe=? AND open_time=?",
        (ETH, "15m", missing_open),
    )
    snapshot, missing = service.recover_missing_closed(ETH, NOW)
    assert missing == [missing_open]
    assert len(snapshot.candles) == 25_920
    assert history.calls == 1
    assert history.windows == [(ETH, missing_open, missing_open + STEP - 1, 1)]


def test_invalid_recent_recovery_page_is_not_persisted():
    _, store, _, service = _service(BTC, stored=13_440)
    missing_open = int(store.load(BTC, "15m").iloc[-5].open_time)
    store._run(
        "DELETE FROM candles WHERE symbol=? AND timeframe=? AND open_time=?",
        (BTC, "15m", missing_open),
    )
    before = store.stats(BTC, "15m")["count"]

    class Malformed(FakeHistory):
        def fetch_window(self, symbol, start_time, end_time, limit):
            result = super().fetch_window(symbol, start_time, end_time, limit)
            result.loc[0, "volume"] = -1.0
            return result

    service.historical = Malformed(_bars(13_440))
    with pytest.raises(DataLayerError, match="OHLCV invalid"):
        service.recover_missing_closed(BTC, NOW)
    assert store.stats(BTC, "15m")["count"] == before


def test_startup_sync_has_no_historical_backfill_or_entry_callback():
    calls: list[str] = []

    class RecentOnly:
        def recover_missing_closed(self, symbol, now_ms=None):
            calls.append(symbol)
            return SimpleNamespace(latest_open_time=LATEST), []

    l1 = L1Data.__new__(L1Data)
    l1.settings = SimpleNamespace(symbols=(BTC, ETH))
    l1.services = {BTC: RecentOnly(), ETH: RecentOnly()}
    l1.health = {}
    l1.startup_sync()
    assert calls == [BTC, ETH]
    assert all(item["source"] == "startup" for item in l1.health.values())
