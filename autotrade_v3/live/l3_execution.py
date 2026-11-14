"""L3 EXECUTION: Entry + TP/SL/trailing for ONE signal, independently.

Flow per signal (Owner decision, Issue #107/#108):
  1. claim the event (client_order_id UNIQUE) - the only dedupe;
  2. POST the MARKET Entry with clientOrderId derived from event_id;
  3. L4 mechanism 1 records evidence at send time (background, never blocks);
  4. L4 mechanism 2 confirms the exact Entry (orderId/clientOrderId);
     a lost POST response is verified the same way and NEVER resent;
  5. store the real fill; derive TP/SL from the real fill with the combo's
     locked tp_pct/sl_pct; SL on the full fill, then 50% TP + 50% trailing
     (or 100% TP when BingX minimums do not allow a split);
  6. verify each protection through L4 mechanism 2;
  7. success -> L5 symbol webhook; every failure -> control_logs + ERROR.

There is no cross-combo gate: no reading of other combos, positions or old
orders, no symbol/side lock, no recovery/reconciliation precondition.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from autotrade_v2.app.execution.quantity import allocate_exit_qty
from autotrade_v2.app.strategy.final14_config import get_case
from autotrade_v2.app.strategy.final14_exact_strategy import hard_tp_sl

from .l4_control import (ACTIVE, FILLED, PARTIALLY_FILLED, TERMINAL_UNFILLED, ControlLog, L4Reader, OrderEvidence,
                         extract_order, order_id_of)
from .sanitize import clean, clean_text
from .store import CLAIMED, EXISTS
from .transport import BingXApiError

log = logging.getLogger("autotrade_v3.l3")

PREFIX = {"entry": "v3e", "sl": "v3s", "tp": "v3t", "trailing": "v3r", "close": "v3c"}
TYPES = {"sl": "STOP_MARKET", "tp": "TAKE_PROFIT_MARKET", "trailing": "TRAILING_STOP_MARKET"}
LEG_NAMES = {"sl": "SL", "tp": "TP", "trailing": "Trailing", "close": "Force Close"}


@dataclass(frozen=True)
class V3Signal:
    event_id: str
    symbol: str
    combo: int
    combo_name: str
    side: str
    timeframe: str
    close_time: int
    entry: float
    tp: float
    sl: float


def client_order_id(event_id: str, purpose: str) -> str:
    digest = hashlib.sha256(f"v3|{purpose}|{event_id}".encode()).hexdigest()[:30]
    return f"{PREFIX[purpose]}-{digest}"


def _dec(value) -> Decimal | None:
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass
class LegResult:
    kind: str
    status: str             # confirmed | rejected | unknown
    order_id: str | None = None
    exchange_status: str | None = None
    error: str | None = None
    error_code: str | None = None
    evidence: dict | None = None


class L3Executor:
    def __init__(self, settings, client, store, reader: L4Reader, control: ControlLog, alert=None,
                 *, protection_confirm_attempts: int = 3, protection_confirm_sleep_sec: float = 1.0,
                 late_verify_attempts: int = 20, late_verify_sleep_sec: float = 30.0):
        self.settings = settings
        self.client = client
        self.store = store
        self.reader = reader
        self.control = control
        self.alert = alert
        self.protection_confirm_attempts = protection_confirm_attempts
        self.protection_confirm_sleep_sec = protection_confirm_sleep_sec
        self.late_verify_attempts = late_verify_attempts
        self.late_verify_sleep_sec = late_verify_sleep_sec
        self._claimed: set[str] = set()
        self._pending_claims: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.results: list[dict] = []
        self.on_settled = None   # optional callback after an Entry attempt finished; never blocks or gates trading

    # ------------------------------------------------------------------
    def submit(self, sig: V3Signal) -> threading.Thread:
        """Run one signal on its own thread: signals never wait for each other."""
        t = threading.Thread(target=self.execute_safe, args=(sig,), name=f"v3-l3-{sig.symbol}-{sig.combo_name}",
                             daemon=True)
        t.start()
        return t

    def execute_safe(self, sig: V3Signal) -> dict:
        try:
            result = self.execute(sig)
        except Exception as exc:  # last line of defence: report, never crash the pipeline
            log.exception("V3_L3_UNEXPECTED event_id=%s", sig.event_id)
            self._report(sig, "unexpected", f"Lỗi nội bộ L3 không lường trước: {type(exc).__name__}: {exc}")
            result = {"event_id": sig.event_id, "stage": "internal_error", "error": clean_text(exc)}
        self.results.append(clean(result))
        del self.results[:-200]
        if self.on_settled is not None:
            try:
                self.on_settled("entry")
            except Exception as exc:
                log.error("V3_L3_ON_SETTLED_FAILED error_class=%s", type(exc).__name__)
        return result

    # ------------------------------------------------------------------
    def _report(self, sig: V3Signal, step: str, message: str, *, order_id=None, code=None, details=None) -> None:
        self.control.report("L3", step, message, symbol=sig.symbol, event_id=sig.event_id, order_id=order_id,
                            error_code=code, details=details)

    def _save(self, sig: V3Signal, fields: dict) -> None:
        pending = self._pending_claims.get(sig.event_id)
        if pending is not None:
            try:  # the row could not be created before the POST; create it now so updates land
                self.store.claim_execution(pending)
                self._pending_claims.pop(sig.event_id, None)
            except Exception:
                pass
        try:
            self.store.update_execution(sig.event_id, fields)
        except Exception as exc:
            self._report(sig, "db_update_execution", f"Không ghi được execution_orders ({fields.get('stage')}): {exc}")

    def execute(self, sig: V3Signal) -> dict:
        side = sig.side.upper()
        coid = client_order_id(sig.event_id, "entry")
        cfg = get_case(sig.symbol, sig.combo)
        qty = round(float(self.settings.notional_usdt) / float(sig.entry), 4) if sig.entry else 0.0
        out: dict[str, Any] = {"event_id": sig.event_id, "symbol": sig.symbol, "combo": sig.combo_name, "side": side,
                               "client_order_id": coid, "leverage": self.settings.leverage, "requested_qty": qty}

        # 1. claim (only dedupe) ------------------------------------------
        with self._lock:
            if coid in self._claimed:
                self._report(sig, "dedupe", "Sự kiện đã được xử lý trong tiến trình này; không gửi Entry lần 2",
                             code="DUPLICATE_EVENT", details={"client_order_id": coid})
                return {**out, "stage": "duplicate_skipped"}
            self._claimed.add(coid)
        try:
            claim = self.store.claim_execution({**out, "event_id": sig.event_id})
        except Exception as exc:
            claim = CLAIMED  # DB failure must not block trading; in-process claim still applies
            self._pending_claims[sig.event_id] = {**out, "event_id": sig.event_id}
            self._report(sig, "db_claim", f"Không ghi được execution_orders trước khi gửi Entry (vẫn gửi Entry): {exc}")
        if claim == EXISTS:
            self._report(sig, "dedupe", "execution_orders đã có event/clientOrderId này; không gửi Entry lần 2",
                         code="DUPLICATE_EVENT", details={"client_order_id": coid})
            return {**out, "stage": "duplicate_skipped"}
        if cfg is None or side not in {"BUY", "SELL"} or qty <= 0:
            self._report(sig, "entry_params", f"Thông số Entry không hợp lệ: cfg={'ok' if cfg else 'missing'} side={side} qty={qty}",
                         code="INVALID_SIGNAL_PARAMS")
            self._save(sig, {"stage": "entry_params_invalid", "error": "invalid params"})
            return {**out, "stage": "entry_params_invalid"}

        # 2. POST Entry ------------------------------------------------------
        entry_order_id = None
        post_outcome: dict[str, Any]
        try:
            payload = self.client.place_market_entry(sig.symbol, side, qty, self.settings.leverage, client_order_id=coid)
            order = extract_order(payload)
            entry_order_id = order_id_of(order) or None
            post_outcome = {"result": "accepted", "order": order}
        except BingXApiError as exc:
            post_outcome = {"result": "rejected", "code": exc.code, "message": exc.message}
        except Exception as exc:
            post_outcome = {"result": "response_lost", "error": f"{type(exc).__name__}: {clean_text(exc, 500)}"}

        # 3. L4 mechanism 1 (evidence only, background) -----------------------
        try:
            self.reader.evidence_at_send(symbol=sig.symbol, event_id=sig.event_id, client_order_id=coid,
                                         post_outcome=post_outcome)
        except Exception as exc:
            self._report(sig, "entry_send_evidence", f"Không chạy được GET bằng chứng L4 cơ chế 1: {exc}")

        if post_outcome["result"] == "rejected":
            msg = f"BingX từ chối Entry: mã {post_outcome['code']} — {post_outcome['message']}"
            self._save(sig, {"stage": "entry_rejected", "entry_status": "REJECTED", "error": msg, "raw_entry": post_outcome})
            self._report(sig, "entry_post", msg, code=post_outcome["code"], details={"client_order_id": coid, "qty": qty})
            return {**out, "stage": "entry_rejected", "error": msg}

        # 4. L4 mechanism 2: confirm exact Entry --------------------------------
        ev = self.reader.confirm_entry(sig.symbol, event_id=sig.event_id, order_id=entry_order_id, client_order_id=coid)
        entry_order_id = ev.order_id or entry_order_id
        raw_entry = {"post": post_outcome, "confirm": ev.as_dict()}
        out.update(entry_order_id=entry_order_id, entry_status=ev.status)

        if not (ev.executed_qty > 0 and ev.avg_price > 0):
            if ev.status in TERMINAL_UNFILLED and ev.executed_qty <= 0:
                stage, msg = "entry_not_filled", f"Entry không khớp: BingX trạng thái {ev.status}"
            else:
                stage = "entry_unknown"
                lost = " (phản hồi POST bị mất)" if post_outcome["result"] == "response_lost" else ""
                msg = (f"Trạng thái Entry CHƯA XÁC ĐỊNH{lost}: BingX GET trả {ev.status}"
                       f"{' — ' + ev.error if ev.error else ''}. Không gửi lại Entry, không kết luận BingX đã từ chối.")
            self._save(sig, {"stage": stage, "entry_order_id": entry_order_id, "entry_status": ev.status,
                             "error": msg, "raw_entry": raw_entry})
            self._report(sig, "entry_confirm", msg, order_id=entry_order_id, code=ev.error_code,
                         details={"client_order_id": coid, "post": post_outcome, "evidence": ev.as_dict()})
            if stage == "entry_unknown":
                self._start_late_verify(sig, cfg, dict(out), entry_order_id, raw_entry)
            return {**out, "stage": stage, "error": msg}
        return self._protect_guarded(sig, cfg, ev, out, entry_order_id, raw_entry)

    # ------------------------------------------------------------------
    def _start_late_verify(self, sig, cfg, out, entry_order_id, raw_entry) -> None:
        """GET-only re-check of an UNKNOWN Entry. Never resends; protects it if it shows up filled."""
        if self.late_verify_attempts <= 0:
            return

        def run():
            coid = out["client_order_id"]
            for _ in range(self.late_verify_attempts):
                time.sleep(self.late_verify_sleep_sec)
                ev = self.reader.confirm_order(sig.symbol, order_id=entry_order_id, client_order_id=coid)
                if ev.executed_qty > 0 and ev.avg_price > 0:
                    self._report(sig, "entry_late_confirm",
                                 f"Entry trước đó CHƯA XÁC ĐỊNH nay BingX xác nhận đã khớp ({ev.executed_qty_raw} @ {ev.avg_price});"
                                 " đặt bảo vệ ngay", order_id=ev.order_id, code="LATE_FILL", details=ev.as_dict())
                    self.results.append(clean(self._protect_guarded(
                        sig, cfg, ev, {**out, "entry_order_id": ev.order_id, "entry_status": ev.status},
                        ev.order_id or entry_order_id, {**raw_entry, "late_confirm": ev.as_dict()})))
                    return
                if ev.status in TERMINAL_UNFILLED:
                    self._save(sig, {"stage": "entry_not_filled", "entry_status": ev.status})
                    return
        try:
            threading.Thread(target=run, name=f"v3-l3-late-{sig.combo_name}", daemon=True).start()
        except Exception as exc:
            self._report(sig, "entry_late_confirm", f"Không khởi động được kiểm tra lại Entry: {exc}")

    def _protect_guarded(self, sig, cfg, ev, out, entry_order_id, raw_entry) -> dict:
        state: dict[str, Any] = {"protection": {}, "sl_confirmed": False}
        try:
            return self._protect(sig, cfg, ev, out, entry_order_id, raw_entry, state)
        except Exception as exc:
            log.exception("V3_L3_PROTECT_UNEXPECTED event_id=%s", sig.event_id)
            self._report(sig, "protection_unexpected",
                         f"Entry ĐÃ KHỚP nhưng lỗi nội bộ khi đặt bảo vệ: {type(exc).__name__}: {exc}",
                         order_id=entry_order_id, code="PROTECTION_INTERNAL_ERROR", details=state["protection"])
            if not state["sl_confirmed"] and "sl" not in state["protection"]:
                side = sig.side.upper()
                state["protection"]["force_close"] = self._force_close(
                    sig, "SELL" if side == "BUY" else "BUY", "LONG" if side == "BUY" else "SHORT",
                    ev.executed_qty_raw, entry_order_id)
            self._save(sig, {"stage": "protection_internal_error", "protection_status": "failed",
                             "raw_protection": state["protection"], "error": clean_text(exc)})
            return {**out, "stage": "protection_internal_error", "error": clean_text(exc)}

    def _protect(self, sig, cfg, ev, out, entry_order_id, raw_entry, state) -> dict:
        side = sig.side.upper()

        avg = ev.avg_price
        filled_raw = ev.executed_qty_raw
        filled = ev.executed_qty
        filled_at = ev.order.get("updateTime") or ev.order.get("time")
        out.update(avg_price=avg, executed_qty=filled_raw)
        fields = {"stage": "entry_filled", "entry_order_id": entry_order_id, "entry_status": ev.status,
                  "executed_qty": filled_raw, "avg_price": avg, "raw_entry": raw_entry}
        if filled_at:
            from .store import ms_to_dt
            try:
                fields["filled_at"] = ms_to_dt(int(filled_at))
            except (TypeError, ValueError):
                pass
        self._save(sig, fields)
        if ev.status != FILLED:
            self._report(sig, "entry_confirm", f"Entry MARKET trạng thái {ev.status}, chỉ khớp ({filled_raw}); đặt bảo vệ theo khối lượng đã khớp",
                         order_id=entry_order_id, code="PARTIAL_FILL", details=ev.as_dict())

        # 5. protection from the real fill ---------------------------------------
        direction = 1 if side == "BUY" else -1
        tp_price, sl_price = hard_tp_sl(avg, direction, cfg)
        out.update(tp_price=tp_price, sl_price=sl_price, sl_qty=filled_raw)
        opposite = "SELL" if side == "BUY" else "BUY"
        position_side = "LONG" if side == "BUY" else "SHORT"
        protection: dict[str, Any] = state["protection"]

        sl = self._place_leg(sig, "sl", {"symbol": sig.symbol, "side": opposite, "positionSide": position_side,
                                         "type": TYPES["sl"], "stopPrice": self.client.fmt_price(sl_price),
                                         "quantity": filled_raw}, trigger=sl_price, qty=filled_raw)
        protection["sl"] = asdict(sl)
        state["sl_confirmed"] = sl.status == "confirmed"
        out.update(sl_order_id=sl.order_id)
        self._save(sig, {"stage": "sl_" + sl.status, "sl_order_id": sl.order_id, "sl_price": sl_price, "sl_qty": filled_raw,
                         "sl_status": sl.exchange_status or sl.status, "raw_protection": protection})

        if sl.status == "rejected":
            msg = (f"Entry ĐÃ KHỚP ({filled_raw} @ {avg}) nhưng đặt SL THẤT BẠI: BingX từ chối mã {sl.error_code} — {sl.error}."
                   " Thực hiện Force Close đúng khối lượng của lệnh này.")
            self._report(sig, "sl_place", msg, order_id=entry_order_id, code=sl.error_code, details={"sl": protection["sl"]})
            closed = self._force_close(sig, opposite, position_side, filled_raw, entry_order_id)
            protection["force_close"] = closed
            self._save(sig, {"stage": "sl_failed_force_closed" if closed.get("confirmed") else "sl_failed_unprotected",
                             "protection_status": "failed", "raw_protection": protection, "error": msg})
            return {**out, "stage": "sl_failed", "protection": protection}
        if sl.status == "unknown":
            self._report(sig, "sl_verify", f"Entry ĐÃ KHỚP ({filled_raw} @ {avg}) nhưng SL CHƯA XÁC NHẬN được trên BingX: {sl.error}",
                         order_id=sl.order_id or entry_order_id, code=sl.error_code, details={"sl": protection["sl"]})

        # split exactly like the current production mechanism
        exit_mode = "hard_tp"
        tp_qty, trail_qty = filled_raw, "0"
        if self.settings.tp_trailing_split_enabled:
            try:
                rules = self.client.get_contract_rules(sig.symbol)
                alloc = allocate_exit_qty(filled_raw, rules, min(avg, tp_price))
                exit_mode = alloc["mode"]
                tp_qty = alloc["tp_qty"]
                trail_qty = alloc["trailing_qty"]
            except Exception as exc:
                self._report(sig, "split_quantity", f"Không đọc được quy tắc khối lượng BingX để chia TP/trailing; đặt TP 100%: {exc}",
                             order_id=entry_order_id, code=getattr(exc, "code", None))
        out.update(exit_mode=exit_mode, tp_qty=tp_qty)

        tp = self._place_leg(sig, "tp", {"symbol": sig.symbol, "side": opposite, "positionSide": position_side,
                                         "type": TYPES["tp"], "stopPrice": self.client.fmt_price(tp_price),
                                         "quantity": tp_qty}, trigger=tp_price, qty=tp_qty)
        protection["tp"] = asdict(tp)
        out.update(tp_order_id=tp.order_id)
        self._save(sig, {"exit_mode": exit_mode, "tp_order_id": tp.order_id, "tp_price": tp_price, "tp_qty": tp_qty,
                         "tp_status": tp.exchange_status or tp.status, "raw_protection": protection})
        if tp.status != "confirmed":
            what = "BingX từ chối" if tp.status == "rejected" else "chưa xác nhận được"
            self._report(sig, "tp_place" if tp.status == "rejected" else "tp_verify",
                         f"Entry ĐÃ KHỚP, SL {'đã xác nhận' if sl.status == 'confirmed' else 'chưa xác nhận'}, nhưng TP {what}: "
                         f"{tp.error_code or ''} {tp.error}", order_id=tp.order_id or entry_order_id, code=tp.error_code,
                         details={"tp": protection["tp"]})

        trail = None
        if exit_mode == "split_trailing":
            act = Decimal(str(avg)) * (Decimal(1) + Decimal(str(self.settings.trailing_activation_pct)) * direction)
            act_price = float(act)
            rate = self.settings.trailing_callback_rate
            trail = self._place_leg(sig, "trailing", {"symbol": sig.symbol, "side": opposite, "positionSide": position_side,
                                                      "type": TYPES["trailing"], "quantity": trail_qty,
                                                      "activationPrice": self.client.fmt_price(act_price),
                                                      "priceRate": str(rate)}, trigger=act_price, qty=trail_qty,
                                    callback=rate)
            protection["trailing"] = asdict(trail)
            out.update(trailing_order_id=trail.order_id, trailing_activation_price=act_price,
                       trailing_callback_rate=rate, trailing_qty=trail_qty)
            self._save(sig, {"trailing_order_id": trail.order_id, "trailing_activation_price": act_price,
                             "trailing_callback_rate": rate, "trailing_qty": trail_qty,
                             "trailing_status": trail.exchange_status or trail.status, "raw_protection": protection})
            if trail.status != "confirmed":
                what = "BingX từ chối" if trail.status == "rejected" else "chưa xác nhận được"
                self._report(sig, "trailing_place" if trail.status == "rejected" else "trailing_verify",
                             f"Entry ĐÃ KHỚP, nhưng Trailing {what}: {trail.error_code or ''} {trail.error}",
                             order_id=trail.order_id or entry_order_id, code=trail.error_code,
                             details={"trailing": protection["trailing"]})

        legs = [sl, tp] + ([trail] if trail is not None else [])
        all_ok = all(leg.status == "confirmed" for leg in legs)
        stage = "protected" if all_ok else "protection_incomplete"
        self._save(sig, {"stage": stage, "protection_status": "confirmed" if all_ok else "failed",
                         "raw_protection": protection, "error": None if all_ok else "see control_logs"})
        out.update(stage=stage, protection=protection)
        if all_ok and self.alert is not None:
            try:
                self.alert.trade_success(out)
            except Exception as exc:
                log.error("V3_L5_SUCCESS_SUBMIT_FAILED error_class=%s", type(exc).__name__)
        return out

    # ------------------------------------------------------------------
    def _place_leg(self, sig: V3Signal, kind: str, params: dict, *, trigger: float, qty: str,
                   callback: float | None = None) -> LegResult:
        coid = client_order_id(sig.event_id, kind)
        params = {**params, "clientOrderId": coid}
        ack: dict = {}
        order_id = None
        try:
            payload = self.client.place_order(params)
            ack = extract_order(payload)
            order_id = order_id_of(ack) or None
        except BingXApiError as exc:
            return LegResult(kind, "rejected", error=clean_text(exc.message, 500), error_code=exc.code,
                             evidence={"params": params})
        except Exception as exc:
            ack = {"post_error": f"{type(exc).__name__}: {clean_text(exc, 300)}"}  # response lost: verify by ID

        ev = OrderEvidence("UNKNOWN", error="chưa GET")
        for attempt in range(1, self.protection_confirm_attempts + 1):
            ev = self.reader.confirm_order(sig.symbol, order_id=order_id, client_order_id=coid)
            if ev.status in ACTIVE or ev.status in TERMINAL_UNFILLED:
                break
            if attempt < self.protection_confirm_attempts:
                time.sleep(self.protection_confirm_sleep_sec)
        try:
            self.store.append_evidence(sig.event_id, {"mechanism": 2, "leg": kind, **ev.as_dict()})
        except Exception as exc:
            self._report(sig, "protection_evidence_store", f"Không lưu được bằng chứng xác nhận {LEG_NAMES[kind]}: {exc}")
        order_id = ev.order_id or order_id
        evidence = {"params": params, "ack": ack, "confirm": ev.as_dict()}
        if ev.status not in ACTIVE:
            if ev.status in TERMINAL_UNFILLED:
                return LegResult(kind, "rejected", order_id, ev.status, f"BingX trạng thái {ev.status} sau khi đặt",
                                 ev.error_code, evidence)
            return LegResult(kind, "unknown", order_id, ev.status, ev.error or f"BingX GET trả {ev.status}",
                             ev.error_code, evidence)
        problem = self._mismatch(kind, ev.order, ack, params, trigger, qty, callback)
        if problem:
            return LegResult(kind, "unknown", order_id, ev.status, problem, "PROTECTION_MISMATCH", evidence)
        return LegResult(kind, "confirmed", order_id, ev.status, evidence=evidence)

    @staticmethod
    def _mismatch(kind, order: dict, ack: dict, params: dict, trigger: float, qty: str, callback) -> str | None:
        def field(name):
            value = order.get(name)
            if value in (None, "", "0", 0) and isinstance(ack, dict):
                value = ack.get(name)
            return value
        if str(order.get("type") or "").upper() not in ("", TYPES[kind]):
            return f"loại lệnh BingX {order.get('type')} khác {TYPES[kind]}"
        if str(order.get("side") or "").upper() not in ("", params["side"]):
            return f"chiều lệnh BingX {order.get('side')} khác {params['side']}"
        if str(order.get("positionSide") or "").upper() not in ("", params["positionSide"]):
            return f"positionSide BingX {order.get('positionSide')} khác {params['positionSide']}"
        got_qty = _dec(order.get("origQty") or order.get("quantity") or (ack or {}).get("origQty") or (ack or {}).get("quantity"))
        want_qty = _dec(qty)
        if got_qty is not None and want_qty is not None and got_qty != want_qty:
            return f"khối lượng BingX {got_qty} khác yêu cầu {want_qty}"
        trig = _dec(field("activationPrice" if kind == "trailing" else "stopPrice"))
        sent = _dec(params.get("activationPrice") if kind == "trailing" else params.get("stopPrice"))
        if trig is not None and sent is not None and trig > 0:
            tolerance = max(Decimal("0.011"), sent * Decimal("0.00001"))
            if abs(trig - sent) > tolerance:
                return f"giá kích hoạt BingX {trig} khác yêu cầu {sent}"
        if kind == "trailing" and callback is not None:
            rate = _dec(field("priceRate"))
            if rate is not None and rate > 0 and rate != _dec(callback):
                return f"callback trailing BingX {rate} khác yêu cầu {callback}"
        return None

    def _force_close(self, sig: V3Signal, opposite: str, position_side: str, qty: str, entry_order_id) -> dict:
        """Close exactly this signal's filled quantity after a definitive SL rejection."""
        coid = client_order_id(sig.event_id, "close")
        params = {"symbol": sig.symbol, "side": opposite, "positionSide": position_side, "type": "MARKET",
                  "quantity": qty, "clientOrderId": coid}
        result: dict[str, Any] = {"client_order_id": coid, "params": params, "confirmed": False}
        try:
            ack = extract_order(self.client.place_order(params))
            result["order_id"] = order_id_of(ack) or None
        except BingXApiError as exc:
            result["error"] = f"BingX từ chối Force Close: mã {exc.code} — {exc.message}"
        except Exception as exc:
            result["error"] = f"phản hồi Force Close bị mất: {type(exc).__name__}: {clean_text(exc, 300)}"
        if "error" not in result or "bị mất" in result.get("error", ""):
            ev = self.reader.confirm_entry(sig.symbol, event_id=sig.event_id, order_id=result.get("order_id"),
                                           client_order_id=coid)
            result["confirm"] = ev.as_dict()
            result["order_id"] = ev.order_id or result.get("order_id")
            result["confirmed"] = ev.status == FILLED
        if result["confirmed"]:
            self._report(sig, "force_close", f"Force Close ĐÃ XÁC NHẬN: đóng {qty} (lệnh {result['order_id']}) vì SL bị từ chối",
                         order_id=result["order_id"], code="FORCE_CLOSE_CONFIRMED", details=result)
        else:
            self._report(sig, "force_close", f"VỊ THẾ KHÔNG CÓ SL: Force Close chưa xác nhận — {result.get('error') or result.get('confirm')}",
                         order_id=result.get("order_id") or entry_order_id, code="UNPROTECTED_POSITION", details=result)
        return result
