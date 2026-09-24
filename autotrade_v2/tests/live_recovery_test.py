from __future__ import annotations

import os
import tempfile

import pandas as pd

from app.data.service import DataService
from app.data.store import CandleStore
from utils import synthetic_15m


class FakeHistorical:
    request_limit=1000

    def __init__(self, source: pd.DataFrame):
        self.source=source.copy(deep=True)
        self.calls=[]

    def fetch_window(self,symbol,start_time,end_time,limit):
        self.calls.append((symbol,start_time,end_time,limit))
        out=self.source[
            (self.source["open_time"]>=int(start_time)) &
            (self.source["open_time"]<=int(end_time))
        ].head(int(limit)).copy(deep=True)
        return out.reset_index(drop=True)


def main()->None:
    full=synthetic_15m(3401)
    initial=full.iloc[:3400].copy(deep=True)
    final_candle=full.iloc[[3400]].copy(deep=True)

    fd,path=tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store=CandleStore(path)
        store.upsert("BTC-USDT","15m",initial)
        historical=FakeHistorical(full)
        service=DataService(historical,store,keep=3400,required=3400)

        expected_open=int(final_candle.iloc[0]["open_time"])
        now_ms=int(final_candle.iloc[0]["close_time"])+5_000

        snapshot,missing=service.recover_missing_closed("BTC-USDT",now_ms=now_ms)
        assert missing==[expected_open]
        assert len(historical.calls)==1
        assert historical.calls[0][1]==expected_open
        assert historical.calls[0][3]==1
        assert snapshot.latest_open_time==expected_open
        assert len(snapshot.candles)==3400
        assert store.count("BTC-USDT","15m")==3400

        snapshot2,missing2=service.recover_missing_closed("BTC-USDT",now_ms=now_ms)
        assert missing2==[]
        assert len(historical.calls)==1
        assert snapshot2.latest_open_time==expected_open

        print({"ok":True,"recovered":1,"rest_calls":1,"candles":3400})
    finally:
        os.unlink(path)


if __name__=="__main__":
    main()
