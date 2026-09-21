from __future__ import annotations

import hashlib
import json
import logging
import math
import time

from .executor import Executor
from .final14_config import case_name, get_case
from .strategy import Signal
from .final14_policy import validate_signal, NOTIONAL, LEVERAGE
from .final14_positions import case_id

log = logging.getLogger(__name__)

FINAL14_NOTIONAL_USDT = NOTIONAL
FINAL14_EXECUTION_LEVERAGE = LEVERAGE


class Final14Executor(Executor):
    """FINAL14 execution: one full-size MARKET entry with attached hard TP/SL.

    Strategy TP/SL are computed from the closed-candle entry used by backtest.
    BingX SEPARATE_ISOLATED is required so each combo gets an independent
    positionId even when several combos open the same symbol/direction.
    """

    def _positions(self, symbol: str) -> list[dict]:
        body = self._signed_trade_request('GET','/openApi/swap/v2/user/positions',{'symbol':symbol})
        if not isinstance(body,dict) or body.get('code') not in (0,'0') or 'data' not in body:
            raise RuntimeError('Invalid positions response; cannot infer flat')
        rows = body['data']
        if isinstance(rows,dict):
            rows = rows.get('positions', rows.get('data'))
        if not isinstance(rows,list):
            raise RuntimeError('Invalid positions data; cannot infer flat')
        for row in rows:
            if not isinstance(row,dict) or 'positionAmt' not in row:
                raise RuntimeError('Invalid position row')
            amount = float(row['positionAmt'])
            if not math.isfinite(amount) or (amount != 0 and not self.position_id(row)):
                raise RuntimeError('Invalid position identity or quantity')
        return rows

    def _margin_type(self, symbol: str) -> str:
        body = self._signed_trade_request(
            "GET",
            "/openApi/swap/v2/trade/marginType",
            {"symbol": symbol},
        )
        data = body.get("data") or {}
        if isinstance(data, dict):
            return str(data.get("marginType") or "").upper()
        return ""

    def _assert_separate_isolated(self, symbol: str) -> None:
        margin_type = self._margin_type(symbol)
        if margin_type != "SEPARATE_ISOLATED":
            raise RuntimeError(
                f"{symbol} marginType={margin_type or 'UNKNOWN'}; "
                "FINAL14 requires SEPARATE_ISOLATED for independent combo positions"
            )

    def _prepare_final14_size(self, signal: Signal) -> dict:
        rules = self._contract_rules(signal.symbol)
        if rules["status"] != 1 or rules["api_state_open"] == "false":
            raise RuntimeError(f"{signal.symbol} is not open for API entries")

        max_lev = (
            rules["max_long_leverage"]
            if signal.side.upper() == "BUY"
            else rules["max_short_leverage"]
        )
        if max_lev > 0 and FINAL14_EXECUTION_LEVERAGE > max_lev:
            raise RuntimeError(
                f"{signal.symbol} max leverage is {max_lev}x, FINAL14 requires {FINAL14_EXECUTION_LEVERAGE}x"
            )

        target_notional = FINAL14_NOTIONAL_USDT
        raw_qty = target_notional / float(signal.entry)
        qty = self._floor_precision(raw_qty, rules["quantity_precision"])
        if qty <= 0:
            raise ValueError("FINAL14 sizing produced zero quantity")

        actual_notional = qty * float(signal.entry)
        if rules["min_qty"] > 0 and qty < rules["min_qty"]:
            raise ValueError(
                f"quantity {qty} below BingX minimum {rules['min_qty']}"
            )
        if rules["min_usdt"] > 0 and actual_notional < rules["min_usdt"]:
            raise ValueError(
                f"notional {actual_notional:.8f} below BingX minimum {rules['min_usdt']}"
            )
        return {
            **rules,
            "qty": qty,
            "target_notional": target_notional,
            "actual_notional": actual_notional,
        }

    @staticmethod
    def _position_ids(rows: list[dict]) -> set[str]:
        out=set()
        for row in rows:
            try:
                amt=abs(float(row.get("positionAmt") or 0))
            except Exception:
                amt=0.0
            pid=str(row.get("positionId") or "")
            if pid and amt>0:
                out.add(pid)
        return out

    def _resolve_new_position_id(
        self,
        symbol: str,
        side: str,
        before_ids: set[str],
        avg_price: float,
        executed_qty: float,
    ) -> str | None:
        wanted_side="LONG" if side.upper()=="BUY" else "SHORT"
        for _ in range(12):
            rows=self._positions(symbol)
            candidates=[]
            for row in rows:
                pid=str(row.get("positionId") or "")
                if not pid or pid in before_ids:
                    continue
                if str(row.get("positionSide") or "").upper()!=wanted_side:
                    continue
                try:
                    amt=abs(float(row.get("positionAmt") or 0))
                    px=float(row.get("avgPrice") or 0)
                except Exception:
                    continue
                if amt<=0:
                    continue
                qty_err=abs(amt-executed_qty)
                px_err=abs(px-avg_price)/avg_price if avg_price>0 else 999
                candidates.append((qty_err,px_err,pid))
            exact = [c for c in candidates if c[0] <= max(1e-10, executed_qty*1e-8) and c[1] <= 1e-8]
            if len(exact) == 1:
                return exact[0][2]
            time.sleep(0.5)
        return None

    def build_payload(self, signal: Signal, usdt_amount: float) -> dict:
        cfg=get_case(signal.symbol,signal.combo)
        return {
            "signal_id":signal.event_id,
            "combo":case_name(signal.combo),
            "side":signal.side,
            "entry":float(signal.entry),
            "tp":float(signal.tp),
            "sl":float(signal.sl),
            "rr":float(cfg["rr"]) if cfg else None,
            "symbol":signal.symbol,
            "timeframe":signal.timeframe,
            "order_type":"MARKET",
            "usdt_amount":usdt_amount,
            "source":"bingx-final14-hardtp",
            "smc_dir":signal.smc_dir,
            "signal_close_time":signal.close_time,
            "exit_mode":"100pct_hard_tp_sl",
        }

    def build_execution_discord_message(self, signal: Signal, result: dict) -> str:
        entry=self._fmt_discord_number(result.get("avg_price"))
        tp=self._fmt_discord_number(result.get("tp"))
        sl=self._fmt_discord_number(result.get("sl"))
        return (
            "✅ FINAL14 đặt lệnh\n"
            f"{signal.symbol} {signal.side}\n\n"
            f"📊 {case_name(signal.combo)}\n"
            f"Entry fill: {entry}\n"
            f"TP 100%: {tp}\n"
            f"SL 100%: {sl}\n"
            f"RR: {result.get('rr')}\n"
            f"PositionId: {result.get('position_id') or 'resolving'}"
        )

    def _execute_direct_bingx(self, signal: Signal, progress=None) -> dict:
        validate_signal(signal)
        progress = progress or (lambda **fields: None)
        cfg=get_case(signal.symbol,signal.combo)
        if not cfg:
            raise RuntimeError(
                f"FINAL14 disabled case: {signal.symbol} {case_name(signal.combo)}"
            )

        self._assert_hedge_mode()
        self._assert_separate_isolated(signal.symbol)
        sizing=self._prepare_final14_size(signal)
        qty=float(sizing["qty"])
        price_precision=int(sizing["price_precision"])

        # Exact strategy levels are based on the closed-candle entry, matching
        # backtest. Only exchange tick-size rounding is applied here.
        tp=round(float(signal.tp),price_precision)
        sl=round(float(signal.sl),price_precision)
        entry=float(signal.entry)
        if signal.side=="BUY" and not (sl<entry<tp):
            raise ValueError("FINAL14 BUY TP/SL ordering invalid")
        if signal.side=="SELL" and not (tp<entry<sl):
            raise ValueError("FINAL14 SELL TP/SL ordering invalid")

        self._set_leverage(signal.symbol,signal.side,FINAL14_EXECUTION_LEVERAGE)
        before=self._positions(signal.symbol)
        before_ids=self._position_ids(before)

        client_order_id=self.client_order_id(signal.event_id)
        params={
            "symbol":signal.symbol,
            "side":signal.side.upper(),
            "positionSide":"LONG" if signal.side=="BUY" else "SHORT",
            "type":"MARKET",
            "quantity":f"{qty:.8f}".rstrip("0").rstrip("."),
            "clientOrderId":client_order_id,
            # CONTRACT_PRICE matches candle OHLC trigger semantics used by backtest.
            "stopLoss":json.dumps({
                "type":"STOP_MARKET",
                "stopPrice":sl,
                "workingType":"CONTRACT_PRICE",
                "stopGuaranteed":False,
            },separators=(",",":")),
            "takeProfit":json.dumps({
                "type":"TAKE_PROFIT_MARKET",
                "stopPrice":tp,
                "workingType":"CONTRACT_PRICE",
                "stopGuaranteed":False,
            },separators=(",",":")),
        }
        progress(status="submitting", tp=tp, sl=sl, qty=qty)
        entry_result=self._signed_trade_request(
            "POST","/openApi/swap/v2/trade/order",params
        )
        order=self._extract_order(entry_result)
        order_id=order.get("orderID") or order.get("orderId")
        if not order_id:
            raise RuntimeError("FINAL14 entry did not return orderId")

        progress(status="accepted", entry_order_id=str(order_id))
        executed_qty=0.0
        avg_price=0.0
        status=""
        order_detail={}
        for _ in range(10):
            raw=self._order_detail(signal.symbol,str(order_id))
            order_detail=self._extract_order(raw)
            executed_qty=float(order_detail.get("executedQty") or 0)
            avg_price=float(order_detail.get("avgPrice") or 0)
            status=str(order_detail.get("status") or "")
            if executed_qty>0 and avg_price>0 and status.upper()=="FILLED":
                break
            time.sleep(1.0)

        if executed_qty<=0 or avg_price<=0 or status.upper()!="FILLED":
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

        position_id=(
            self.position_id(order_detail) or self.position_id(order)
            or self._resolve_new_position_id(
                signal.symbol,signal.side,before_ids,avg_price,executed_qty
            )
        )

        verified = self.protection_matches(order_detail, {"tp":tp,"sl":sl,"qty":executed_qty})
        progress(status="active" if verified and position_id else "protection_unverified",
                 position_id=position_id, qty=executed_qty, avg_price=avg_price,
                 protection_verified=verified)
        result={
            "processed":True,
            "entry_accepted":True,
            "entry_filled":True,
            "ok":bool(verified and position_id),
            "stage":"complete" if verified and position_id else "protection_or_position_pending",
            "order_id":str(order_id),
            "position_id":position_id,
            "avg_price":avg_price,
            "executed_qty":executed_qty,
            "target_notional":float(sizing["target_notional"]),
            "actual_notional":float(sizing["actual_notional"]),
            "execution_leverage":FINAL14_EXECUTION_LEVERAGE,
            "tp":tp,
            "sl":sl,
            "rr":float(cfg["rr"]),
            "tp_pct":float(cfg["tp_pct"]),
            "sl_pct":float(cfg["sl_pct"]),
            "protection_mode":"attached_hard_tp_sl",
            "working_type":"CONTRACT_PRICE",
            "entry_result":entry_result,
            "error":None if verified and position_id else "Protection or position identity not yet verified",
        }
        log.info(
            "FINAL14_ENTRY symbol=%s combo=%s side=%s order_id=%s position_id=%s "
            "signal_entry=%s avg_fill=%s qty=%s tp=%s sl=%s rr=%s",
            signal.symbol,case_name(signal.combo),signal.side,order_id,position_id,
            entry,avg_price,executed_qty,tp,sl,cfg["rr"],
        )
        return result


    @staticmethod
    def position_id(row):
        value = str(row.get("positionId") or row.get("positionID") or "")
        return "" if value == "0" else value

    @staticmethod
    def client_order_id(event_id):
        return "f14" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:28]

    def query_entry(self, rec):
        if rec.get('entry_order_id'):
            return self._extract_order(self._order_detail(rec['symbol'],rec['entry_order_id']))
        return self._extract_order(self._signed_trade_request(
            'GET','/openApi/swap/v2/trade/order',
            {'symbol':rec['symbol'],'clientOrderId':rec['client_order_id']}))

    @staticmethod
    def protection_matches(detail, rec):
        for name,typ,level in [('takeProfit','TAKE_PROFIT_MARKET','tp'),('stopLoss','STOP_MARKET','sl')]:
            obj = detail.get(name)
            if isinstance(obj,str):
                try: obj=json.loads(obj)
                except (ValueError,TypeError): return False
            if not isinstance(obj,dict): return False
            try:
                if str(obj.get('type')).upper()!=typ: return False
                if str(obj.get('workingType')).upper()!='CONTRACT_PRICE': return False
                price=float(obj.get('stopPrice') or 0)
                if not math.isfinite(price) or abs(price-float(rec[level])) > max(1e-9,abs(float(rec[level]))*1e-10): return False
                # Absent/zero quantity on attached TP/SL means parent quantity.
                amount=float(obj.get('quantity') or 0)
                if amount>0 and amount+1e-12<float(rec.get('qty') or 0): return False
            except (ValueError,TypeError,KeyError): return False
        return True

    def send_case(self, state, signal, target_name):
        """The only production entry path: journal before network, never blind retry."""
        if self.settings.dry_run:
            return {'ok':False,'processed':False,'stage':'policy','error':'dry_run'}
        rec={'case_id':case_id(signal.symbol,signal.combo), 'event_id':signal.event_id,
             'symbol':signal.symbol,'combo':signal.combo,'side':signal.side,
             'entry_close_time':signal.close_time,'signal_entry':signal.entry,
             'tp':signal.tp,'sl':signal.sl,'client_order_id':self.client_order_id(signal.event_id)}
        if not state.claim_final14(rec):
            return {'ok':False,'processed':True,'stage':'duplicate_or_active_case',
                    'target':target_name,'error':'Event already claimed or case active'}
        submitted=False
        def progress(**fields):
            nonlocal submitted
            state.update_final14(signal.event_id,fields)
            if fields.get('status')=='submitting': submitted=True
        try:
            result=self._execute_direct_bingx(signal,progress=progress)
            result['target']=target_name
            return result
        except Exception as exc:
            # A timeout after POST may hide a successful exchange submission.
            # Keep the slot pending and query by the deterministic client ID.
            try:
                state.update_final14(signal.event_id,{'status':'unknown' if submitted else 'rejected',
                                                      'error':str(exc)})
            except Exception:
                log.exception('FINAL14_JOURNAL_UPDATE_FAILED event=%s',signal.event_id)
            return {'ok':False,'processed':True,'entry_accepted':None if submitted else False,
                    'entry_filled':False,'stage':'reconcile_pending' if submitted else 'pre_entry',
                    'target':target_name,'error':str(exc)}
