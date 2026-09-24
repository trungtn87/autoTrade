from __future__ import annotations

import asyncio
import gzip
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass

import pandas as pd
import websockets

log=logging.getLogger(__name__)
STEP_15M_MS=15*60_000


@dataclass
class BingXKlineStream:
    symbols: tuple[str,...]
    on_closed: object
    url: str = "wss://open-api-swap.bingx.com/swap-market"
    on_event: object | None = None

    def __post_init__(self)->None:
        self._stop=threading.Event()
        self._thread:threading.Thread|None=None
        self._pending:dict[str,dict]={}
        self._last_finalized:dict[str,int]={}
        self.connected=False
        self.reconnects=0
        self.last_message_ms=0
        self.last_error=""

    def start(self)->None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread=threading.Thread(target=self._run,name="bingx-kline-ws",daemon=True)
        self._thread.start()

    def stop(self)->None:
        self._stop.set()

    def status(self)->dict:
        return {
            "connected":self.connected,
            "reconnects":self.reconnects,
            "last_message_ms":self.last_message_ms,
            "last_error":self.last_error,
            "pending":{k:v.get("open_time") for k,v in self._pending.items()},
        }

    def _emit(self,key:str,severity:str,message:str,**kwargs)->None:
        if not self.on_event:
            return
        try:
            self.on_event(key,severity,message,**kwargs)
        except Exception:
            return

    def _run(self)->None:
        asyncio.run(self._loop())

    async def _loop(self)->None:
        backoff=2
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=None,
                    close_timeout=5,
                    max_size=2_000_000,
                ) as ws:
                    self.connected=True
                    self.last_error=""
                    backoff=2
                    self._emit(
                        "L1.DATA.WS_CONNECTED","INFO","BingX 15m WebSocket connected",
                        details={"url":self.url,"symbols":list(self.symbols),"reconnects":self.reconnects},
                    )
                    for symbol in self.symbols:
                        await ws.send(json.dumps({
                            "id":str(uuid.uuid4()),
                            "reqType":"sub",
                            "dataType":f"{symbol}@kline_15m",
                        }))
                        log.info("WS_SUBSCRIBE symbol=%s channel=kline_15m",symbol)
                    while not self._stop.is_set():
                        raw=await asyncio.wait_for(ws.recv(),timeout=45)
                        text=self._decode(raw)
                        if text=="Ping" or "ping" in text.lower() and len(text)<64:
                            await ws.send("Pong")
                            continue
                        self._handle_text(text)
            except asyncio.TimeoutError:
                self.last_error="websocket receive timeout"
                log.warning("WS_RECONNECT reason=timeout")
                self._emit(
                    "L1.DATA.WS_RECONNECT","WARNING","WebSocket receive timeout; reconnecting",
                    details={"reason":"timeout","reconnects":self.reconnects},
                )
            except Exception as exc:
                self.last_error=f"{type(exc).__name__}: {exc}"
                log.exception("WS_ERROR error=%s",exc)
                self._emit(
                    "L1.DATA.WS_ERROR","ERROR",self.last_error,
                    details={"reconnects":self.reconnects},
                )
            finally:
                if self.connected:
                    self._emit(
                        "L1.DATA.WS_DISCONNECTED","WARNING","BingX 15m WebSocket disconnected",
                        details={"last_error":self.last_error,"reconnects":self.reconnects},
                    )
                self.connected=False
            if self._stop.is_set():
                break
            self.reconnects+=1
            await asyncio.sleep(backoff)
            backoff=min(backoff*2,30)

    @staticmethod
    def _decode(raw)->str:
        if isinstance(raw,str):
            return raw
        try:
            return gzip.decompress(raw).decode("utf-8")
        except Exception:
            return bytes(raw).decode("utf-8")

    def _handle_text(self,text:str)->None:
        try:
            payload=json.loads(text)
        except Exception:
            return
        data=payload.get("data") or {}
        k=data.get("K") if isinstance(data,dict) else None
        if not isinstance(k,dict):
            return
        symbol=str(k.get("s") or data.get("s") or "").upper()
        if symbol not in self.symbols:
            return
        try:
            candle={
                "open_time":int(k["t"]),
                "open":float(k["o"]),
                "high":float(k["h"]),
                "low":float(k["l"]),
                "close":float(k["c"]),
                "volume":float(k["v"]),
                "close_time":int(k["T"]),
            }
        except Exception:
            return
        self.last_message_ms=int(time.time()*1000)
        prev=self._pending.get(symbol)
        if prev and candle["open_time"]>prev["open_time"]:
            self._finalize(symbol,prev)
        if prev is None or candle["open_time"]>=prev["open_time"]:
            self._pending[symbol]=candle
        # If BingX emits the final update after close, accept it without waiting
        # for the next candle. Dedupe prevents a second finalize.
        if int(time.time()*1000)>=candle["close_time"]+1500:
            self._finalize(symbol,candle)

    def _finalize(self,symbol:str,candle:dict)->None:
        ot=int(candle["open_time"])
        if ot<=int(self._last_finalized.get(symbol,0)):
            return
        if int(candle["close_time"])!=ot+STEP_15M_MS-1:
            log.error("WS_CANDLE_REJECT symbol=%s reason=close_time open=%s close=%s",symbol,ot,candle["close_time"])
            self._emit(
                "L1.DATA.CANDLE_REJECT","ERROR","invalid 15m close_time from WebSocket",
                symbol=symbol,
                details={"open_time":ot,"close_time":int(candle["close_time"])},
            )
            return
        self._last_finalized[symbol]=ot
        frame=pd.DataFrame([candle])
        log.info("WS_CANDLE_CLOSED symbol=%s open_time=%s close_time=%s",symbol,ot,candle["close_time"])
        try:
            self.on_closed(symbol,frame)
        except Exception as exc:
            log.exception("WS_CANDLE_CALLBACK_ERROR symbol=%s open_time=%s",symbol,ot)
            self._emit(
                "L1.DATA.CANDLE_CALLBACK_FAIL","ERROR",str(exc),
                symbol=symbol,
                details={"open_time":ot},
            )
