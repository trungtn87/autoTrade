from __future__ import annotations

import json

from app.data.websocket import BingXKlineStream


def msg(symbol, t, o, h, l, c, v):
    return json.dumps({
        "dataType": f"{symbol}@kline_15m",
        "data": {
            "s": symbol,
            "K": {
                "s": symbol,
                "t": t,
                "T": t + 900_000 - 1,
                "o": str(o),
                "h": str(h),
                "l": str(l),
                "c": str(c),
                "v": str(v),
            },
        },
    })


def main() -> None:
    closed=[]
    stream=BingXKlineStream(("BTC-USDT",), lambda symbol, frame: closed.append((symbol, frame.copy())))

    t0=1_800_000_000_000
    stream._handle_text(msg("BTC-USDT",t0,100,102,99,101,10))
    assert closed==[], "current candle must not be finalized immediately"

    stream._handle_text(msg("BTC-USDT",t0+900_000,101,103,100,102,11))
    assert len(closed)==1
    symbol,frame=closed[0]
    assert symbol=="BTC-USDT"
    assert int(frame.iloc[0]["open_time"])==t0
    assert int(frame.iloc[0]["close_time"])==t0+900_000-1

    stream._handle_text(msg("BTC-USDT",t0,100,104,98,103,12))
    assert len(closed)==1

    stream.connected=True
    stream._connection_started_ms=1_000
    stream._connection_kline_seen=False
    assert not stream._market_data_stale(60_000)
    assert stream._market_data_stale(61_001)

    stream._connection_kline_seen=True
    stream.last_kline_ms=100_000
    assert not stream._market_data_stale(190_000)
    assert stream._market_data_stale(190_001)

    wrapped=json.dumps({
        "code":0,
        "dataType":"BTC-USDT@kline_15m",
        "data":{"wrapped":{"K":{
            "t":t0+1_800_000,
            "T":t0+2_700_000-1,
            "o":"102","h":"104","l":"101","c":"103","v":"12",
        }}},
    })
    stream2=BingXKlineStream(("BTC-USDT",), lambda symbol, frame: None)
    assert stream2._handle_text(wrapped)
    assert stream2._pending["BTC-USDT"]["open_time"]==t0+1_800_000

    print({"ok":True,"finalized":1,"duplicates":0,"stale_watchdog":True,"wrapped_payload":True})


if __name__=="__main__":
    main()
