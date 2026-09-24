from __future__ import annotations

import hashlib
import logging
import time

from ..contracts import TradeIntent
from ..strategy.final14_config import case_name,get_case
from .executor import LegacyStyleBingXClient

log=logging.getLogger(__name__)

FINAL14_NOTIONAL_USDT=100.0
FINAL14_EXECUTION_LEVERAGE=50
ENTRY_CONFIRM_ATTEMPTS=10
ENTRY_CONFIRM_SLEEP_SEC=1.5
AMBIGUOUS_RECHECK_ATTEMPTS=3
AMBIGUOUS_RECHECK_SLEEP_SEC=0.5


class Final14Executor:
    """Execute one locked FINAL14 TradeIntent on BingX.

    Safety invariants:
    - deterministic clientOrderId makes MARKET entry/close idempotent;
    - query before send and query after send;
    - only FILLED is treated as a complete entry fill;
    - a partial MARKET fill that does not settle is cancelled and closed;
    - hard SL is placed and verified before hard TP;
    - an entry is complete only after both protections are confirmed.
    """

    def __init__(self,settings,request_guard=None):
        self.client=LegacyStyleBingXClient(settings,request_guard=request_guard)

    @staticmethod
    def client_order_id(event_id:str,purpose:str="entry")->str:
        prefix={"entry":"atv2e","close":"atv2c"}.get(purpose,"atv2x")
        digest=hashlib.sha256(f"{purpose}|{event_id}".encode("utf-8")).hexdigest()[:30]
        return f"{prefix}-{digest}"

    @staticmethod
    def _valid_fill(side:str,avg_price:float,tp:float,sl:float)->bool:
        if side.upper()=="BUY":
            return sl<avg_price<tp
        return tp<avg_price<sl

    @staticmethod
    def _order_id(order:dict)->str:
        return str(order.get("orderID") or order.get("orderId") or "")

    @staticmethod
    def _order_metrics(order:dict)->tuple[str,float,float]:
        status=str(order.get("status") or "").upper()
        executed_qty=float(order.get("executedQty") or 0)
        avg_price=float(order.get("avgPrice") or 0)
        return status,executed_qty,avg_price

    def _find_entry_after_ambiguous_send(
        self,symbol:str,client_order_id:str
    )->tuple[dict|None,str|None]:
        last_error=None
        for attempt in range(1,AMBIGUOUS_RECHECK_ATTEMPTS+1):
            try:
                found=self.client.find_order_by_client_id(symbol,client_order_id)
                if found is not None:
                    return found,None
            except Exception as exc:
                last_error=f"{type(exc).__name__}: {exc}"
            if attempt<AMBIGUOUS_RECHECK_ATTEMPTS:
                time.sleep(AMBIGUOUS_RECHECK_SLEEP_SEC)
        return None,last_error

    def _confirm_market_close(
        self,intent:TradeIntent,qty:float,reason:str
    )->dict:
        cid=self.client_order_id(intent.event_id,"close")
        raw=self.client.find_order_by_client_id(intent.symbol,cid)
        if raw is None:
            try:
                self.client.close_market(
                    intent.symbol,intent.side,qty,FINAL14_EXECUTION_LEVERAGE,
                    client_order_id=cid,
                )
            except Exception as send_exc:
                raw,recheck_error=self._find_entry_after_ambiguous_send(
                    intent.symbol,cid
                )
                if raw is None:
                    return {
                        "confirmed":False,
                        "client_order_id":cid,
                        "reason":reason,
                        "error":(
                            f"emergency close send uncertain: {send_exc}; "
                            f"recheck_error={recheck_error}"
                        ),
                    }

        for attempt in range(1,ENTRY_CONFIRM_ATTEMPTS+1):
            raw=self.client.find_order_by_client_id(intent.symbol,cid)
            if raw is None:
                if attempt<ENTRY_CONFIRM_ATTEMPTS:
                    time.sleep(ENTRY_CONFIRM_SLEEP_SEC)
                continue
            order=self.client.extract_order(raw)
            status,executed_qty,avg_price=self._order_metrics(order)
            if status=="FILLED" and executed_qty>0:
                return {
                    "confirmed":True,
                    "client_order_id":cid,
                    "order_id":self._order_id(order),
                    "status":status,
                    "executed_qty":executed_qty,
                    "avg_price":avg_price,
                    "reason":reason,
                }
            if status in {"CANCELED","EXPIRED"}:
                return {
                    "confirmed":False,
                    "client_order_id":cid,
                    "order_id":self._order_id(order),
                    "status":status,
                    "executed_qty":executed_qty,
                    "reason":reason,
                    "error":"emergency close did not fill",
                }
            if attempt<ENTRY_CONFIRM_ATTEMPTS:
                time.sleep(ENTRY_CONFIRM_SLEEP_SEC)

        return {
            "confirmed":False,
            "client_order_id":cid,
            "reason":reason,
            "error":"emergency close fill not confirmed",
        }

    def _verify_protection(self,symbol:str,payload:dict,kind:str)->dict:
        order=self.client.extract_order(payload)
        order_id=self._order_id(order)
        if not order_id:
            raise RuntimeError(f"{kind} placement did not return orderId")
        raw=self.client.get_order_detail(symbol,order_id=order_id)
        detail=self.client.extract_order(raw)
        status=str(detail.get("status") or "").upper()
        if status not in {"NEW","PARTIALLY_FILLED","FILLED"}:
            raise RuntimeError(f"{kind} not active after placement: status={status or 'UNKNOWN'}")
        return {
            "order_id":order_id,
            "status":status,
            "payload":payload,
        }

    def execute(
        self,
        intent:TradeIntent,
        resume_state:dict|None=None,
        allow_new_entry:bool=True,
    )->dict:
        cfg=get_case(intent.symbol,intent.combo)
        if not cfg:
            raise RuntimeError(
                f"FINAL14 disabled case: {intent.symbol} {case_name(intent.combo)}"
            )

        side=intent.side.upper()
        entry=float(intent.entry)
        tp=float(intent.tp)
        sl=float(intent.sl)
        if side=="BUY" and not (sl<entry<tp):
            raise ValueError("FINAL14 BUY TP/SL ordering invalid")
        if side=="SELL" and not (tp<entry<sl):
            raise ValueError("FINAL14 SELL TP/SL ordering invalid")

        qty=round(FINAL14_NOTIONAL_USDT/entry,4)
        if qty<=0:
            raise ValueError("FINAL14 quantity rounded to zero")

        client_order_id=self.client_order_id(intent.event_id,"entry")
        entry_result=None

        # PRE-CHECK: if the exact deterministic clientOrderId already exists,
        # never submit the MARKET entry again.
        existing=self.client.find_order_by_client_id(intent.symbol,client_order_id)
        if existing is not None:
            entry_result=existing
            log.warning(
                "ENTRY_RECONCILED_EXISTING event_id=%s client_order_id=%s",
                intent.event_id,client_order_id,
            )
        elif not allow_new_entry:
            return {
                "processed":True,
                "entry_accepted":False,
                "entry_filled":False,
                "ok":False,
                "stage":"entry_absent_reconciled",
                "client_order_id":client_order_id,
                "error":"no BingX order exists for persisted event; entry not resent",
            }
        else:
            try:
                entry_result=self.client.place_market_entry(
                    intent.symbol,side,qty,FINAL14_EXECUTION_LEVERAGE,
                    client_order_id=client_order_id,
                )
            except Exception as send_exc:
                # POST may have reached BingX even though its response was lost.
                # Query by deterministic id before deciding anything.
                found,recheck_error=self._find_entry_after_ambiguous_send(
                    intent.symbol,client_order_id
                )
                if found is None:
                    return {
                        "processed":False,
                        "entry_accepted":False,
                        "entry_filled":False,
                        "ok":False,
                        "stage":"entry_unknown_reconcile_required",
                        "client_order_id":client_order_id,
                        "error":(
                            f"entry send uncertain: {send_exc}; "
                            f"recheck_error={recheck_error}"
                        ),
                    }
                entry_result=found

        # POST-CHECK: always query the exchange by clientOrderId.
        status=""
        executed_qty=0.0
        avg_price=0.0
        order_id=""
        for attempt in range(1,ENTRY_CONFIRM_ATTEMPTS+1):
            raw=self.client.find_order_by_client_id(intent.symbol,client_order_id)
            if raw is None:
                if attempt<ENTRY_CONFIRM_ATTEMPTS:
                    time.sleep(ENTRY_CONFIRM_SLEEP_SEC)
                continue
            detail=self.client.extract_order(raw)
            order_id=self._order_id(detail)
            status,executed_qty,avg_price=self._order_metrics(detail)
            log.info(
                "ENTRY_FILL_CHECK symbol=%s client_order_id=%s order_id=%s "
                "attempt=%s status=%s qty=%s avg=%s",
                intent.symbol,client_order_id,order_id,attempt,status,
                executed_qty,avg_price,
            )
            if status=="FILLED" and executed_qty>0 and avg_price>0:
                break
            if status in {"CANCELED","EXPIRED"}:
                break
            if attempt<ENTRY_CONFIRM_ATTEMPTS:
                time.sleep(ENTRY_CONFIRM_SLEEP_SEC)

        if status!="FILLED":
            if status=="PARTIALLY_FILLED" and executed_qty>0:
                try:
                    self.client.cancel_order(
                        intent.symbol,client_order_id=client_order_id
                    )
                except Exception as cancel_exc:
                    return {
                        "processed":False,
                        "entry_accepted":True,
                        "entry_filled":False,
                        "ok":False,
                        "stage":"unsafe_partial_entry",
                        "client_order_id":client_order_id,
                        "order_id":order_id or None,
                        "status":status,
                        "executed_qty":executed_qty,
                        "avg_price":avg_price,
                        "unsafe_open_position":True,
                        "error":f"partial entry could not be cancelled: {cancel_exc}",
                    }
                close=self._confirm_market_close(
                    intent,executed_qty,"partial_entry_timeout"
                )
                if close.get("confirmed"):
                    return {
                        "processed":True,
                        "entry_accepted":True,
                        "entry_filled":False,
                        "ok":False,
                        "stage":"partial_fill_closed",
                        "client_order_id":client_order_id,
                        "order_id":order_id or None,
                        "status":status,
                        "executed_qty":executed_qty,
                        "avg_price":avg_price,
                        "close_result":close,
                        "error":"partial MARKET fill cancelled and closed safely",
                    }
                return {
                    "processed":False,
                    "entry_accepted":True,
                    "entry_filled":False,
                    "ok":False,
                    "stage":"unsafe_open_position",
                    "client_order_id":client_order_id,
                    "order_id":order_id or None,
                    "status":status,
                    "executed_qty":executed_qty,
                    "avg_price":avg_price,
                    "unsafe_open_position":True,
                    "close_result":close,
                    "error":"partial MARKET fill could not be confirmed closed",
                }

            return {
                "processed":status in {"CANCELED","EXPIRED"} and executed_qty<=0,
                "entry_accepted":bool(order_id or entry_result),
                "entry_filled":False,
                "ok":False,
                "stage":(
                    "entry_not_filled_terminal"
                    if status in {"CANCELED","EXPIRED"} and executed_qty<=0
                    else "entry_unknown_reconcile_required"
                ),
                "client_order_id":client_order_id,
                "order_id":order_id or None,
                "status":status,
                "executed_qty":executed_qty,
                "avg_price":avg_price,
                "error":"entry did not reach confirmed FILLED state",
            }

        if not self._valid_fill(side,avg_price,tp,sl):
            close=self._confirm_market_close(
                intent,executed_qty,"fill_outside_final14_range"
            )
            if close.get("confirmed"):
                return {
                    "processed":True,
                    "entry_accepted":True,
                    "entry_filled":True,
                    "ok":False,
                    "stage":"fill_outside_final14_range_closed",
                    "client_order_id":client_order_id,
                    "order_id":order_id,
                    "avg_price":avg_price,
                    "executed_qty":executed_qty,
                    "tp":tp,
                    "sl":sl,
                    "close_result":close,
                    "error":"actual fill outside FINAL14 TP/SL envelope",
                }
            return {
                "processed":False,
                "entry_accepted":True,
                "entry_filled":True,
                "ok":False,
                "stage":"unsafe_open_position",
                "client_order_id":client_order_id,
                "order_id":order_id,
                "avg_price":avg_price,
                "executed_qty":executed_qty,
                "tp":tp,
                "sl":sl,
                "unsafe_open_position":True,
                "close_result":close,
                "error":"invalid fill and emergency close not confirmed",
            }

        resume_state=resume_state or {}
        old_stage=str(resume_state.get("stage") or "")
        old_protection=resume_state.get("protection") or {}
        sl_verified=None

        # If a previous attempt already confirmed SL but failed TP, do not place
        # another SL during reconciliation.
        if old_stage in {
            "protection_tp_failed_sl_active",
            "protection_tp_unconfirmed_sl_active",
        }:
            old_sl=old_protection.get("sl") or {}
            old_sl_id=str(old_sl.get("order_id") or "")
            if old_sl_id:
                raw=self.client.get_order_detail(intent.symbol,order_id=old_sl_id)
                detail=self.client.extract_order(raw)
                sl_status=str(detail.get("status") or "").upper()
                if sl_status in {"NEW","PARTIALLY_FILLED","FILLED"}:
                    sl_verified={
                        "order_id":old_sl_id,
                        "status":sl_status,
                        "payload":old_sl.get("payload"),
                    }

        if sl_verified is None:
            try:
                sl_payload=self.client.place_stop_loss(
                    intent.symbol,side,executed_qty,sl
                )
                sl_verified=self._verify_protection(
                    intent.symbol,sl_payload,"stop-loss"
                )
            except Exception as sl_exc:
                log.exception(
                    "PROTECTION_SL_FAILED symbol=%s order_id=%s; emergency closing",
                    intent.symbol,order_id,
                )
                close=self._confirm_market_close(
                    intent,executed_qty,"stop_loss_unconfirmed"
                )
                if close.get("confirmed"):
                    return {
                        "processed":True,
                        "entry_accepted":True,
                        "entry_filled":True,
                        "ok":False,
                        "stage":"protection_sl_failed_closed",
                        "client_order_id":client_order_id,
                        "order_id":order_id,
                        "avg_price":avg_price,
                        "executed_qty":executed_qty,
                        "tp":tp,
                        "sl":sl,
                        "emergency_closed":True,
                        "close_result":close,
                        "error":f"stop-loss placement/verification failed; position closed: {sl_exc}",
                    }
                return {
                    "processed":False,
                    "entry_accepted":True,
                    "entry_filled":True,
                    "ok":False,
                    "stage":"unsafe_open_position",
                    "client_order_id":client_order_id,
                    "order_id":order_id,
                    "avg_price":avg_price,
                    "executed_qty":executed_qty,
                    "tp":tp,
                    "sl":sl,
                    "unsafe_open_position":True,
                    "close_result":close,
                    "error":(
                        f"stop-loss placement/verification failed ({sl_exc}); "
                        "emergency close not confirmed"
                    ),
                }

        try:
            tp_payload=self.client.place_take_profit(
                intent.symbol,side,executed_qty,tp
            )
            tp_verified=self._verify_protection(
                intent.symbol,tp_payload,"take-profit"
            )
        except Exception as tp_exc:
            log.exception(
                "PROTECTION_TP_FAILED_SL_ACTIVE symbol=%s order_id=%s",
                intent.symbol,order_id,
            )
            return {
                "processed":False,
                "entry_accepted":True,
                "entry_filled":True,
                "ok":False,
                "stage":"protection_tp_failed_sl_active",
                "client_order_id":client_order_id,
                "order_id":order_id,
                "avg_price":avg_price,
                "executed_qty":executed_qty,
                "target_notional":FINAL14_NOTIONAL_USDT,
                "execution_leverage":FINAL14_EXECUTION_LEVERAGE,
                "tp":tp,
                "sl":sl,
                "sl_protected":True,
                "protection":{"sl":sl_verified,"tp":None},
                "error":f"take-profit placement/verification failed; hard SL remains active: {tp_exc}",
            }

        protection={"tp":tp_verified,"sl":sl_verified}
        return {
            "processed":True,
            "entry_accepted":True,
            "entry_filled":True,
            "ok":True,
            "stage":"complete",
            "client_order_id":client_order_id,
            "order_id":order_id,
            "avg_price":avg_price,
            "executed_qty":executed_qty,
            "target_notional":FINAL14_NOTIONAL_USDT,
            "execution_leverage":FINAL14_EXECUTION_LEVERAGE,
            "tp":tp,
            "sl":sl,
            "rr":float(cfg["rr"]),
            "tp_pct":float(cfg["tp_pct"]),
            "sl_pct":float(cfg["sl_pct"]),
            "protection_mode":"separate_hard_tp_sl",
            "entry_result":entry_result,
            "protection":protection,
            "error":None,
        }

    def execute_safe(
        self,
        intent:TradeIntent,
        resume_state:dict|None=None,
        allow_new_entry:bool=True,
    )->dict:
        try:
            return self.execute(
                intent,
                resume_state=resume_state,
                allow_new_entry=allow_new_entry,
            )
        except Exception as exc:
            log.exception(
                "FINAL14_EXEC_FAILED event_id=%s",
                intent.event_id,
            )
            return {
                "ok":False,
                "processed":False,
                "entry_accepted":False,
                "entry_filled":False,
                "stage":"reconcile_error",
                "client_order_id":self.client_order_id(intent.event_id,"entry"),
                "error":str(exc),
            }
