from __future__ import annotations

import logging
import time

from ..strategy.final14_config import case_name,get_case
from ..strategy.strategy import Signal
from .executor import LegacyStyleBingXClient

log=logging.getLogger(__name__)

FINAL14_NOTIONAL_USDT=100.0
FINAL14_EXECUTION_LEVERAGE=50


class Final14Executor:
    """FINAL14 levels + original autoTrade execution style.

    - MARKET entry
    - poll order detail for executedQty/avgPrice
    - reject/close if actual fill is outside FINAL14 TP/SL envelope
    - 100% hard TP and 100% hard SL
    - no trailing, no partial exit
    """

    def __init__(self,settings,request_guard=None):
        self.settings=settings
        self.client=LegacyStyleBingXClient(settings,request_guard=request_guard)

    @staticmethod
    def _valid_fill(side:str,avg_price:float,tp:float,sl:float)->bool:
        if side.upper()=="BUY":
            return sl<avg_price<tp
        return tp<avg_price<sl

    def execute(self,signal:Signal)->dict:
        cfg=get_case(signal.symbol,signal.combo)
        if not cfg:
            raise RuntimeError(
                f"FINAL14 disabled case: {signal.symbol} {case_name(signal.combo)}"
            )

        side=signal.side.upper()
        entry=float(signal.entry)
        tp=float(signal.tp)
        sl=float(signal.sl)
        if side=="BUY" and not (sl<entry<tp):
            raise ValueError("FINAL14 BUY TP/SL ordering invalid")
        if side=="SELL" and not (tp<entry<sl):
            raise ValueError("FINAL14 SELL TP/SL ordering invalid")

        # Same sizing concept as the old project: notional / signal entry.
        qty=round(FINAL14_NOTIONAL_USDT/entry,4)
        if qty<=0:
            raise ValueError("FINAL14 quantity rounded to zero")

        entry_result=self.client.place_market_entry(
            signal.symbol,side,qty,FINAL14_EXECUTION_LEVERAGE
        )
        order=self.client.extract_order(entry_result)
        order_id=order.get("orderId") or order.get("orderID")
        if not order_id:
            raise RuntimeError("BingX entry did not return orderId")

        executed_qty=0.0
        avg_price=0.0
        status=""
        detail={}
        try:
            for attempt in range(1,11):
                raw=self.client.get_order_detail(signal.symbol,str(order_id))
                detail=self.client.extract_order(raw)
                executed_qty=float(detail.get("executedQty") or 0)
                avg_price=float(detail.get("avgPrice") or 0)
                status=str(detail.get("status") or "")
                log.info(
                    "ENTRY_FILL_CHECK symbol=%s order_id=%s attempt=%s status=%s qty=%s avg=%s",
                    signal.symbol,order_id,attempt,status,executed_qty,avg_price,
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
                    signal.symbol,side,executed_qty,FINAL14_EXECUTION_LEVERAGE
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

            protection=self.client.place_protection(
                signal.symbol,side,executed_qty,tp,sl
            )
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
            # Entry was already accepted. Caller must dedupe this event even if
            # a later fill/protection check fails.
            exc.accepted_order_id=str(order_id)
            raise

    def send_target(self,signal:Signal,target_name:str,url:str,usdt_amount:float)->dict:
        if self.settings.dry_run:
            return {
                "target":target_name,
                "ok":False,
                "processed":False,
                "stage":"dry_run",
                "error":"dry_run",
            }
        try:
            result=self.execute(signal)
            result["target"]=target_name
            return result
        except Exception as exc:
            accepted_order_id=str(getattr(exc,"accepted_order_id","") or "")
            log.exception(
                "FINAL14_EXEC_FAILED signal_id=%s accepted_order_id=%s",
                signal.event_id,accepted_order_id or None,
            )
            return {
                "target":target_name,
                "ok":False,
                "processed":bool(accepted_order_id),
                "entry_accepted":bool(accepted_order_id),
                "entry_filled":False,
                "stage":"post_entry_error" if accepted_order_id else "pre_entry_error",
                "order_id":accepted_order_id,
                "error":str(exc),
            }
