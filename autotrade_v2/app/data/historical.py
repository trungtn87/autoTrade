from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

import httpx
import pandas as pd

from ..control.errors import DataLayerError

log = logging.getLogger(__name__)
STEP_15M_MS = 15 * 60_000


def _frame_from_payload(payload: dict) -> pd.DataFrame:
    data = payload.get("data") or []
    if isinstance(data, dict):
        data = data.get("data") or data.get("list") or data.get("rows") or []
    rows = []
    for row in data:
        if isinstance(row, dict):
            t = row.get("openTime", row.get("time"))
            if t is None:
                continue
            T = row.get("closeTime")
            if T is None:
                T = int(t) + STEP_15M_MS - 1
            rows.append([t,row.get("open"),row.get("high"),row.get("low"),row.get("close"),row.get("volume"),T])
        elif isinstance(row, (list, tuple)) and len(row) >= 7:
            rows.append(list(row[:7]))
    cols=["open_time","open","high","low","close","volume","close_time"]
    df=pd.DataFrame(rows,columns=cols)
    if df.empty:
        return df
    for c in cols:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df=df.dropna().copy()
    df["open_time"]=df["open_time"].astype("int64")
    df["close_time"]=df["close_time"].astype("int64")
    return df.sort_values("open_time").drop_duplicates("open_time",keep="last").reset_index(drop=True)


@dataclass
class HistoricalKlineClient:
    base_url: str = "https://open-api.bingx.com"
    timeout: float = 20.0
    min_interval_sec: float = 1.10
    request_limit: int = 1440

    def __post_init__(self) -> None:
        self._client=httpx.Client(timeout=self.timeout,headers={
            "User-Agent":"autotrade-v2/2.0",
            "X-SOURCE-KEY":"BX-AI-SKILL",
        })
        self._lock=threading.Lock()
        self._last_call=0.0

    def _wait_slot(self) -> None:
        with self._lock:
            wait=self.min_interval_sec-(time.monotonic()-self._last_call)
            if wait>0:
                time.sleep(wait)
            self._last_call=time.monotonic()

    def fetch_window(self,symbol:str,start_time:int,end_time:int,limit:int) -> pd.DataFrame:
        if not re.fullmatch(r"[A-Z0-9]+-[A-Z]+",symbol):
            raise ValueError(f"invalid BingX symbol: {symbol}")
        limit=int(limit)
        if not 1<=limit<=self.request_limit:
            raise ValueError(f"historical limit must be 1..{self.request_limit}")
        path="/openApi/swap/v3/quote/klines"
        params={
            "symbol":symbol,
            "interval":"15m",
            "startTime":int(start_time),
            "endTime":int(end_time),
            "limit":limit,
        }
        self._wait_slot()
        log.info("HIST_REQUEST symbol=%s start=%s end=%s limit=%s",symbol,start_time,end_time,limit)
        try:
            r=self._client.get(self.base_url+path,params=params)
            payload=r.json()
        except Exception as exc:
            raise DataLayerError(f"historical transport failed: {type(exc).__name__}: {exc}") from exc
        code=payload.get("code") if isinstance(payload,dict) else None
        msg=str(payload.get("msg","")) if isinstance(payload,dict) else ""
        if r.status_code!=200 or code not in (0,"0"):
            raise DataLayerError(
                f"historical BingX error status={r.status_code} code={code} msg={msg} params={params}"
            )
        return _frame_from_payload(payload)

    def fetch_history(self,symbol:str,bars:int,now_ms:int|None=None) -> pd.DataFrame:
        bars=int(bars)
        if bars<=0:
            raise ValueError("bars must be positive")
        now_ms=int(now_ms or time.time()*1000)
        latest_open=(now_ms//STEP_15M_MS)*STEP_15M_MS-STEP_15M_MS
        first_open=latest_open-(bars-1)*STEP_15M_MS
        frames=[]
        cursor=first_open
        while cursor<=latest_open:
            remaining=((latest_open-cursor)//STEP_15M_MS)+1
            take=min(self.request_limit,int(remaining))
            end_open=cursor+(take-1)*STEP_15M_MS
            frame=self.fetch_window(
                symbol,
                cursor,
                end_open+STEP_15M_MS-1,
                take,
            )
            frames.append(frame)
            cursor=end_open+STEP_15M_MS
        if not frames:
            return pd.DataFrame()
        out=pd.concat(frames,ignore_index=True)
        out=(out[(out["open_time"]>=first_open)&(out["open_time"]<=latest_open)]
             .sort_values("open_time").drop_duplicates("open_time",keep="last").reset_index(drop=True))
        if len(out)!=bars:
            raise DataLayerError(f"{symbol} historical bootstrap count mismatch: got={len(out)} expected={bars}")
        expected=pd.Series(range(first_open,latest_open+STEP_15M_MS,STEP_15M_MS),dtype="int64")
        if not out["open_time"].reset_index(drop=True).equals(expected):
            raise DataLayerError(f"{symbol} historical bootstrap contains a gap or misaligned candle")
        return out
