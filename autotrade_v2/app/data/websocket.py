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
    market_stale_ms: int = 90_000
    first_kline_deadline_ms: int = 60_000

    def __post_init__(self)->None:
        self._stop=threading.Event()
        self._thread:threading.Thread|None=None
        self._pending:dict[str,dict]={}
        self._last_finalized:dict[str,int]={}
        self.connected=False
        self.reconnects=0
        self.last_message_ms=0
        self.last_control_ms=0
        self.last_kline_ms=0
        self.last_error=""
        self._connection_started_ms=0
        self._connection_kline_seen=False
        self._shape_logged:set[str]=set()

    def start(self)->None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread=threading.Thread(target=self._run,name="bingx-kline-ws",daemon=True)
        self._thread.start()

    def stop(self)->None:
        self._stop.set()

    def status(self)->dict:
        now_ms=int(time.time()*1000)
        return {
            "connected":self.connected,
            "data_healthy":self.connected and not self._market_data_stale(now_ms),
            "reconnects":self.reconnects,
            "last_message_ms":self.last_message_ms,
            "last_control_ms":self.last_control_ms,
            "last_kline_ms":self.last_kline_ms,
            "last_finalized":dict(self._last_finalized),
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

    def _market_data_stale(self,now_ms:int|None=None)->bool:
        if not self.connected:
            return True
        now_ms=int(now_ms or time.time()*1000)
        if not self._connection_kline_seen:
            return (
                self._connection_started_ms > 0
                and now_ms-self._connection_started_ms > self.first_kline_deadline_ms
            )
        return self.last_kline_ms <= 0 or now_ms-self.last_kline_ms > self.market_stale_ms

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
                    self._connection_started_ms=int(time.time()*1000)
                    self._connection_kline_seen=False
                    backoff=2
                    self._emit(
                        "L1.DATA.WS_CONNECTED","INFO","BingX 15m WebSocket connected",
                        details={"url":self.url,"symbols":list(self.symbols),"reconnects":self.reconnects},
                    )
                    for symbol in self.symbols:
                        request_id=str(uuid.uuid4())
                        await ws.send(json.dumps({
                            "id":request_id,
                            "reqType":"sub",
                            "dataType":f"{symbol}@kline_15m",
                        }))
                        log.info(
                            "WS_SUBSCRIBE symbol=%s channel=kline_15m request_id=%s",
                            symbol,request_id,
                        )
                    while not self._stop.is_set():
                        raw=await asyncio.wait_for(ws.recv(),timeout=45)
                        now_ms=int(time.time()*1000)
                        text=self._decode(raw)
                        if text.strip().lower()=="ping":
                            self.last_control_ms=now_ms
                            await ws.send("Pong")
                            if self._market_data_stale(now_ms):
                                raise RuntimeError("kline market data stale while WebSocket heartbeat is alive")
                            continue
                        parsed_kline=self._handle_text(text)
                        if not parsed_kline:
                            self._handle_control_text(text,now_ms)
                        if self._market_data_stale(now_ms):
                            raise RuntimeError("kline market data stale; reconnect required")
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
                if self.connected and not self._stop.is_set():
                    self._emit(
                        "L1.DATA.WS_DISCONNECTED","INFO","BingX 15m WebSocket disconnected before reconnect",
                        details={"last_error":self.last_error,"reconnects":self.reconnects},
                    )
                self.connected=False
                self._connection_started_ms=0
                self._connection_kline_seen=False
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

    def _handle_control_text(self,text:str,now_ms:int)->None:
        self.last_control_ms=int(now_ms)
        try:
            payload=json.loads(text)
        except Exception:
            log.debug("WS_CONTROL non_json=%r",text[:160])
            return
        if not isinstance(payload,dict):
            return
        request_id=payload.get("id")
        code=payload.get("code")
        msg=payload.get("msg")
        data_type=payload.get("dataType")
        if request_id is not None or code is not None or msg is not None:
            log.info(
                "WS_CONTROL id=%s code=%s msg=%s dataType=%s",
                request_id,code,msg,data_type,
            )
            if code not in (None,0,"0"):
                raise RuntimeError(f"BingX WebSocket subscription error code={code} msg={msg}")

    @classmethod
    def _find_kline_payload(cls,obj):
        required={"t","T","o","h","l","c","v"}
        if isinstance(obj,dict):
            k=obj.get("K")
            if isinstance(k,dict) and required.issubset(k.keys()):
                return k
            if required.issubset(obj.keys()):
                return obj
            for value in obj.values():
                found=cls._find_kline_payload(value)
                if found is not None:
                    return found
        elif isinstance(obj,list):
            for value in obj:
                found=cls._find_kline_payload(value)
                if found is not None:
                    return found
        elif isinstance(obj,str):
            raw=obj.strip()
            if raw.startswith("{") or raw.startswith("["):
                try:
                    return cls._find_kline_payload(json.loads(raw))
                except Exception:
                    return None
        return None

    @classmethod
    def _payload_shape(cls,obj,depth:int=0):
        if depth>=5:
            return type(obj).__name__
        if isinstance(obj,dict):
            return {
                str(k):cls._payload_shape(v,depth+1)
                for k,v in list(obj.items())[:30]
            }
        if isinstance(obj,list):
            return {
                "_type":"list",
                "_len":len(obj),
                "_item":cls._payload_shape(obj[0],depth+1) if obj else None,
            }
        if isinstance(obj,str):
            raw=obj.strip()
            if raw.startswith("{") or raw.startswith("["):
                try:
                    return {
                        "_type":"json_string",
                        "_parsed":cls._payload_shape(json.loads(raw),depth+1),
                    }
                except Exception:
                    pass
            return "str"
        return type(obj).__name__

    def _handle_text(self,text:str)->bool:
        try:
            payload=json.loads(text)
        except Exception:
            return False
        if not isinstance(payload,dict):
            return False
        data_type=str(payload.get("dataType") or "")
        if "@kline_15m" not in data_type:
            return False
        k=self._find_kline_payload(payload)
        if not isinstance(k,dict):
            if data_type not in self._shape_logged:
                self._shape_logged.add(data_type)
                log.warning(
                    "WS_KLINE_SHAPE_UNRECOGNIZED dataType=%s shape=%s",
                    data_type,
                    json.dumps(self._payload_shape(payload),separators=(",",":")),
                )
            return False
        symbol=str(k.get("s") or data_type.split("@",1)[0] or "").upper()
        if symbol not in self.symbols:
            return False
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
            return False
        now_ms=int(time.time()*1000)
        self.last_message_ms=now_ms
        self.last_kline_ms=now_ms
        self._connection_kline_seen=True
        prev=self._pending.get(symbol)
        if prev and candle["open_time"]>prev["open_time"]:
            self._finalize(symbol,prev)
        if prev is None or candle["open_time"]>=prev["open_time"]:
            self._pending[symbol]=candle
        if now_ms>=candle["close_time"]+1500:
            self._finalize(symbol,candle)
        return True

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
