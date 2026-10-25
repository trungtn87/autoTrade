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

    def reconcile(self,state:dict,write,peer_states:list[dict]|None=None)->str:
        """Reconcile only this combo's owned exit orders, never global cancel-all.

        Actual account positionAmt is a cross-check. This combo's remainder
        comes exclusively from its own BingX entry/exit executedQty values.
        """
        state=deepcopy(state)
        if not state.get("ok") or not state.get("terminal"):
            return "unchanged"
        if not isinstance(state.get("protection"),dict) or not state["protection"].get("sl"):
            return "unchanged"
        lifecycle=state.get("lifecycle")
        if not isinstance(lifecycle,dict) or lifecycle.get("status") not in {"open","cleanup"}:
            return "unchanged"

        # A prior iteration witnessed a fully closed trade and durably recorded
        # the proof. Continue per-order cleanup after Render restarts, even if
        # another combo subsequently opened the same BingX side.
        if lifecycle.get("status")=="cleanup":
            return self._cancel_exits(state,write)

        current,orders=self._owned_remaining(state)
        exchange_total=self._position(state)
        if current==0:
            evidence="owned_exit_fills"
        elif exchange_total==0:
            if self._position(state)!=0:
                raise LifecycleRisk("BingX side briefly appeared flat; retry")
            evidence="exchange_position_flat"
        else:
            evidence=None

        if evidence:
            lifecycle["closure_evidence"]=evidence
            lifecycle["status"]="cleanup"
            self._persist(state,write)
            return self._cancel_exits(state,write)

        if current>exchange_total:
            raise LifecycleRisk(
                "own exchange-confirmed remaining quantity exceeds aggregate BingX positionAmt"
            )

        # Additional combos never block entry. For safety, before changing SL,
        # compare the sum of independently observed owned remainders against
        # the account-wide positionAmt. External/manual reductions then fail
        # closed instead of accidentally resizing an unrelated strategy SL.
        peer_total=Decimal(0)
        for peer in peer_states or []:
            status=(peer.get("lifecycle") or {}).get("status")
            if status=="closed":
                continue
            if status in {"open","cleanup"} and peer.get("ok"):
                peer_qty,_=self._owned_remaining(peer)
                peer_total+=peer_qty
            elif not peer.get("terminal"):
                raise LifecycleRisk(
                    "another same-side entry is still settling; defer SL amendment, not entry"
                )
        if current+peer_total>exchange_total:
            raise LifecycleRisk(
                "exchange positionAmt smaller than summed real unclosed combo fills"
            )

        if lifecycle.get("pending_sl_replace"):
            # Reconcile the deterministic replacement first. Do not submit a
            # different replacement until a prior ambiguous POST is resolved.
            if self._reconcile_replacement(state,write):
                return "sl_resized"

        sl_saved=state["protection"]["sl"]
        old_id=str(sl_saved.get("order_id") or "")
        if not old_id:
            raise LifecycleRisk("tracked SL orderId missing")
        sl_order=orders.get("sl") or self._order(state,order_id=old_id)
        self._validate_owned(state,sl_order,"sl")
        if str(sl_order.get("status") or "").upper()!="NEW":
            raise LifecycleRisk("SL is not NEW while trade remains open")

        sl_qty=_amount(sl_order.get("origQty"),"SL origQty")
        if sl_qty==current:
            lifecycle["remaining_qty"]=format(current,"f")
            if lifecycle.get("last_error"):
                lifecycle.pop("last_error",None)
                self._persist(state,write)
            return "unchanged"
        if sl_qty<current:
            raise LifecycleRisk("own SL amount smaller than this trade's remaining quantity")

        symbol,_,_=self._sid(state)
        rules=self.client.get_contract_rules(symbol)
        precision=rules.get("quantityPrecision")
        minimum=_amount(rules.get("tradeMinQuantity"),"minimum order size")
        if (
            type(precision) is not int or not 0<=precision<=12
            or current<minimum
            or current!=current.quantize(Decimal(1).scaleb(-precision))
        ):
            raise LifecycleRisk("own remaining qty cannot be placed as valid BingX SL")

        other_open=Decimal(0)
        for kind in ("tp","trailing"):
            order=orders.get(kind)
            if not order:
                continue
            if str(order.get("status") or "").upper() in ACTIVE:
                other_open+=(
                    _amount(order.get("origQty"),kind+" origQty")
                    -_amount(order.get("executedQty"),kind+" executedQty")
                )
        if other_open>current:
            raise LifecycleRisk("owned conditional exit quantities exceed own remaining fill")

        # Re-read BingX before mutating. Other combos can be opening/closing
        # concurrently and must not be affected by this SL correction.
        if self._position(state)!=exchange_total:
            raise LifecycleRisk("BingX account-side position changed during SL reconcile")
        fresh_current,_=self._owned_remaining(state)
        if fresh_current!=current:
            raise LifecycleRisk("this trade's own exits changed during SL reconcile")

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
        elif pending["old_order_id"]!=old_id or _amount(pending["quantity"],"pending qty")!=current:
            raise LifecycleRisk("pending replacement no longer matches own live fills")

        try:
            result=self.client.cancel_replace_stop_loss(
                symbol,state["intent"]["side"],old_id,pending["quantity"],
                state["intent"]["sl"],precision,pending["client_order_id"],
                position_id=state.get("position_id"),
            )
            data=result.get("data") or {}
            if data.get("cancelResult")!="SUCCESS" or data.get("newOrderResult")!="SUCCESS":
                raise LifecycleRisk("BingX did not confirm atomic SL cancelReplace success")
        except Exception:
            if self._reconcile_replacement(state,write):
                return "sl_resized"
            raise LifecycleRisk("SL cancelReplace unconfirmed; old SL status must be verified")
        if not self._reconcile_replacement(state,write):
            raise LifecycleRisk("SL replacement acknowledged but not yet queryable")
        return "sl_resized"
