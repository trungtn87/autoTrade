from __future__ import annotations

import hashlib
from decimal import Decimal
import logging
import time

from ..contracts import TradeIntent
from ..strategy.final14_config import case_name,get_case
from .executor import LegacyStyleBingXClient
from .quantity import allocate_exit_qty,confirmed_position_qty,PositionQuantityMismatch
from .identity import identify_entry_position
from .protection_journal import ProtectionJournal,ProtectionReconcilePending
from .close_position_check import position_remaining,PositionConfirmationError

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
    - hard SL is placed and verified before fixed TP and optional trailing;
    - an entry is complete only after all selected protections are confirmed.
    """

    def __init__(self,settings,request_guard=None):
        self.settings=settings
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
    def trailing_activation_price(entry_price:float,side:str,activation_pct:float)->float:
        """Favorable move from the confirmed exchange entry, NOT the combo TP."""
        if entry_price<=0 or not 0<activation_pct<1:
            raise ValueError("invalid trailing entry or activation percentage")
        entry=Decimal(str(entry_price))
        pct=Decimal(str(activation_pct))
        if side.upper()=="BUY":
            return float(entry*(Decimal(1)+pct))
        if side.upper()=="SELL":
            return float(entry*(Decimal(1)-pct))
        raise ValueError("trailing activation side must be BUY or SELL")

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
        self,intent:TradeIntent,qty:float,reason:str,
        position_id:str|None=None,resume_state:dict|None=None,
        protection_persist=None,
    )->dict:
        # Separate Isolated must NOT be closed by side alone when multiple
        # positions coexist. Resolve from durable pre-entry snapshot if supplied.
        before=(resume_state or {}).get("pre_entry_positions")
        if position_id is None and isinstance(before,dict):
            try:
                position_id=identify_entry_position(
                    self.client.get_positions(intent.symbol),
                    intent.symbol,intent.side,qty,baseline=before,
                )
            except Exception as exc:
                return {
                    "confirmed":False,"reason":reason,
                    "error":f"positionId not attributable; refusing ambiguous close: {exc}",
                }
        state=resume_state if resume_state is not None else {}
        journal=dict(state.get("emergency_close") or {})
        cid=str(journal.get("client_order_id") or self.client_order_id(intent.event_id,"close"))
        if journal and str(journal.get("position_id") or "")!=str(position_id or ""):
            return {"confirmed":False,"reason":reason,
                    "error":"emergency close journal positionId mismatch; refusing another order"}
        if protection_persist is not None:
            try:
                if not position_id:
                    raise PositionConfirmationError("cannot close without uniquely identified positionId")
                position_qty=position_remaining(
                    self.client,intent.symbol,intent.side,str(position_id)
                )
            except Exception as exc:
                return {"confirmed":False,"reason":reason,
                        "error":f"pre-close position could not be verified: {exc}"}
            if position_qty==0:
                try:
                    second=position_remaining(
                        self.client,intent.symbol,intent.side,str(position_id)
                    )
                except Exception as exc:
                    return {"confirmed":False,"reason":reason,
                            "error":f"cannot confirm flat position twice: {exc}"}
                if second!=0:
                    return {"confirmed":False,"reason":reason,
                            "error":"conflicting BingX position snapshots; refusing another close"}
                return {"confirmed":True,"reason":reason,"status":"POSITION_FLAT",
                        "position_id":position_id,"remaining_qty":"0"}
            if float(position_qty)>qty+0.0001:
                return {"confirmed":False,"reason":reason,
                        "error":"position quantity exceeds entry fill; refusing cross-combo close",
                        "position_id":position_id,"remaining_qty":str(position_qty)}

        # Use the live remaining size for the FIRST close attempt. The SL
        # may already have reduced this isolated position before recovery.
        close_qty=float(position_qty) if protection_persist is not None else qty
        raw=self.client.find_order_by_client_id(intent.symbol,cid)
        if raw is None:
            if journal:
                # Prepared or previously sent. Missing lookup is not proof
                # that the MARKET POST was rejected: never re-submit.
                return {"confirmed":False,"client_order_id":cid,"reason":reason,
                        "error":"prepared emergency close lookup unresolved; not resending"}
            previous_close=state.get("close_result") or {}
            if (isinstance(previous_close,dict)
                    and str(previous_close.get("client_order_id") or "")==cid):
                # Recovery from legacy state without a write-ahead journal.
                # The saved close CID may already have been accepted by BingX.
                return {"confirmed":False,"client_order_id":cid,"reason":reason,
                        "error":"previous close send unresolved; no legacy duplicate"}

            if protection_persist is not None:
                journal={"client_order_id":cid,"position_id":str(position_id),
                         "reason":reason,"phase":"prepared",
                         "requested_qty":str(close_qty),"revision":0}
                prepared={**state,"emergency_close":journal}
                protection_persist(prepared)  # durable BEFORE signed MARKET POST
                state.clear()
                state.update(prepared)
            try:
                kwargs={"position_id":position_id} if position_id else {}
                self.client.close_market(
                    intent.symbol,intent.side,close_qty,FINAL14_EXECUTION_LEVERAGE,
                    client_order_id=cid,**kwargs,
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
            if order.get("clientOrderId") and str(order["clientOrderId"]).lower()!=cid.lower():
                return {"confirmed":False,"reason":reason,
                        "error":"emergency close lookup returned different clientOrderId"}
            if position_id and order.get("positionId") and str(order["positionId"])!=str(position_id):
                return {"confirmed":False,"reason":reason,
                        "error":"emergency close lookup returned different positionId"}
            if order.get("symbol") and str(order["symbol"])!=intent.symbol:
                return {"confirmed":False,"reason":reason,
                        "error":"emergency close lookup returned different symbol"}
            if order.get("positionSide") and str(order["positionSide"]).upper()!=(
                "LONG" if intent.side.upper()=="BUY" else "SHORT"
            ):
                return {"confirmed":False,"reason":reason,
                        "error":"emergency close lookup returned different positionSide"}
            if order.get("type") and str(order["type"]).upper()!="MARKET":
                return {"confirmed":False,"reason":reason,
                        "error":"emergency close lookup returned a non-MARKET order"}
            status,executed_qty,avg_price=self._order_metrics(order)
            if status in {"FILLED","CANCELED","EXPIRED","PARTIALLY_FILLED"}:
                remaining=None
                verification_error=None
                try:
                    if not position_id:
                        raise PositionConfirmationError(
                            "missing positionId: cannot prove Separate Isolated position flat"
                        )
                    remaining=position_remaining(
                        self.client,intent.symbol,intent.side,str(position_id)
                    )
                except Exception as exc:
                    verification_error=f"{type(exc).__name__}: {exc}"
                base={
                    "client_order_id":cid,
                    "order_id":self._order_id(order),
                    "status":status,
                    "executed_qty":executed_qty,
                    "avg_price":avg_price,
                    "reason":reason,
                    "position_id":position_id,
                    "remaining_qty":str(remaining) if remaining is not None else None,
                }
                # Order status alone cannot guarantee that the specific
                # isolated position is closed, especially after partial fills.
                if remaining is not None and remaining==0:
                    try:
                        repeat=position_remaining(
                            self.client,intent.symbol,intent.side,str(position_id)
                        )
                        if repeat==0:
                            return {"confirmed":True,**base}
                        return {"confirmed":False,**base,
                                "error":"conflicting BingX position snapshots after close"}
                    except Exception as exc:
                        return {"confirmed":False,**base,
                                "error":f"second flat-position confirmation failed: {exc}"}
                if status in {"CANCELED","EXPIRED"}:
                    if protection_persist is not None and remaining is not None and journal:
                        # Only a terminal order with a consistent remaining
                        # quantity may start a new, bounded close revision.
                        # Never retry merely because an exchange query timed out.
                        from decimal import Decimal,ROUND_DOWN
                        submitted=Decimal(str(journal.get("requested_qty") or "0"))
                        filled=Decimal(str(executed_qty))
                        revision=int(journal.get("revision",0))
                        if (submitted>0 and filled>=0 and filled<=submitted
                                and abs(remaining-(submitted-filled))<=Decimal("0.0001")
                                and revision<2):
                            next_revision=revision+1
                            next_qty=remaining.quantize(Decimal("0.0001"),rounding=ROUND_DOWN)
                            if next_qty>0 and next_qty<=submitted:
                                next_cid=self.client_order_id(
                                    intent.event_id,f"close-retry-{next_revision}"
                                )
                                # A stale worker may have sent this revision.
                                # Persist its CID first and recover, never
                                # blindly submit an already known client ID.
                                existing_next=self.client.find_order_by_client_id(
                                    intent.symbol,next_cid
                                )
                                next_journal={
                                    "client_order_id":next_cid,"position_id":str(position_id),
                                    "reason":reason,"phase":"prepared",
                                    "requested_qty":str(next_qty),
                                    "revision":next_revision,
                                    "previous_client_order_id":cid,
                                }
                                prepared={**state,"emergency_close":next_journal}
                                protection_persist(prepared)
                                state.clear()
                                state.update(prepared)
                                if existing_next is None:
                                    try:
                                        self.client.close_market(
                                            intent.symbol,intent.side,float(next_qty),
                                            FINAL14_EXECUTION_LEVERAGE,
                                            client_order_id=next_cid,position_id=position_id,
                                        )
                                    except Exception:
                                        # CID is durable. Recovery queries
                                        # before considering anything else.
                                        pass
                                return self._confirm_market_close(
                                    intent,qty,reason,position_id=position_id,
                                    resume_state=state,
                                    protection_persist=protection_persist,
                                )
                    return {
                        "confirmed":False,**base,
                        "error":"emergency close canceled/expired; residual unverified "
                        "or maximum bounded close attempts reached"
                        + (f"; {verification_error}" if verification_error else ""),
                    }
                if status=="FILLED":
                    return {
                        "confirmed":False,**base,
                        "error":"emergency close FILLED, but position is not independently confirmed flat"
                        + (f"; {verification_error}" if verification_error else ""),
                    }
                return {
                    "confirmed":False,**base,
                    "error":"partial close in progress; no new close until exchange status reconciles"
                    + (f"; {verification_error}" if verification_error else ""),
                }
            if attempt<ENTRY_CONFIRM_ATTEMPTS:
                time.sleep(ENTRY_CONFIRM_SLEEP_SEC)

        return {
            "confirmed":False,
            "client_order_id":cid,
            "reason":reason,
            "error":"emergency close fill not confirmed",
        }

    def _verify_protection(
        self,symbol:str,payload:dict,kind:str,position_id:str|None=None
    )->dict:
        order=self.client.extract_order(payload)
        order_id=self._order_id(order)
        if not order_id:
            raise RuntimeError(f"{kind} placement did not return orderId")
        raw=self.client.get_order_detail(symbol,order_id=order_id)
        detail=self.client.extract_order(raw)
        status=str(detail.get("status") or "").upper()
        if position_id and detail.get("positionId") and str(detail["positionId"])!=str(position_id):
            raise RuntimeError(f"{kind} targets the wrong positionId")
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
        protection_persist=None,
    )->dict:
        resume_state=resume_state or {}
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
        filled_qty_raw="0"
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
            filled_qty_raw=str(detail.get("executedQty") or "0")
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

                # Cancellation and matching can race. Re-query until the entry
                # reaches a terminal status, then use the FINAL executedQty.
                final_status=status
                final_qty=executed_qty
                final_avg=avg_price
                final_order_id=order_id
                for attempt in range(1,AMBIGUOUS_RECHECK_ATTEMPTS+1):
                    raw=self.client.find_order_by_client_id(
                        intent.symbol,client_order_id
                    )
                    if raw is not None:
                        detail=self.client.extract_order(raw)
                        final_order_id=self._order_id(detail) or final_order_id
                        final_status,final_qty,final_avg=self._order_metrics(detail)
                        if final_status in {"FILLED","CANCELED","EXPIRED"}:
                            break
                    if attempt<AMBIGUOUS_RECHECK_ATTEMPTS:
                        time.sleep(AMBIGUOUS_RECHECK_SLEEP_SEC)

                if final_status not in {"FILLED","CANCELED","EXPIRED"}:
                    return {
                        "processed":False,
                        "entry_accepted":True,
                        "entry_filled":False,
                        "ok":False,
                        "stage":"unsafe_partial_entry",
                        "client_order_id":client_order_id,
                        "order_id":final_order_id or None,
                        "status":final_status,
                        "executed_qty":final_qty,
                        "avg_price":final_avg,
                        "unsafe_open_position":True,
                        "error":"partial entry cancellation not confirmed terminal",
                    }

                if final_qty<=0:
                    return {
                        "processed":True,
                        "entry_accepted":True,
                        "entry_filled":False,
                        "ok":False,
                        "stage":"entry_not_filled_terminal",
                        "client_order_id":client_order_id,
                        "order_id":final_order_id or None,
                        "status":final_status,
                        "executed_qty":0.0,
                        "avg_price":0.0,
                        "error":"partial entry cancelled before any fill remained",
                    }

                executed_qty=final_qty
                avg_price=final_avg or avg_price
                status=final_status
                order_id=final_order_id
                close=self._confirm_market_close(
                    intent,executed_qty,"partial_entry_timeout",
                    position_id=str(detail.get("positionId") or "") or None,
                    resume_state=resume_state,protection_persist=protection_persist,
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
                intent,executed_qty,"fill_outside_final14_range",
                position_id=str(detail.get("positionId") or "") or None,
                resume_state=resume_state,protection_persist=protection_persist,
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

        # Freeze the per-trade exit mode across restart/recovery and config changes.
        exit_mode=str(resume_state.get("exit_mode") or (
            "split_trailing" if self.settings.tp_trailing_split_enabled else "hard_tp"
        ))
        if exit_mode not in {"hard_tp","split_trailing"}:
            raise ValueError(f"unsupported exit mode: {exit_mode}")
        split_trailing=exit_mode=="split_trailing"
        tp_qty=executed_qty
        trail_qty=0.0
        qty_precision=None
        actual_position_qty=None
        position_id=None
        split_fallback_reason=None
        old_protection=resume_state.get("protection") or {}
        # On production, the caller persists journal snapshots in Supabase
        # *before* every conditional POST and after every confirmation.
        journal=(
            ProtectionJournal(
                self.client,resume_state,protection_persist,
                lambda raw,kind,pid:self._verify_protection(
                    intent.symbol,raw,kind,pid
                ),
            ) if protection_persist is not None else None
        )
        # Resolve the particular position BEFORE placing conditional exit orders.
        # Baseline is durable in Supabase prior to the MARKET POST.
        before=resume_state.get("pre_entry_positions")
        if split_trailing and isinstance(before,dict):
            last_error=None
            for attempt in range(1,AMBIGUOUS_RECHECK_ATTEMPTS+1):
                try:
                    position_id=identify_entry_position(
                        self.client.get_positions(intent.symbol),
                        intent.symbol,side,filled_qty_raw,baseline=before,
                        entry_position_id=str(detail.get("positionId") or "") or None,
                    )
                    break
                except Exception as exc:
                    last_error=exc
                    if attempt<AMBIGUOUS_RECHECK_ATTEMPTS:
                        time.sleep(AMBIGUOUS_RECHECK_SLEEP_SEC)
            if not position_id:
                return {
                    "processed":False,"entry_accepted":True,"entry_filled":True,
                    "ok":False,"stage":"position_identification_pending",
                    "client_order_id":client_order_id,"order_id":order_id,
                    "avg_price":avg_price,"executed_qty":executed_qty,
                    "unsafe_open_position":True,
                    "error":f"cannot attribute entry to exactly one BingX positionId: {last_error}",
                }
        sl_verified=None
        tp_verified=None
        trailing_verified=None

        # Reuse already-known protection order IDs after restart. This prevents
        # recovery from placing duplicate SL/TP orders.
        old_sl=old_protection.get("sl") or {}
        old_sl_id=str(old_sl.get("order_id") or "")
        if old_sl_id:
            try:
                raw=self.client.get_order_detail(intent.symbol,order_id=old_sl_id)
                detail=self.client.extract_order(raw)
                sl_status=str(detail.get("status") or "").upper()
                if sl_status in {"NEW","PARTIALLY_FILLED","FILLED"}:
                    sl_verified={
                        "order_id":old_sl_id,
                        "status":sl_status,
                        "payload":old_sl.get("payload"),
                    }
            except Exception:
                pass

        old_tp=old_protection.get("tp") or {}
        old_tp_id=str(old_tp.get("order_id") or "")
        if old_tp_id:
            try:
                raw=self.client.get_order_detail(intent.symbol,order_id=old_tp_id)
                detail=self.client.extract_order(raw)
                tp_status=str(detail.get("status") or "").upper()
                if tp_status in {"NEW","PARTIALLY_FILLED","FILLED"}:
                    tp_verified={
                        "order_id":old_tp_id,
                        "status":tp_status,
                        "payload":old_tp.get("payload"),
                    }
            except Exception:
                pass

        old_trailing=old_protection.get("trailing") or {}
        old_trailing_id=str(old_trailing.get("order_id") or "")
        if old_trailing_id and split_trailing:
            try:
                raw=self.client.get_order_detail(intent.symbol,order_id=old_trailing_id)
                detail=self.client.extract_order(raw)
                status=str(detail.get("status") or "").upper()
                if status in {"NEW","PARTIALLY_FILLED","FILLED"}:
                    trailing_verified={
                        "order_id":old_trailing_id,
                        "status":status,
                        "payload":old_trailing.get("payload"),
                    }
            except Exception:
                pass

        # A previously VERIFIED Order ID is more authoritative than any
        # new lookup. Never mint a second exit because a recorded order cannot
        # currently be verified (timeout, canceled, filled but unreported).
        # Lifecycle/manual reconciliation must resolve its terminal state.
        if journal is not None:
            for kind,old_id,verified in (
                ("sl",old_sl_id,sl_verified),
                ("tp",old_tp_id,tp_verified),
                ("trailing",old_trailing_id if split_trailing else "",trailing_verified),
            ):
                if old_id and verified is None:
                    raise ProtectionReconcilePending(
                        f"{kind}: persisted protection order {old_id} is unverified; "
                        "refusing a replacement without exchange-side reconciliation"
                    )

        if sl_verified is None:
            try:
                if journal is not None:
                    sl_verified=journal.place(
                        "sl",intent.symbol,side,executed_qty,sl,position_id,
                        lambda:self.client.place_stop_loss(
                            intent.symbol,side,executed_qty,sl,
                            **({"position_id":position_id} if position_id else {})
                        ),
                    )
                else:
                    sl_payload=self.client.place_stop_loss(
                        intent.symbol,side,executed_qty,sl,
                        **({"position_id":position_id} if position_id else {})
                    )
                    sl_verified=self._verify_protection(
                        intent.symbol,sl_payload,"stop-loss",position_id
                    )
            except ProtectionReconcilePending as pending:
                return {
                    "processed":False,"entry_accepted":True,"entry_filled":True,
                    "ok":False,"stage":"protection_sl_reconcile_pending",
                    "client_order_id":client_order_id,"order_id":order_id,
                    "executed_qty":executed_qty,"avg_price":avg_price,
                    "unsafe_open_position":True,
                    "protection":resume_state.get("protection") or {},
                    "error":str(pending),
                }
            except Exception as sl_exc:
                if journal is not None:
                    # Journal snapshot/persistence/transport was not verified.
                    # Never trust an arbitrary same-side open order or retry.
                    return {
                        "processed":False,"entry_accepted":True,"entry_filled":True,
                        "ok":False,"stage":"protection_sl_reconcile_pending",
                        "client_order_id":client_order_id,"order_id":order_id,
                        "executed_qty":executed_qty,"avg_price":avg_price,
                        "unsafe_open_position":True,
                        "protection":resume_state.get("protection") or {},
                        "error":f"SL WAL reconciliation failed: {sl_exc}",
                    }
                # The POST may have reached BingX while its response was lost.
                # Search current open protection orders before emergency close.
                try:
                    matched=self.client.find_matching_protection(
                        intent.symbol,side,executed_qty,sl,"STOP_MARKET",
                        **({"position_id":position_id} if position_id else {})
                    )
                    if matched is not None:
                        sl_verified=self._verify_protection(
                            intent.symbol,matched,"stop-loss",position_id
                        )
                except Exception:
                    sl_verified=None

                if sl_verified is None:
                    log.exception(
                        "PROTECTION_SL_FAILED symbol=%s order_id=%s; emergency closing",
                        intent.symbol,order_id,
                    )
                    close=self._confirm_market_close(
                        intent,executed_qty,"stop_loss_unconfirmed",
                        position_id=position_id or str(detail.get("positionId") or "") or None,
                        resume_state=resume_state,
                        protection_persist=protection_persist,
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

        sl_status=str(sl_verified.get("status") or "").upper()
        if sl_status=="FILLED":
            return {
                "processed":True,
                "entry_accepted":True,
                "entry_filled":True,
                "ok":False,
                "stage":"closed_by_sl_during_protection",
                "client_order_id":client_order_id,
                "order_id":order_id,
                "avg_price":avg_price,
                "executed_qty":executed_qty,
                "tp":tp,
                "sl":sl,
                "protection":{"sl":sl_verified,"tp":tp_verified},
                "error":"stop-loss filled before protection setup completed",
            }
        if sl_status=="PARTIALLY_FILLED":
            return {
                "processed":False,
                "entry_accepted":True,
                "entry_filled":True,
                "ok":False,
                "stage":"sl_execution_in_progress",
                "client_order_id":client_order_id,
                "order_id":order_id,
                "avg_price":avg_price,
                "executed_qty":executed_qty,
                "tp":tp,
                "sl":sl,
                "protection":{"sl":sl_verified,"tp":tp_verified},
                "error":"stop-loss is partially filled; protection reconciliation required",
            }

        if split_trailing:
            # SL is already active. Before splitting, independently read the
            # exchange position and current contract lot rules. Never trust
            # formula-derived size, floats or a local expected remainder.
            try:
                positions=self.client.get_positions(intent.symbol)
                actual_position_qty,position_id=confirmed_position_qty(
                    positions,intent.symbol,side,filled_qty_raw,
                    allow_aggregate=True,expected_position_id=position_id,
                )
                old_position_id=resume_state.get("position_id")
                if old_position_id and str(old_position_id)!=str(position_id):
                    raise PositionQuantityMismatch(
                        "BingX positionId changed during recovery"
                    )
                rules=self.client.get_contract_rules(intent.symbol)
                allocation=allocate_exit_qty(
                    actual_position_qty,rules,min(avg_price,tp)
                )
                qty_precision=allocation["quantity_precision"]
                tp_qty=allocation["tp_qty"]
                trail_qty=allocation["trailing_qty"]
                if allocation["mode"]=="hard_tp":
                    exit_mode="hard_tp"
                    split_trailing=False
                    split_fallback_reason=allocation["reason"]
                    log.warning(
                        "SPLIT_BELOW_BINGX_MINIMUM symbol=%s position=%s min_qty=%s min_usdt=%s; using full hard TP",
                        intent.symbol,actual_position_qty,allocation["min_order_qty"],allocation["min_order_usdt"],
                    )
                else:
                    log.info(
                        "BINGX_EXACT_SPLIT symbol=%s position_id=%s executed_qty=%s position_amt=%s tp_qty=%s trail_qty=%s precision=%s",
                        intent.symbol,position_id,filled_qty_raw,actual_position_qty,tp_qty,trail_qty,qty_precision,
                    )
            except Exception as snapshot_exc:
                stage=(
                    "position_quantity_mismatch_sl_active"
                    if isinstance(snapshot_exc,PositionQuantityMismatch)
                    else "position_snapshot_unavailable_sl_active"
                )
                return {
                    "processed":False,
                    "entry_accepted":True,
                    "entry_filled":True,
                    "ok":False,
                    "stage":stage,
                    "client_order_id":client_order_id,
                    "order_id":order_id,
                    "avg_price":avg_price,
                    "executed_qty":executed_qty,
                    "tp":tp,
                    "sl":sl,
                    "sl_protected":True,
                    "protection":{"sl":sl_verified,"tp":tp_verified,"trailing":trailing_verified},
                    "error":f"BingX position/contract snapshot unverified: {snapshot_exc}",
                }

        # If TP was previously confirmed, reuse it. Otherwise place it. On a
        # lost TP response, first search open orders for the exact protection.
        if tp_verified is None:
            try:
                if journal is not None:
                    tp_verified=journal.place(
                        "tp",intent.symbol,side,tp_qty,tp,position_id,
                        lambda:self.client.place_take_profit(
                            intent.symbol,side,tp_qty,tp,
                            **({"quantity_precision":qty_precision} if qty_precision is not None else {}),
                            **({"position_id":position_id} if position_id else {}),
                        ),
                    )
                else:
                    tp_payload=self.client.place_take_profit(
                        intent.symbol,side,tp_qty,tp,
                        **({"quantity_precision":qty_precision} if qty_precision is not None else {}),
                        **({"position_id":position_id} if position_id else {}),
                    )
                    tp_verified=self._verify_protection(
                        intent.symbol,tp_payload,"take-profit",position_id
                    )
            except ProtectionReconcilePending as pending:
                return {
                    "processed":False,"entry_accepted":True,"entry_filled":True,
                    "ok":False,"stage":"protection_tp_reconcile_pending",
                    "client_order_id":client_order_id,"order_id":order_id,
                    "executed_qty":executed_qty,"avg_price":avg_price,
                    "sl_protected":True,
                    "protection":resume_state.get("protection") or {"sl":sl_verified},
                    "error":str(pending),
                }
            except Exception as tp_exc:
                if journal is not None:
                    return {
                        "processed":False,"entry_accepted":True,"entry_filled":True,
                        "ok":False,"stage":"protection_tp_reconcile_pending",
                        "client_order_id":client_order_id,"order_id":order_id,
                        "executed_qty":executed_qty,"avg_price":avg_price,
                        "sl_protected":True,
                        "protection":resume_state.get("protection") or {"sl":sl_verified},
                        "error":f"TP WAL reconciliation failed: {tp_exc}",
                    }
                try:
                    matched=self.client.find_matching_protection(
                        intent.symbol,side,tp_qty,tp,"TAKE_PROFIT_MARKET",
                        **({"position_id":position_id} if position_id else {}),
                    )
                    if matched is not None:
                        tp_verified=self._verify_protection(
                            intent.symbol,matched,"take-profit",position_id
                        )
                except Exception:
                    tp_verified=None

                if tp_verified is None:
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

        tp_status=str(tp_verified.get("status") or "").upper()
        if tp_status=="PARTIALLY_FILLED":
            return {
                "processed":False,
                "entry_accepted":True,
                "entry_filled":True,
                "ok":False,
                "stage":"tp_execution_in_progress",
                "client_order_id":client_order_id,
                "order_id":order_id,
                "avg_price":avg_price,
                "executed_qty":executed_qty,
                "tp":tp,
                "sl":sl,
                "protection":{"sl":sl_verified,"tp":tp_verified},
                "error":"take-profit is partially filled; protection reconciliation required",
            }

        if tp_status=="FILLED" and not split_trailing:
            try:
                self.client.cancel_order(
                    intent.symbol,order_id=str(sl_verified["order_id"])
                )
                return {
                    "processed":True,
                    "entry_accepted":True,
                    "entry_filled":True,
                    "ok":False,
                    "stage":"closed_by_tp_during_protection",
                    "client_order_id":client_order_id,
                    "order_id":order_id,
                    "avg_price":avg_price,
                    "executed_qty":executed_qty,
                    "tp":tp,
                    "sl":sl,
                    "protection":{"sl":sl_verified,"tp":tp_verified},
                    "error":"take-profit filled during setup; remaining SL cancelled",
                }
            except Exception as cancel_exc:
                return {
                    "processed":False,
                    "entry_accepted":True,
                    "entry_filled":True,
                    "ok":False,
                    "stage":"orphan_sl_cleanup_required",
                    "client_order_id":client_order_id,
                    "order_id":order_id,
                    "avg_price":avg_price,
                    "executed_qty":executed_qty,
                    "tp":tp,
                    "sl":sl,
                    "protection":{"sl":sl_verified,"tp":tp_verified},
                    "error":f"TP filled but remaining SL cancellation failed: {cancel_exc}",
                }

        if split_trailing:
            # Hard TP is per combo; trailing activates independently once the
            # CONFIRMED exchange entry advances favorably by 0.5% (default).
            # Preserve already-prepared/existing conditional order parameters
            # across deploy/restart: never silently recreate a live order.
            callback_rate=self.settings.tp_trailing_callback_rate
            saved_activation=resume_state.get("trailing_activation_price")
            trailing_journal=((resume_state.get("protection_journal") or {}).get("trailing") or {})
            prepared_activation=(trailing_journal.get("spec") or {}).get("trigger")
            if saved_activation is not None:
                activation_price=float(saved_activation)
            elif prepared_activation is not None:
                activation_price=float(prepared_activation)
            elif old_trailing_id:
                # Existing trades placed under the old TP-activation release.
                activation_price=tp
            else:
                activation_price=self.trailing_activation_price(
                    avg_price,side,self.settings.tp_trailing_activation_pct
                )
            if trailing_verified is None:
                try:
                    if journal is not None:
                        trailing_verified=journal.place(
                            "trailing",intent.symbol,side,trail_qty,activation_price,position_id,
                            lambda:self.client.place_trailing_stop(
                                intent.symbol,side,trail_qty,activation_price,callback_rate,
                                **({"quantity_precision":qty_precision} if qty_precision is not None else {}),
                                **({"position_id":position_id} if position_id else {}),
                            ),callback_rate=callback_rate,
                        )
                    else:
                        trailing_payload=self.client.place_trailing_stop(
                            intent.symbol,side,trail_qty,activation_price,callback_rate,
                            **({"quantity_precision":qty_precision} if qty_precision is not None else {}),
                            **({"position_id":position_id} if position_id else {}),
                        )
                        trailing_verified=self._verify_protection(
                            intent.symbol,trailing_payload,"trailing-stop",position_id
                        )
                except ProtectionReconcilePending as pending:
                    return {
                        "processed":False,"entry_accepted":True,"entry_filled":True,
                        "ok":False,"stage":"protection_trailing_reconcile_pending",
                        "client_order_id":client_order_id,"order_id":order_id,
                        "executed_qty":executed_qty,"avg_price":avg_price,
                        "sl_protected":True,
                        "protection":resume_state.get("protection") or {"sl":sl_verified,"tp":tp_verified},
                        "error":str(pending),
                    }
                except Exception as trailing_exc:
                    if journal is not None:
                        return {
                            "processed":False,"entry_accepted":True,"entry_filled":True,
                            "ok":False,"stage":"protection_trailing_reconcile_pending",
                            "client_order_id":client_order_id,"order_id":order_id,
                            "executed_qty":executed_qty,"avg_price":avg_price,
                            "sl_protected":True,
                            "protection":resume_state.get("protection") or {"sl":sl_verified,"tp":tp_verified},
                            "error":f"trailing WAL reconciliation failed: {trailing_exc}",
                        }
                    try:
                        matched=self.client.find_matching_trailing(
                            intent.symbol,side,trail_qty,activation_price,callback_rate,
                            **({"position_id":position_id} if position_id else {}),
                        )
                        if matched is not None:
                            trailing_verified=self._verify_protection(
                                intent.symbol,matched,"trailing-stop",position_id
                            )
                    except Exception:
                        trailing_verified=None
                    if trailing_verified is None:
                        return {
                            "processed":False,
                            "entry_accepted":True,
                            "entry_filled":True,
                            "ok":False,
                            "stage":"protection_trailing_failed_sl_active",
                            "client_order_id":client_order_id,
                            "order_id":order_id,
                            "avg_price":avg_price,
                            "executed_qty":executed_qty,
                            "tp":tp,
                            "sl":sl,
                            "sl_protected":True,
                            "protection":{"sl":sl_verified,"tp":tp_verified,"trailing":None},
                            "error":f"trailing placement/verification failed; full SL remains active: {trailing_exc}",
                        }

            trailing_status=str(trailing_verified.get("status") or "").upper()
            if tp_status=="PARTIALLY_FILLED" or trailing_status in {"PARTIALLY_FILLED","FILLED"} or tp_status=="FILLED":
                return {
                    "processed":False,
                    "entry_accepted":True,
                    "entry_filled":True,
                    "ok":False,
                    "stage":"partial_exit_reconcile_required",
                    "client_order_id":client_order_id,
                    "order_id":order_id,
                    "avg_price":avg_price,
                    "executed_qty":executed_qty,
                    "tp":tp,
                    "sl":sl,
                    "sl_protected":True,
                    "protection":{"sl":sl_verified,"tp":tp_verified,"trailing":trailing_verified},
                    "error":"partial exit filled during setup; reconcile remaining position and reduce SL quantity",
                }

        protection={"tp":tp_verified,"sl":sl_verified}
        if split_trailing:
            protection["trailing"]=trailing_verified
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
            "exit_mode":exit_mode,
            "split_fallback_reason":split_fallback_reason,
            "actual_position_qty":actual_position_qty,
            "position_id":position_id,
            "quantity_precision":qty_precision,
            "protection_mode":"half_hard_tp_half_trailing_full_sl" if split_trailing else "separate_hard_tp_sl",
            "tp_qty":tp_qty,
            "trailing_qty":trail_qty,
            "trailing_activation_price":activation_price if split_trailing else None,
            "trailing_activation_pct":self.settings.tp_trailing_activation_pct if split_trailing else None,
            "trailing_callback_rate":self.settings.tp_trailing_callback_rate if split_trailing else None,
            "entry_result":entry_result,
            "protection":protection,
            "error":None,
        }

    def execute_safe(
        self,
        intent:TradeIntent,
        resume_state:dict|None=None,
        allow_new_entry:bool=True,
        protection_persist=None,
    )->dict:
        selected_mode=str((resume_state or {}).get("exit_mode") or (
            "split_trailing" if self.settings.tp_trailing_split_enabled else "hard_tp"
        ))
        try:
            result=self.execute(
                intent,
                resume_state=resume_state,
                allow_new_entry=allow_new_entry,
                protection_persist=protection_persist,
            )
            result.setdefault("exit_mode",selected_mode)
            return result
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
                "exit_mode":selected_mode,
                "client_order_id":self.client_order_id(intent.event_id,"entry"),
                "error":str(exc),
            }
