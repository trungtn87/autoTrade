from __future__ import annotations

import os
import tempfile
from dataclasses import asdict

from app import main
from app.final14_exact_strategy import scan_latest
from app.self_test import _synthetic_15m
from app.state import SignalState


def run() -> None:
    candles = _synthetic_15m(3400)
    latest_window = candles.tail(1000).copy(deep=True)

    expected = {
        symbol: [asdict(sig) for sig in scan_latest(symbol=symbol, m15=candles)]
        for symbol in ("BTC-USDT", "ETH-USDT")
    }

    fd, db_path = tempfile.mkstemp(prefix="single-window-", suffix=".db")
    os.close(fd)
    old_state = main.state
    old_market = main.market
    old_time = main.time.time

    class FakeMarket:
        def __init__(self):
            self.calls = []

        def klines(self, symbol, interval, limit, start_time=None, end_time=None):
            self.calls.append({
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
                "start_time": start_time,
                "end_time": end_time,
            })
            return latest_window.copy(deep=True)

    fake_market = FakeMarket()

    try:
        st = SignalState(db_path)
        st.upsert_candles("BTC-USDT", "15m", candles)
        main.state = st
        main.market = fake_market

        last_close = int(candles.iloc[-1]["close_time"])
        fake_now_ms = last_close + 2_000
        main.time.time = lambda: fake_now_ms / 1000.0

        _, result, bootstrap, validation = main.fetch_bundle("BTC-USDT")

        assert bootstrap is False
        assert validation["ok"] is True
        assert len(result) == 3400
        assert len(fake_market.calls) == 1, fake_market.calls

        call = fake_market.calls[0]
        assert call["interval"] == "15m"
        assert call["limit"] == 1000
        assert call["start_time"] is None
        assert call["end_time"] is None

        actual = {
            symbol: [asdict(sig) for sig in scan_latest(symbol=symbol, m15=result)]
            for symbol in ("BTC-USDT", "ETH-USDT")
        }
        assert actual == expected, {"expected": expected, "actual": actual}

        print({
            "ok": True,
            "kline_calls": len(fake_market.calls),
            "limit": call["limit"],
            "start_time": call["start_time"],
            "end_time": call["end_time"],
            "candles_after_upsert": len(result),
            "signal_parity": True,
        })
    finally:
        main.time.time = old_time
        main.state = old_state
        main.market = old_market
        try:
            os.unlink(db_path)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    run()
