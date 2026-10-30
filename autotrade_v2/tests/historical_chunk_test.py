from __future__ import annotations

import pandas as pd

from app.data.historical import HistoricalKlineClient, STEP_15M_MS
from utils import synthetic_15m


class FakeHistorical(HistoricalKlineClient):
    def __post_init__(self):
        self.calls=[]

    def fetch_window(self,symbol,start_time,end_time,limit):
        self.calls.append((symbol,start_time,end_time,limit))
        first=start_time
        rows=[]
        for i in range(limit):
            t=first+i*STEP_15M_MS
            if t+STEP_15M_MS-1>end_time:
                break
            rows.append({
                "open_time":t,"open":1.0,"high":2.0,"low":0.5,
                "close":1.5,"volume":10.0,"close_time":t+STEP_15M_MS-1,
            })
        return pd.DataFrame(rows)


def main():
    client=FakeHistorical(request_limit=1000)
    now_ms=1_800_000_000_000
    out=client.fetch_history("BTC-USDT",3400,now_ms=now_ms)
    assert len(out)==3400
    limits=[x[3] for x in client.calls]
    assert limits==[1000,1000,1000,400],limits
    print({"ok":True,"chunks":limits,"bars":len(out)})


if __name__=="__main__":
    main()
