from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from decimal import Decimal, InvalidOperation

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
    OPEN_ORDERS_PATH="/openApi/swap/v2/trade/openOrders"
    BALANCE_PATH="/openApi/swap/v3/user/balance"
    POSITIONS_PATH="/openApi/swap/v2/user/positions"
    CONTRACTS_PATH="/openApi/swap/v2/quote/contracts"

    def __init__(self,settings:Settings,request_guard=None):
        self.settings=settings
        self.http=httpx.Client(timeout=20.0)
        self.guard=request_guard or BingXRequestGuard()

    @staticmethod
    def _fmt_qty(qty:float)->str:
        return f"{float(qty):.4f}".rstrip("0").rstrip(".")

    @staticmethod
    def _fmt_exact_qty(qty,precision:int)->str:
        """No silent truncation: accept only exact exchange quantity steps."""
        if isinstance(precision,bool) or not isinstance(precision,int) or precision<0 or precision>12:
            raise ValueError("invalid BingX quantityPrecision")
        try:
            quantity=Decimal(str(qty))
            step=Decimal(1).scaleb(-precision)
            if not quantity.is_finite() or quantity<=0 or quantity!=quantity.quantize(step):
                raise ValueError("quantity is nonpositive or does not match BingX precision")
        except (InvalidOperation,TypeError) as exc:
            raise ValueError("invalid BingX quantity") from exc
        return format(quantity,"f")

    @staticmethod
    def _add_position_id(params:dict,position_id:str|None)->None:
        """Only send a validated, explicit BingX position identifier.

        Never infer a positionId from positionSide or a matching quantity.
        BingX uses int64 IDs; reject nonnumeric/zero IDs before signing.
        """
        if position_id is None or str(position_id)=="":
            return
        value=str(position_id)
        if not re.fullmatch(r"[1-9][0-9]*",value):
            raise ValueError("invalid BingX positionId")
        params["positionId"]=value

    def get_contract_rules(self,symbol:str)->dict:
        """Read live contract precision and minimum order size from BingX."""
        with self.guard.request_slot():
            response=self.http.get(
                f"{self.settings.bingx_base_url}{self.CONTRACTS_PATH}",
                params={"symbol":symbol},
            )
            response.raise_for_status()
            payload=response.json()
        if not isinstance(payload,dict) or payload.get("code") not in (0,"0"):
            raise RuntimeError("BingX contract rules request failed")
        data=payload.get("data")
        if isinstance(data,dict):
            data=[data]
        if not isinstance(data,list):
            raise RuntimeError("BingX contract rules returned no contract list")
        matching=[row for row in data if isinstance(row,dict) and row.get("symbol")==symbol]
        if len(matching)!=1:
            raise RuntimeError("BingX contract rules missing or ambiguous symbol")
        rule=matching[0]
        if "quantityPrecision" not in rule or "tradeMinQuantity" not in rule:
            raise RuntimeError("BingX contract rules missing quantity precision/minimum")
        return rule

    def get_positions(self,symbol:str)->list[dict]:
        """Exchange positionAmt is authoritative remaining quantity (not local subtraction)."""
        payload=self._signed_request("GET",self.POSITIONS_PATH,{"symbol":symbol})
        data=payload.get("data")
        if isinstance(data,dict):
            data=data.get("positions")
        if not isinstance(data,list):
            raise RuntimeError("BingX position response missing positions list")
        return [row for row in data if isinstance(row,dict)]

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
                elif method.upper()=="DELETE":
                    response=self.http.delete(url,headers=headers)
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
                expected_missing_client_order=(
                    str(code)=="109421"
                    and method.upper()=="GET"
                    and path==self.ORDER_PATH
                    and bool(body.get("clientOrderId"))
                    and not body.get("orderId")
                )
                if expected_missing_client_order:
                    log.info(
                        "BINGX_ORDER_NOT_FOUND_EXPECTED path=%s symbol=%s client_order_id=%s",
                        path,body.get("symbol"),body.get("clientOrderId"),
                    )
                    raise BingXApiError(
                        code,msg or "order not exist",path,body
                    )
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
        self._add_position_id(params,position_id)
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

    def get_open_orders(self,symbol:str)->list[dict]:
        payload=self._signed_request(
            "GET",self.OPEN_ORDERS_PATH,{"symbol":symbol}
        )
        data=payload.get("data") or []
        if isinstance(data,list):
            return [x for x in data if isinstance(x,dict)]
        if isinstance(data,dict):
            rows=data.get("orders") or []
            return [x for x in rows if isinstance(x,dict)]
        return []

    def find_matching_protection(
        self,
        symbol:str,
        entry_side:str,
        qty:float,
        stop_price:float,
        order_type:str,
        position_id:str|None=None,
    )->dict|None:
        opposite,position_side=self._protection_side(entry_side)
        expected_qty=float(self._fmt_qty(qty))
        expected_stop=float(self._fmt_price(stop_price))
        matches=[]
        for order in self.get_open_orders(symbol):
            try:
                if str(order.get("type") or "").upper()!=order_type.upper():
                    continue
                if str(order.get("side") or "").upper()!=opposite:
                    continue
                if str(order.get("positionSide") or "").upper()!=position_side:
                    continue
                if position_id and str(order.get("positionId") or "")!=str(position_id):
                    continue
                if str(order.get("status") or "NEW").upper() not in {"NEW","PARTIALLY_FILLED"}:
                    continue
                oq=float(order.get("origQty") or order.get("quantity") or 0)
                sp=float(order.get("stopPrice") or 0)
                if abs(oq-expected_qty)>1e-12 or abs(sp-expected_stop)>1e-9:
                    continue
                matches.append(order)
            except Exception:
                continue
        if not matches:
            return None
        return {"code":0,"data":{"order":matches[0]}}

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
        position_id:str|None=None,
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
        self._add_position_id(params,position_id)
        return self._signed_request("POST",self.ORDER_PATH,params)

    def place_take_profit(
        self,
        symbol:str,
        entry_side:str,
        qty:float,
        tp:float,
        quantity_precision:int|None=None,
        position_id:str|None=None,
    )->dict:
        opposite,position_side=self._protection_side(entry_side)
        params={
            "symbol":symbol,
            "side":opposite,
            "positionSide":position_side,
            "type":"TAKE_PROFIT_MARKET",
            "stopPrice":self._fmt_price(tp),
            "quantity":self._fmt_qty(qty) if quantity_precision is None else self._fmt_exact_qty(qty,quantity_precision),
        }
        self._add_position_id(params,position_id)
        return self._signed_request("POST",self.ORDER_PATH,params)

    def place_trailing_stop(
        self,
        symbol:str,
        entry_side:str,
        qty:float,
        activation_price:float,
        callback_rate:float,
        quantity_precision:int|None=None,
        position_id:str|None=None,
    )->dict:
        """Exchange-native trailing close of the requested partial quantity."""
        opposite,position_side=self._protection_side(entry_side)
        params={
            "symbol":symbol,
            "side":opposite,
            "positionSide":position_side,
            "type":"TRAILING_STOP_MARKET",
            "quantity":self._fmt_qty(qty) if quantity_precision is None else self._fmt_exact_qty(qty,quantity_precision),
            "activationPrice":self._fmt_price(activation_price),
            "priceRate":str(callback_rate),
        }
        self._add_position_id(params,position_id)
        return self._signed_request("POST",self.ORDER_PATH,params)

    def find_matching_trailing(
        self,
        symbol:str,
        entry_side:str,
        qty:float,
        activation_price:float,
        callback_rate:float,
        position_id:str|None=None,
    )->dict|None:
        """Recover a trailing order after an ambiguous POST, without duplicating it."""
        opposite,position_side=self._protection_side(entry_side)
        matches=[]
        for order in self.get_open_orders(symbol):
            try:
                if str(order.get("type") or "").upper()!="TRAILING_STOP_MARKET":
                    continue
                if str(order.get("side") or "").upper()!=opposite:
                    continue
                if str(order.get("positionSide") or "").upper()!=position_side:
                    continue
                if position_id and str(order.get("positionId") or "")!=str(position_id):
                    continue
                if str(order.get("status") or "NEW").upper() not in {"NEW","PARTIALLY_FILLED"}:
                    continue
                oq=float(order.get("origQty") or order.get("quantity") or 0)
                ap=float(order.get("activationPrice") or 0)
                rate=float(order.get("priceRate") or 0)
                if abs(oq-float(self._fmt_qty(qty)))>1e-12:
                    continue
                if abs(ap-float(self._fmt_price(activation_price)))>1e-9:
                    continue
                if abs(rate-float(callback_rate))>1e-9:
                    continue
                matches.append(order)
            except (ValueError,TypeError):
                continue
        if not matches:
            return None
        if len(matches)>1:
            raise RuntimeError("ambiguous duplicate trailing orders found; manual reconciliation required")
        return {"code":0,"data":{"order":matches[0]}}

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

    def cancel_replace_stop_loss(
        self,symbol:str,entry_side:str,old_order_id:str,
        qty:str,sl:float,quantity_precision:int,
        new_client_order_id:str,position_id:str|None=None,
    )->dict:
        """Atomic STOP_ON_FAILURE SL replacement; never cancel-then-place."""
        opposite,position_side=self._protection_side(entry_side)
        params={
            "symbol":symbol,
            "cancelOrderId":str(old_order_id),
            "cancelReplaceMode":"STOP_ON_FAILURE",
            "side":opposite,
            "positionSide":position_side,
            "type":"STOP_MARKET",
            "quantity":self._fmt_exact_qty(qty,quantity_precision),
            "stopPrice":self._fmt_price(sl),
            "clientOrderId":str(new_client_order_id),
        }
        self._add_position_id(params,position_id)
        return self._signed_request(
            "POST","/openApi/swap/v1/trade/cancelReplace",params,
        )

    def close_market(
        self,
        symbol:str,
        entry_side:str,
        qty:float,
        leverage:int,
        client_order_id:str|None=None,
        position_id:str|None=None,
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
