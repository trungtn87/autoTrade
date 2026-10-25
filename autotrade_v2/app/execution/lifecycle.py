"""Restart-safe exchange-driven cleanup and SL sizing for a single tracked trade.

Only mutate orders with IDs already stored for this trade. Every stateful
order mutation journals its deterministic intent *before* the API request.
Do not use expected TP volume to compute remaining position volume.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from decimal import Decimal

from .quantity import PositionQuantityMismatch, _amount, remaining_position_qty

ACTIVE={"NEW","PARTIALLY_FILLED"}
TERMINAL={"FILLED","CANCELED","EXPIRED","REJECTED"}


class LifecycleRisk(RuntimeError):
    """Unsafe/ambiguous exchange state; retry observation, emit a critical alert."""


class ProtectionLifecycle:
    def __init__(self,client):
        self.client=client

    @staticmethod
    def _record(record:dict)->dict:
        data=record.get("data") if isinstance(record,dict) else None
        return data.get("order") if isinstance(data,dict) and isinstance(data.get("order"),dict) else (data if isinstance(data,dict) else {})

    @staticmethod
    def _sid(state:dict)->tuple[str,str,str]:
        intent=state["intent"]
        side="LONG" if intent["side"].upper()=="BUY" else "SHORT"
        opposite="SELL" if side=="LONG" else "BUY"
        return str(intent["symbol"]),side,opposite

    def _position(self,state:dict)->Decimal:
        """Account-wide exchange volume; not attributable to a single combo."""
        symbol,side,_=self._sid(state)
        rows=self.client.get_positions(symbol)
        if not isinstance(rows,list):
            raise LifecycleRisk("BingX position list missing")
        total=Decimal(0)
        for row in rows:
            if row.get("symbol")==symbol and str(row.get("positionSide") or "").upper()==side:
                total+=_amount(row.get("positionAmt"),"BingX positionAmt")
        return total

    def _order(self,state:dict,order_id:str|None=None,client_order_id:str|None=None)->dict:
        symbol,_,_=self._sid(state)
        payload=self.client.get_order_detail(
            symbol,order_id=order_id,client_order_id=client_order_id
        )
        order=self._record(payload)
        if not order or not (order.get("orderId") or order.get("orderID")):
            raise LifecycleRisk("BingX order query returned no matching orderId")
        return order

    @staticmethod
    def _order_id(order:dict)->str:
        return str(order.get("orderId") or order.get("orderID") or "")

    def _validate_owned(self,state:dict,order:dict,kind:str,qty:str|None=None)->None:
        symbol,side,opposite=self._sid(state)
        if (
            str(order.get("symbol") or "")!=symbol
            or str(order.get("side") or "").upper()!=opposite
            or str(order.get("positionSide") or "").upper()!=side
        ):
            raise LifecycleRisk("tracked order metadata mismatch; do not cancel another strategy order")
        expected={"sl":"STOP_MARKET","tp":"TAKE_PROFIT_MARKET","trailing":"TRAILING_STOP_MARKET"}[kind]
        if str(order.get("type") or "").upper()!=expected:
            raise LifecycleRisk("tracked protection order type mismatch")
        if qty is not None:
            if _amount(order.get("origQty"),"SL order origQty")!=_amount(qty,"expected SL qty"):
                raise LifecycleRisk("SL replacement order quantity mismatch")
            if _amount(order.get("stopPrice"),"SL stopPrice")!=_amount(state["intent"]["sl"],"strategy SL"):
                raise LifecycleRisk("SL replacement changed stop price")

    def _owned_remaining(self,state:dict)->tuple[Decimal,dict]:
        """Exact residual from this trade's exchange-reported exit fills."""
        initial=_amount(
            state.get("executed_qty") or state.get("actual_position_qty"),
            "entry executedQty",
        )
        if initial<=0:
            raise LifecycleRisk("missing positive exchange entry executedQty")
        orders={}
        closed_qty=Decimal(0)
        for kind in ("tp","trailing","sl"):
            item=(state.get("protection") or {}).get(kind) or {}
            oid=str(item.get("order_id") or "")
            if not oid:
                continue
            order=self._order(state,order_id=oid)
            self._validate_owned(state,order,kind)
            status=str(order.get("status") or "").upper()
            if status not in ACTIVE|TERMINAL:
                raise LifecycleRisk(f"unknown BingX {kind} status {status!r}")
            original=_amount(order.get("origQty"),f"{kind} origQty")
            executed=_amount(order.get("executedQty"),f"{kind} executedQty")
            if executed>original:
                raise LifecycleRisk(f"{kind} executedQty exceeds origQty")
            closed_qty+=executed
            orders[kind]=order
        if closed_qty>initial:
            raise LifecycleRisk("sum of owned exit fills exceeds own entry fill")
        return initial-closed_qty,orders

    def _persist(self,state:dict,write)->None:
        write(deepcopy(state))

    def _cancel_exits(self,state:dict,write)->str:
        """Cancel each known protection by its own ID, verifying each outcome."""
        symbol,_,_=self._sid(state)
        lifecycle=state["lifecycle"]
        if lifecycle.get("pending_sl_replace"):
            raise LifecycleRisk("SL replace pending; resolve exchange outcome before cleanup")
        if lifecycle.get("closure_evidence") not in {"owned_exit_fills","exchange_position_flat"}:
            raise LifecycleRisk("no exchange-confirmed closure evidence for this combo")
        # Never call cancel-all: manual/other-combo orders can coexist.
        for kind in ("tp","trailing","sl"):
            saved=(state.get("protection") or {}).get(kind) or {}
            order_id=str(saved.get("order_id") or "")
            if not order_id:
                continue
            if kind in lifecycle.get("cleaned_orders",[]):
                continue
            order=self._order(state,order_id=order_id)
            self._validate_owned(state,order,kind)
            status=str(order.get("status") or "").upper()
            if status in ACTIVE:
                try:
                    self.client.cancel_order(symbol,order_id=order_id)
                except Exception:
                    # DELETE response may be lost: inspect exchange status below.
                    pass
                order=self._order(state,order_id=order_id)
                self._validate_owned(state,order,kind)
                status=str(order.get("status") or "").upper()
            if status not in TERMINAL:
                raise LifecycleRisk(f"{kind} order {order_id} not confirmed cancelled/filled: {status}")
            lifecycle.setdefault("cleaned_orders",[]).append(kind)
            self._persist(state,write)
        lifecycle["status"]="closed"
        lifecycle["remaining_qty"]="0"
        lifecycle["pending_sl_replace"]=None
        lifecycle.pop("last_error",None)
        self._persist(state,write)
        return "closed"

    def _reconcile_replacement(self,state:dict,write)->bool:
        """Resolve an ambiguous atomic replace using deterministic clientOrderId."""
        pending=state["lifecycle"].get("pending_sl_replace")
        if not pending:
            return False
        original=self._order(state,order_id=pending["old_order_id"])
        self._validate_owned(state,original,"sl")
        old_status=str(original.get("status") or "").upper()
        new=None
        try:
            new=self._order(state,client_order_id=pending["client_order_id"])
        except Exception:
            # Not found may be normal after unsuccessful STOP_ON_FAILURE.
            new=None
        if new:
            self._validate_owned(state,new,"sl",pending["quantity"])
            if str(new.get("status") or "").upper() not in ACTIVE:
                raise LifecycleRisk("replacement SL not active")
            if old_status not in TERMINAL:
                raise LifecycleRisk("both old and replacement SL appear open")
            state["protection"]["sl"]={
                "order_id":self._order_id(new),
                "status":str(new["status"]).upper(),
                "qty":pending["quantity"],
                "payload":None,
            }
            state["lifecycle"]["sl_qty"]=pending["quantity"]
            state["lifecycle"]["sl_revision"]=pending["revision"]
            state["lifecycle"]["pending_sl_replace"]=None
            self._persist(state,write)
            return True
        if old_status not in ACTIVE:
            raise LifecycleRisk("old SL no longer active but replacement cannot be found")
        return False  # Old SL still active: safe to retry STOP_ON_FAILURE.

    def reconcile(self,state:dict,write,conflicting_position:bool=False)->str:
        """Return 'unchanged', 'sl_resized' or 'closed'; raises on unsafe states."""
        state=deepcopy(state)
        if not state.get("ok") or not state.get("terminal"):
            return "unchanged"
        if not isinstance(state.get("protection"),dict) or not state["protection"].get("sl"):
            return "unchanged"
        lifecycle=state.get("lifecycle")
        if not isinstance(lifecycle,dict) or lifecycle.get("status") not in {"open","cleanup"}:
            return "unchanged"
        if conflicting_position:
            raise LifecycleRisk("more than one active bot trade has the same symbol and positionSide")

        initial=_amount(state.get("actual_position_qty") or state.get("executed_qty"),"initial position")
        if initial<=0:
            raise LifecycleRisk("missing initial position size")
        current=self._position(state)
        if current>initial:
            raise LifecycleRisk("BingX positionAmt exceeds tracked entry quantity; shared/changed position")
        if current==0:
            # Confirm independently before canceling anything.
            if self._position(state)!=0:
                raise LifecycleRisk("position close was transient")
            if lifecycle.get("status")!="cleanup":
                lifecycle["status"]="cleanup"
                self._persist(state,write)
            return self._cancel_exits(state,write)

        if lifecycle.get("status")=="cleanup":
            lifecycle["status"]="open"
            lifecycle["cleaned_orders"]=[]
            self._persist(state,write)
        sl_saved=state["protection"]["sl"]
        old_id=str(sl_saved.get("order_id") or "")
        if not old_id:
            raise LifecycleRisk("missing tracked SL orderId")

        if lifecycle.get("pending_sl_replace"):
            self._reconcile_replacement(state,write)
            if not state["lifecycle"].get("pending_sl_replace"):
                return "sl_resized"
        sl_order=self._order(state,order_id=old_id)
        self._validate_owned(state,sl_order,"sl")
        if str(sl_order.get("status") or "").upper()!="NEW":
            raise LifecycleRisk("SL not NEW while position remains; manual/exchange reconciliation required")

        sl_qty=_amount(sl_order.get("origQty"),"SL origQty")
        if sl_qty==current:
            lifecycle["remaining_qty"]=format(current,"f")
            if lifecycle.get("last_error"):
                lifecycle.pop("last_error",None)
                self._persist(state,write)
            return "unchanged"
        if sl_qty<current:
            raise LifecycleRisk("SL amount smaller than live BingX position; do not fabricate quantity")

        symbol,_,_=self._sid(state)
        rules=self.client.get_contract_rules(symbol)
        precision=rules.get("quantityPrecision")
        minimum=_amount(rules.get("tradeMinQuantity"),"minimum order size")
        if (
            type(precision) is not int or not 0<=precision<=12
            or current<minimum
            or current!=current.quantize(Decimal(1).scaleb(-precision))
        ):
            raise LifecycleRisk("remaining position cannot be represented as a valid replacement SL order")

        # Verify this trade's other exits; do not resize around overlapping
        # conditional quantities or a second strategy sharing this position.
        other_open=Decimal(0)
        for kind in ("tp","trailing"):
            item=state["protection"].get(kind) or {}
            oid=str(item.get("order_id") or "")
            if not oid:
                continue
            order=self._order(state,order_id=oid)
            self._validate_owned(state,order,kind)
            status=str(order.get("status") or "").upper()
            if status in ACTIVE:
                ordered=_amount(order.get("origQty"),kind+" origQty")
                executed=_amount(order.get("executedQty"),kind+" executedQty")
                if executed>ordered:
                    raise LifecycleRisk("executedQty exceeds origQty in open exit")
                other_open+=ordered-executed
            elif status not in TERMINAL:
                raise LifecycleRisk("unrecognized exit order state")
        if other_open>current:
            raise LifecycleRisk("remaining conditional exit sizes exceed actual position")

        if self._position(state)!=current:
            raise LifecycleRisk("position changed while preparing SL replacement; retry next poll")

        revision=int(lifecycle.get("sl_revision",0))+1
        pending=lifecycle.get("pending_sl_replace")
        if pending is None:
            token=hashlib.sha256(
                f'{state["intent"]["event_id"]}|sl|{revision}'.encode("utf-8")
            ).hexdigest()[:26]
            pending={
                "old_order_id":old_id,
                "client_order_id":"atv2sl-"+token,
                "quantity":format(current,"f"),
                "revision":revision,
            }
            lifecycle["pending_sl_replace"]=pending
            self._persist(state,write)
        elif pending["old_order_id"]!=old_id or _amount(pending["quantity"],"pending amount")!=current:
            raise LifecycleRisk("pending SL replacement no longer matches live position")

        try:
            result=self.client.cancel_replace_stop_loss(
                symbol,state["intent"]["side"],old_id,pending["quantity"],
                state["intent"]["sl"],precision,pending["client_order_id"],
                position_id=state.get("position_id"),
            )
            data=result.get("data") or {}
            if data.get("cancelResult")!="SUCCESS" or data.get("newOrderResult")!="SUCCESS":
                raise LifecycleRisk("BingX did not confirm both parts of atomic cancelReplace")
        except Exception:
            # Result might be lost despite exchange success; inspect by CID.
            if self._reconcile_replacement(state,write):
                return "sl_resized"
            raise LifecycleRisk("SL cancelReplace unconfirmed; original SL remains active")
        if not self._reconcile_replacement(state,write):
            raise LifecycleRisk("SL replacement was acknowledged but is not queryable yet")
        return "sl_resized"
