from __future__ import annotations

import pandas as pd
import pytest

from autotrade_v3.live.candles import service as svc
from autotrade_v3.live.candles.errors import DataLayerError
from autotrade_v3.live.store import CandleStore

STEP = 15 * 60_000
LATEST = 1_794_917_700_000  # a 15m open_time boundary used as the latest closed bar in tests


class FakeHistory:
    request_limit = 1000

    def __init__(self, bars: pd.DataFrame):
        self.bars = bars
        self.calls = 0

    def fetch_window(self, symbol, start_time, end_time, limit):
        self.calls += 1
        rows = self.bars[(self.bars.open_time >= start_time) & (self.bars.open_time <= end_time - STEP + 1)]
        return rows.head(limit).reset_index(drop=True)


def _bars(n: int) -> pd.DataFrame:
    opens = [LATEST - (n - 1 - i) * STEP for i in range(n)]
    return pd.DataFrame({"open_time": opens, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                         "volume": 1.0, "close_time": [o + STEP - 1 for o in opens]})


def test_recover_window_fills_12000_bars_within_rest_budget(monkeypatch):
    monkeypatch.setattr(svc, "RECOVERY_REST_MAX_REQUESTS", 20)
    store = CandleStore()
    bars = _bars(12_000)
    store.upsert("ETH-USDT", "15m", bars.tail(3400))          # DB holds only the newest 3400
    service = svc.DataService(FakeHistory(bars), store, keep=12_000, required=3400, recover_window=12_000)
    snap, missing = service.recover_missing_closed("ETH-USDT", LATEST + STEP + 1)
    assert store.stats("ETH-USDT", "15m")["count"] == 12_000
    assert len(missing) == 8_600
    assert len(snap.candles) == 12_000


def test_default_budget_stops_after_six_requests_and_retries_later(monkeypatch):
    store = CandleStore()
    bars = _bars(12_000)
    store.upsert("ETH-USDT", "15m", bars.tail(3400))
    hist = FakeHistory(bars)
    service = svc.DataService(hist, store, keep=12_000, required=3400, recover_window=12_000)
    with pytest.raises(DataLayerError, match="budget"):
        service.recover_missing_closed("ETH-USDT", LATEST + STEP + 1)
    assert hist.calls == svc.RECOVERY_REST_MAX_REQUESTS
    assert store.stats("ETH-USDT", "15m")["count"] == 3400   # nothing partial was written


def test_btc_keeps_3400_and_never_fetches_beyond_required(monkeypatch):
    monkeypatch.setattr(svc, "RECOVERY_REST_MAX_REQUESTS", 20)
    store = CandleStore()
    bars = _bars(3_400)
    store.upsert("BTC-USDT", "15m", bars)
    hist = FakeHistory(bars)
    service = svc.DataService(hist, store, keep=3400, required=3400)
    _, missing = service.recover_missing_closed("BTC-USDT", LATEST + STEP + 1)
    assert missing == [] and hist.calls == 0
