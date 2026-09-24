from __future__ import annotations

import logging
import time

from ..contracts import TradeIntent
from ..strategy.final14_config import case_name,get_case
from .executor import LegacyStyleBingXClient

log=logging.getLogger(__name__)

FINAL14_NOTIONAL_USDT=100.0
FINAL14_EXECUTION_LEVERAGE=50


class Final14Executor:
    """Execute one locked FINAL14 TradeIntent on BingX.

    MARKET entry -> confirm fill -> reject/close invalid fill -> place full hard
    TP and full hard SL. No trailing and no partial exit.
    """

    def __init__(self,settings,request_guard=None):
        self.client=LegacyStyleBingXClient(settings,request_guard=request_guard)

    @staticmethod
    def _valid_fill(side:str,avg_price:float,tp:float,sl:float)->bool:
        if side.upper()=="BUY":
            return sl<avg_price<tp
        return tp<avg_price<sl

    def execute(self,intent:TradeIntent)->dict:
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

        entry_result=self.client.place_market_entry(
            intent.symbol,side,qty,FINAL14_EXECUTION_LEVERAGE
        )
        order=self.client.extract_order(entry_result)
        order_id=order.get("orderId") or order.get("orderID")
        if not order_id:
            raise RuntimeError("BingX entry did not return orderId")

        executed_qty=0.0
        avg_price=0.0
        status=""
        try:
            for attempt in range(1,11):
                raw=self.client.get_order_detail(intent.symbol,str(order_id))
                detail=self.client.extract_order(raw)
                executed_qty=float(detail.get("executedQty") or 0)
                avg_price=float(detail.get("avgPrice") or 0)
                status=str(detail.get("status") or "")
                log.info(
                    "ENTRY_FILL_CHECK symbol=%s order_id=%s attempt=%s status=%s qty=%s avg=%s",
                    intent.symbol,order_id,attempt,status,executed_qty,avg_price,
                )
                if executed_qty>0 and avg_price>0:
                    break
                time.sleep(1.5)

            if executed_qty<=0 or avg_price<=0:
                return {
                    "processed":True,
                    "entry_accepted":True,
                    "entry_filled":False,
                    "ok":False,
                    "stage":"entry_fill_check",
                    "order_id":str(order_id),
                    "status":status,
                    "error":"entry accepted but fill not confirmed",
                }

            if not self._valid_fill(side,avg_price,tp,sl):
                close_result=self.client.close_market(
                    intent.symbol,side,executed_qty,FINAL14_EXECUTION_LEVERAGE
                )
                return {
                    "processed":True,
                    "entry_accepted":True,
                    "entry_filled":True,
                    "ok":False,
                    "stage":"fill_outside_final14_range_closed",
                    "order_id":str(order_id),
                    "avg_price":avg_price,
                    "executed_qty":executed_qty,
                    "tp":tp,
                    "sl":sl,
                    "close_result":close_result,
                    "error":"actual fill outside FINAL14 TP/SL envelope",
                }

            # Safety invariant: once entry is filled, never report completion
            # unless a hard SL has been accepted. Place SL first so a TP failure
            # cannot leave the position naked.
            try:
                sl_result=self.client.place_stop_loss(
                    intent.symbol,side,executed_qty,sl
                )
            except Exception as sl_exc:
                log.exception(
                    "PROTECTION_SL_FAILED symbol=%s order_id=%s; emergency closing",
                    intent.symbol,order_id,
                )
                try:
                    close_result=self.client.close_market(
                        intent.symbol,side,executed_qty,FINAL14_EXECUTION_LEVERAGE
                    )
                    return {
                        "processed":True,
                        "entry_accepted":True,
                        "entry_filled":True,
                        "ok":False,
                        "stage":"protection_sl_failed_closed",
                        "order_id":str(order_id),
                        "avg_price":avg_price,
                        "executed_qty":executed_qty,
                        "tp":tp,
                        "sl":sl,
                        "emergency_closed":True,
                        "close_result":close_result,
                        "error":f"stop-loss placement failed; position emergency-closed: {sl_exc}",
                    }
                except Exception as close_exc:
                    log.exception(
                        "PROTECTION_SL_FAILED_CLOSE_FAILED symbol=%s order_id=%s",
                        intent.symbol,order_id,
                    )
                    return {
                        "processed":True,
                        "entry_accepted":True,
                        "entry_filled":True,
                        "ok":False,
                        "stage":"unsafe_open_position",
                        "order_id":str(order_id),
                        "avg_price":avg_price,
                        "executed_qty":executed_qty,
                        "tp":tp,
                        "sl":sl,
                        "unsafe_open_position":True,
                        "error":(
                            f"stop-loss placement failed ({sl_exc}); "
                            f"emergency close also failed ({close_exc})"
                        ),
                    }

            try:
                tp_result=self.client.place_take_profit(
                    intent.symbol,side,executed_qty,tp
                )
            except Exception as tp_exc:
                log.exception(
                    "PROTECTION_TP_FAILED_SL_ACTIVE symbol=%s order_id=%s",
                    intent.symbol,order_id,
                )
                return {
                    "processed":True,
                    "entry_accepted":True,
                    "entry_filled":True,
                    "ok":False,
                    "stage":"protection_tp_failed_sl_active",
                    "order_id":str(order_id),
                    "avg_price":avg_price,
                    "executed_qty":executed_qty,
                    "target_notional":FINAL14_NOTIONAL_USDT,
                    "execution_leverage":FINAL14_EXECUTION_LEVERAGE,
                    "tp":tp,
                    "sl":sl,
                    "sl_protected":True,
                    "protection":{"sl":sl_result,"tp":None},
                    "error":f"take-profit placement failed; hard stop-loss remains active: {tp_exc}",
                }

            protection={"tp":tp_result,"sl":sl_result}
            return {
                "processed":True,
                "entry_accepted":True,
                "entry_filled":True,
                "ok":True,
                "stage":"complete",
                "order_id":str(order_id),
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
        except Exception as exc:
            exc.accepted_order_id=str(order_id)
            raise

    def execute_safe(self,intent:TradeIntent)->dict:
        try:
            return self.execute(intent)
        except Exception as exc:
            accepted_order_id=str(getattr(exc,"accepted_order_id","") or "")
            log.exception(
                "FINAL14_EXEC_FAILED event_id=%s accepted_order_id=%s",
                intent.event_id,accepted_order_id or None,
            )
            return {
                "ok":False,
                "processed":bool(accepted_order_id),
                "entry_accepted":bool(accepted_order_id),
                "entry_filled":False,
                "stage":"post_entry_error" if accepted_order_id else "pre_entry_error",
                "order_id":accepted_order_id,
                "error":str(exc),
            }
