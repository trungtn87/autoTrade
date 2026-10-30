from __future__ import annotations

import os
import tempfile

from app.data.service import DataService
from app.data.store import CandleStore
from utils import synthetic_15m


class FakeHistorical:
    def __init__(self, frame):
        self.frame=frame
        self.calls=[]

    def fetch_history(self,symbol,bars,now_ms=None):
        self.calls.append((symbol,bars,now_ms))
        return self.frame.tail(bars).copy(deep=True)


def main()->None:
    candles=synthetic_15m(3400)
    fd,path=tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store=CandleStore(path)
        historical=FakeHistorical(candles)
        service=DataService(historical,store,keep=3400,required=3400)
        now_ms=int(candles.iloc[-1]["close_time"])+5_000

        snapshot=service.bootstrap("BTC-USDT",now_ms=now_ms)
        assert len(historical.calls)==1
        assert historical.calls[0][0]=="BTC-USDT"
        assert historical.calls[0][1]==3400
        assert len(snapshot.candles)==3400

        # A ready DB must not call historical REST again.
        snapshot2=service.bootstrap("BTC-USDT",now_ms=now_ms)
        assert len(historical.calls)==1
        assert snapshot2.latest_open_time==snapshot.latest_open_time

        print({"ok":True,"bootstrap_calls":1,"candles":len(snapshot.candles)})
    finally:
        os.unlink(path)


if __name__=="__main__":
    main()
