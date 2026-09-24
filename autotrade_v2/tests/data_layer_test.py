from __future__ import annotations

import os
import tempfile

from app.data.service import DataService
from app.data.store import CandleStore
from utils import synthetic_15m


class FakeMarket:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def klines(self, symbol, interval, limit):
        self.calls.append((symbol, interval, limit))
        return self.frame.tail(limit).copy(deep=True)


def main() -> None:
    candles = synthetic_15m(3400)
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = CandleStore(path)
        store.upsert("BTC-USDT", "15m", candles)
        market = FakeMarket(candles)
        service = DataService(market, store, fetch_limit=1000, keep=3400, required=3400)
        now_ms = int(candles.iloc[-1]["close_time"]) + 5_000
        snapshot = service.refresh("BTC-USDT", now_ms=now_ms)

        assert market.calls == [("BTC-USDT", "15m", 1000)]
        assert len(snapshot.candles) == 3400
        assert snapshot.validation["ok"] is True
        assert snapshot.latest_open_time == int(candles.iloc[-1]["open_time"])
        print({"ok": True, "market_calls": len(market.calls), "candles": len(snapshot.candles)})
    finally:
        os.unlink(path)


if __name__ == "__main__":
    main()
