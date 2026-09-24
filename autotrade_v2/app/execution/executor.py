from __future__ import annotations

import hashlib
import hmac
import logging
import time

import httpx

from ..config import Settings
from ..control.bingx_guard import BingXRequestGuard
from ..control.errors import BingXApiError

log=logging.getLogger(__name__)


class LegacyStyleBingXClient:
    """Direct BingX trade transport modeled on the user's original autoTrade.

    One responsibility only: sign requests, send them, validate BingX response.
    No strategy, no market-data fetch, no database access.
    """

    ORDER_PATH="/openApi/swap/v2/trade/order"
    BALANCE_PATH="/openApi/swap/v3/user/balance"

    def __init__(self,settings:Settings,request_guard=None):
        self.settings=settings
        self.http=httpx.Client(timeout=20.0)
        self.guard=request_guard or BingXRequestGuard()

    @staticmethod
    def _fmt_qty(qty:float)->str:
        return f"{float(qty):.4f}".rstrip("0").rstrip(".")

    @staticmethod
    def _fmt_price(price:float)->str:
        return f"{float(price):.2f}".rstrip("0").rstrip(".")

    def _signed_request(self,method:str,path:str,params:dict)->dict:
        with self.guard.request_slot():
            body=dict(params)
            body["timestamp"]=str(int(time.time()*1000))
            query="&".join(f"{k}={body[k]}" for k in sorted(body))
            signature=hmac.new(
                self.settings.bingx_api_secret.encode("utf-8"),
                query.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            url=f"{self.settings.bingx_base_url}{path}?{query}&signature={signature}"
            headers={"X-BX-APIKEY":self.settings.bingx_api_key}

            log.info(
                "BINGX_TRADE_REQ method=%s path=%s symbol=%s type=%s",
                method.upper(),path,body.get("symbol"),body.get("type"),
            )
            try:
                if method.upper()=="POST":
                    response=self.http.post(url,headers=headers)
                elif method.upper()=="GET":
                    response=self.http.get(url,headers=headers)
                else:
                    raise ValueError(f"unsupported method: {method}")
                payload=response.json()
            except Exception as exc:
                raise RuntimeError(
                    f"BingX trade transport failed: {type(exc).__name__}: {exc}"
                ) from exc

            code=payload.get("code") if isinstance(payload,dict) else None
            msg=str(payload.get("msg","")) if isinstance(payload,dict) else ""
            log.info(
                "BINGX_TRADE_RESP method=%s path=%s status=%s code=%s",
                method.upper(),path,response.status_code,code,
            )
            if response.status_code==429:
                raise self.guard.api_error(
                    "legacy_executor","HTTP_429","BingX HTTP rate limit",path,body
                )
            if response.status_code<200 or response.status_code>=300:
                raise RuntimeError(
                    f"BingX HTTP {response.status_code}: {msg or response.text[:200]}"
                )
            if code not in (0,"0"):
                raise self.guard.api_error(
                    "legacy_executor",code,msg or "BingX rejected request",path,body
                )
            return payload

    @staticmethod
    def extract_order(payload:dict)->dict:
        data=payload.get("data") or {}
        if isinstance(data,dict) and isinstance(data.get("order"),dict):
            return data["order"]
        return data if isinstance(data,dict) else {}

    def query_balance(self)->dict:
        """Authenticated read-only preflight. Never creates/cancels/modifies orders."""
        return self._signed_request("GET",self.BALANCE_PATH,{})

    def place_market_entry(
        self,
        symbol:str,
        side:str,
        qty:float,
        leverage:int,
        client_order_id:str|None=None,
    )->dict:
        params={
            "symbol":symbol,
            "side":side.upper(),
            "quantity":self._fmt_qty(qty),
            "leverage":str(int(leverage)),
            "type":"MARKET",
            "positionSide":"LONG" if side.upper()=="BUY" else "SHORT",
        }
        if client_order_id:
            params["clientOrderId"]=str(client_order_id)
        return self._signed_request("POST",self.ORDER_PATH,params)

    def get_order_detail(
        self,
        symbol:str,
        order_id:str|None=None,
        client_order_id:str|None=None,
    )->dict:
        params={"symbol":symbol}
        if order_id:
            params["orderId"]=str(order_id)
        elif client_order_id:
            params["clientOrderId"]=str(client_order_id)
        else:
            raise ValueError("order_id or client_order_id is required")
        return self._signed_request("GET",self.ORDER_PATH,params)

    def find_order_by_client_id(self,symbol:str,client_order_id:str)->dict|None:
        try:
            return self.get_order_detail(
                symbol,client_order_id=client_order_id
            )
        except BingXApiError as exc:
            if exc.code=="109421":
                return None
            raise

    def cancel_order(
        self,
        symbol:str,
        order_id:str|None=None,
        client_order_id:str|None=None,
    )->dict:
        params={"symbol":symbol}
        if order_id:
            params["orderId"]=str(order_id)
        elif client_order_id:
            params["clientOrderId"]=str(client_order_id)
        else:
            raise ValueError("order_id or client_order_id is required")
        return self._signed_request("DELETE",self.ORDER_PATH,params)

    @staticmethod
    def _protection_side(entry_side:str)->tuple[str,str]:
        side=entry_side.upper()
        return (
            "SELL" if side=="BUY" else "BUY",
            "LONG" if side=="BUY" else "SHORT",
        )

    def place_stop_loss(
        self,
        symbol:str,
        entry_side:str,
        qty:float,
        sl:float,
    )->dict:
        opposite,position_side=self._protection_side(entry_side)
        params={
            "symbol":symbol,
            "side":opposite,
            "positionSide":position_side,
            "type":"STOP_MARKET",
            "stopPrice":self._fmt_price(sl),
            "quantity":self._fmt_qty(qty),
        }
        return self._signed_request("POST",self.ORDER_PATH,params)

    def place_take_profit(
        self,
        symbol:str,
        entry_side:str,
        qty:float,
        tp:float,
    )->dict:
        opposite,position_side=self._protection_side(entry_side)
        params={
            "symbol":symbol,
            "side":opposite,
            "positionSide":position_side,
            "type":"TAKE_PROFIT_MARKET",
            "stopPrice":self._fmt_price(tp),
            "quantity":self._fmt_qty(qty),
        }
        return self._signed_request("POST",self.ORDER_PATH,params)

    def place_protection(
        self,
        symbol:str,
        entry_side:str,
        qty:float,
        tp:float,
        sl:float,
    )->dict:
        """Compatibility helper. Safety order is intentionally SL before TP."""
        sl_result=self.place_stop_loss(symbol,entry_side,qty,sl)
        tp_result=self.place_take_profit(symbol,entry_side,qty,tp)
        return {"tp":tp_result,"sl":sl_result}

    def close_market(
        self,
        symbol:str,
        entry_side:str,
        qty:float,
        leverage:int,
        client_order_id:str|None=None,
    )->dict:
        close_side="SELL" if entry_side.upper()=="BUY" else "BUY"
        params={
            "symbol":symbol,
            "side":close_side,
            "quantity":self._fmt_qty(qty),
            "leverage":str(int(leverage)),
            "type":"MARKET",
            "positionSide":"LONG" if entry_side.upper()=="BUY" else "SHORT",
        }
        if client_order_id:
            params["clientOrderId"]=str(client_order_id)
        return self._signed_request("POST",self.ORDER_PATH,params)
