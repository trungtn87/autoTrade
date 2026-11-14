"""L4 LEFTOVER: cancel the protection orders a combo no longer needs, and ONLY those.

A combo is closed when the sum of the fills of its OWN exit orders equals its own
Entry fill (exact decimal). Only then are the protection legs (SL/TP/trailing)
that are still open on BingX cancelled, one by one, by exact ``orderId``.

Hard rules (Owner decisions, 2026-11-14):
  * never blocks, delays or conditions an Entry; it runs in the background AFTER
    the new signal's own L3 thread has finished (so it never queues BingX
    requests in front of that signal's SL/TP);
  * never ``cancel all``, never ``closePosition``, never uses ``positionId`` or
    price/time to decide ownership, never touches another combo's orders;
  * an exit that cannot be proven (missing history, id mismatch, partial data)
    means NO cancel: it is recorded in ``control_logs`` and alerted instead;
  * every DELETE is written ahead, then verified by an exact-ID GET; a lost
    DELETE reply is never assumed to have succeeded;
  * triggers: after each Entry attempt, and after the hourly :07 sync. Nothing else.

Mode ``V3_LEFTOVER_CLEANUP_MODE``: ``live`` (default, Owner decision 2026-11-14) |
``shadow`` (decide and record what WOULD be cancelled, send no DELETE) | ``off``.
Owner decision (2026-11-14): all leftover handling (reads, decision, cancel, log) lives in L4.
L3 only sends orders; this module is the only V3 code that cancels. Future fixes and
extensions of this behaviour go here.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .l4_control import ACTIVE, FILLED, NOT_FOUND, TERMINAL_UNFILLED, UNKNOWN
from .sanitize import clean, clean_text
from .transport import BingXApiError

log = logging.getLogger("autotrade_v3.l4.leftover")

TYPES = {"sl": "STOP_MARKET", "tp": "TAKE_PROFIT_MARKET", "trailing": "TRAILING_STOP_MARKET"}
LEG_COLUMNS = (("sl", "sl_order_id"), ("tp", "tp_order_id"), ("trailing", "trailing_order_id"))
OPEN_STATUSES = {"NEW", "PENDING", "PARTIALLY_FILLED", "WORKING"}
CANDIDATE_STAGES = {"protected", "protection_incomplete"}
MAX_CANCEL_ATTEMPTS = 2
RETRY_GAP_MS = 60_000
LOOKBACK_DAYS = 6
MODES = ("off", "shadow", "live")


def _dec(value) -> Decimal | None:
    try:
        d = Decimal(str(value))
        return d if d.is_finite() and d >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def norm_id(value) -> str:
    """Exact positive integer id as a string. Floats from lossy JSON are made exact integers."""
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        value = int(value)
    text = str(value).strip()
    return text if re.fullmatch(r"[1-9][0-9]*", text) else ""


def row_id(row: dict) -> str:
    return norm_id(row.get("orderID") or row.get("orderId"))


def trigger_id(row: dict) -> str:
    return norm_id(row.get("triggerOrderID") or row.get("triggerOrderId"))


class Unproven(Exception):
    """The leg's fills cannot be proven from exact BingX data."""


class LeftoverCleaner:
    def __init__(self, settings, client, store, reader, control, alert=None, *, busy=None, mode: str | None = None,
                 busy_wait_sec: float = 180.0, now_ms=None):
        self.settings = settings
        self.alert = alert
        self.client = client
        self.store = store
        self.reader = reader
        self.control = control
        self.mode = (mode or getattr(settings, "leftover_cleanup_mode", "live") or "live").lower()
        if self.mode not in MODES:
            self.mode = "live"
        self._busy = busy or (lambda: False)
        self._busy_wait_sec = busy_wait_sec
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._pass_lock = threading.Lock()
        self._guard = threading.Lock()
        self._pending = False
        self._running = False
        self.last: dict = {"state": "not_run", "mode": self.mode}

    # ------------------------------------------------------------------ trigger
    def request(self, reason: str) -> None:
        """Ask for a pass (Entry attempt or :07 sync). Never blocks the caller."""
        if self.mode == "off":
            return
        with self._guard:
            if self._running:
                self._pending = True
                return
            self._running = True
        try:
            threading.Thread(target=self._loop, args=(reason,), name="v3-leftover", daemon=True).start()
        except Exception as exc:
            with self._guard:
                self._running = False
            self.control.report("L4", "leftover_start", f"Không khởi động được dọn lệnh thừa: {exc}")

    def _loop(self, reason: str) -> None:
        try:
            while True:
                self._wait_idle()
                self.run_pass(reason)
                with self._guard:
                    if not self._pending:
                        self._running = False
                        return
                    self._pending = False
                reason = "queued"
        except Exception as exc:  # never let the background thread die silently
            with self._guard:
                self._running = False
            log.exception("V3_LEFTOVER_LOOP_FAILED")
            self.control.report("L4", "leftover_loop", f"Dọn lệnh thừa gặp lỗi nội bộ: {type(exc).__name__}: {exc}")

    def _wait_idle(self) -> None:
        """Let every in-flight Entry/protection finish first: its SL/TP POSTs go before our GETs."""
        deadline = time.monotonic() + self._busy_wait_sec
        while time.monotonic() < deadline:
            try:
                if not self._busy():
                    return
            except Exception:
                return
            time.sleep(1.0)

    # --------------------------------------------------------------------- pass
    def run_pass(self, reason: str = "manual") -> dict:
        with self._pass_lock:
            summary: dict[str, Any] = {"state": "running", "mode": self.mode, "reason": reason,
                                       "at_ms": self._now_ms(), "executions": {}, "open_order_types": {}}
            try:
                since = datetime.fromtimestamp(self._now_ms() / 1000 - LOOKBACK_DAYS * 86400, tz=timezone.utc)
                rows = self.store.executions_for_cleanup(since)
            except Exception as exc:
                self.control.report("L4", "leftover_candidates", f"Không đọc được danh sách lệnh cần dọn: {exc}")
                summary["state"] = "failed"
                self.last = summary
                return summary
            by_symbol: dict[str, list[dict]] = {}
            for ex in rows:
                if self._candidate(ex):
                    by_symbol.setdefault(str(ex["symbol"]), []).append(ex)
            for symbol, items in by_symbol.items():
                try:
                    open_rows = self.client.get_open_orders(symbol)
                except Exception as exc:
                    self.control.report("L4", "leftover_open_orders",
                                        f"GET openOrders {symbol} thất bại; không dọn gì: {type(exc).__name__}: {clean_text(exc, 300)}",
                                        symbol=symbol, error_code=getattr(exc, "code", None))
                    continue
                open_by_id = {row_id(r): r for r in open_rows if isinstance(r, dict) and row_id(r)}
                for r in open_rows:
                    if isinstance(r, dict):
                        t = str(r.get("type") or "?")
                        summary["open_order_types"][t] = summary["open_order_types"].get(t, 0) + 1
                for ex in items:
                    try:
                        summary["executions"][ex["event_id"]] = self._handle(ex, open_by_id)
                    except Exception as exc:
                        log.exception("V3_LEFTOVER_EXEC_FAILED event_id=%s", ex.get("event_id"))
                        self.control.report("L4", "leftover_exec", f"Lỗi nội bộ khi dọn lệnh {ex.get('event_id')}: "
                                            f"{type(exc).__name__}: {exc}", symbol=ex.get("symbol"), event_id=ex.get("event_id"))
                        summary["executions"][ex["event_id"]] = "internal_error"
            summary["state"] = "completed"
            self.last = clean(summary)
            return self.last

    @staticmethod
    def _candidate(ex: dict) -> bool:
        if str(ex.get("stage") or "") not in CANDIDATE_STAGES:
            return False
        if not norm_id(ex.get("entry_order_id")) or _dec(ex.get("executed_qty")) in (None, Decimal(0)):
            return False
        state = _protection(ex).get("cleanup") or {}
        return state.get("status") != "done"

    # ----------------------------------------------------------------- one combo
    def _handle(self, ex: dict, open_by_id: dict) -> str:
        symbol = str(ex["symbol"])
        entry_qty = _dec(ex.get("executed_qty"))
        state = dict((_protection(ex).get("cleanup")) or {})
        state.setdefault("legs", {})
        state.setdefault("attempts", {})
        state.setdefault("warnings", [])
        state.setdefault("shadow_logged", [])
        legs = {kind: norm_id(ex.get(col)) for kind, col in LEG_COLUMNS if ex.get(col)}
        if not legs or any(not v for v in legs.values()):
            self._warn_once(ex, state, "LEFTOVER_LEG_ID_INVALID", "orderId chân bảo vệ không hợp lệ; không dọn")
            self._persist(ex, state)
            return "unproven"
        history: dict[str, Any] = {}
        views: dict[str, dict] = {}
        unproven: list[str] = []
        for kind, leg_id in legs.items():
            try:
                views[kind] = self._leg_view(ex, kind, leg_id, open_by_id, state, history)
            except Unproven as exc:
                unproven.append(f"{kind}:{leg_id}")
                self._warn_once(ex, state, f"UNPROVEN:{leg_id}",
                                f"Không chứng minh được khối lượng khớp của chân {kind.upper()} ({leg_id}): {exc}. Không hủy gì.",
                                order_id=leg_id)
        total = sum((Decimal(v["executed"]) for v in views.values()), Decimal(0))
        active = {k: v for k, v in views.items() if v["status"] == "ACTIVE"}
        outcome = "open"
        if unproven:
            outcome = "unproven"
        elif total > entry_qty:
            self._warn_once(ex, state, "LEFTOVER_FILL_OVER_ENTRY",
                            f"Tổng khớp của các chân ({total}) lớn hơn khối lượng Entry ({entry_qty}); không hủy gì")
            outcome = "anomaly"
        elif total == entry_qty:
            outcome = self._close_out(ex, state, active, legs)
        else:
            remaining = entry_qty - total
            sl = active.get("sl")
            if sl is not None and Decimal(sl["orig"]) - Decimal(sl["executed"]) > remaining:
                self._warn_once(ex, state, "SL_OVER_REMAINING",
                                f"SL còn {Decimal(sl['orig']) - Decimal(sl['executed'])} lớn hơn phần còn lại của combo ({remaining})"
                                " (chỉ cảnh báo, không tự sửa)", order_id=legs["sl"])
        if outcome in ("closed_clean", "cancelled", "would_cancel") and not state.get("closed_notified"):
            state["closed_notified"] = True
            self._notify_closed(ex, state, views, active, legs, outcome)
        state["last_outcome"] = outcome
        state["last_checked_ms"] = self._now_ms()
        state["entry_qty"] = str(entry_qty)
        state["exit_qty"] = str(total)
        self._persist(ex, state)
        return outcome

    def _close_out(self, ex, state, active: dict, legs: dict) -> str:
        if not active:
            state["status"] = "done"
            return "closed_clean"
        ids = [(kind, legs[kind]) for kind in active]
        if self.mode != "live":
            fresh = [i for _, i in ids if i not in state["shadow_logged"]]
            if fresh:
                state["shadow_logged"].extend(fresh)
                self._note(ex, "leftover_shadow", f"[SHADOW] Combo đã đóng đủ; SẼ hủy chân thừa {', '.join(fresh)} (chưa gửi DELETE)",
                           details={"legs": dict(ids)})
            return "would_cancel"
        cancelled = 0
        for kind, leg_id in ids:
            cancelled += 1 if self._cancel(ex, state, kind, leg_id) else 0
        if all(state["legs"].get(i, {}).get("status") == "TERMINAL" for _, i in ids):
            state["status"] = "done"
            return "cancelled"
        return "cancel_pending"

    # ---------------------------------------------------------------- cancel
    def _cancel(self, ex, state, kind: str, leg_id: str) -> bool:
        symbol = str(ex["symbol"])
        att = state["attempts"].setdefault(leg_id, {"n": 0, "last_ms": 0})
        now = self._now_ms()
        if att["n"] >= MAX_CANCEL_ATTEMPTS:
            self._warn_once(ex, state, f"CANCEL_RETRY_EXHAUSTED:{leg_id}",
                            f"Hủy {kind.upper()} {leg_id} đã thử {att['n']} lần mà chưa xác nhận; dừng thử, cần xem tay",
                            order_id=leg_id)
            return False
        if now - att["last_ms"] < RETRY_GAP_MS:
            return False
        att["n"] += 1
        att["last_ms"] = now
        self._persist(ex, state)               # write-ahead, before the DELETE
        try:
            self.client.cancel_order(symbol, order_id=leg_id)
        except BingXApiError as exc:
            self.control.report("L4", "leftover_cancel", f"BingX trả lỗi khi hủy {kind.upper()} {leg_id}: mã {exc.code} — {exc.message}."
                                " Sẽ GET lại để biết đã khớp, đã hủy hay còn mở.", symbol=symbol, event_id=ex.get("event_id"),
                                order_id=leg_id, error_code=exc.code)
        except Exception as exc:
            self.control.report("L4", "leftover_cancel", f"Phản hồi hủy {kind.upper()} {leg_id} bị mất: {type(exc).__name__}. "
                                "Không giả định đã hủy; kiểm tra bằng GET.", symbol=symbol, event_id=ex.get("event_id"),
                                order_id=leg_id)
        ev = self.reader.confirm_order(symbol, order_id=leg_id)
        if ev.status in {"CANCELED", "CANCELLED", "EXPIRED"}:
            state["legs"][leg_id] = {"status": "TERMINAL", "executed": str(ev.executed_qty_raw), "source": "cancel_verified"}
            state.setdefault("cleaned", []).append(kind)
            self._note(ex, "leftover_cancel_ok", f"Đã hủy chân thừa {kind.upper()} {leg_id} (xác nhận bằng GET)", order_id=leg_id)
            return True
        if ev.status == FILLED:
            state["legs"][leg_id] = {"status": "TERMINAL", "executed": str(ev.executed_qty_raw), "source": "filled_during_cancel"}
            self.control.report("L4", "leftover_cancel", f"Chân {kind.upper()} {leg_id} đã KHỚP trong lúc hủy; cần đối soát khối lượng",
                                symbol=symbol, event_id=ex.get("event_id"), order_id=leg_id, error_code="LEFTOVER_FILLED_DURING_CANCEL")
            return False
        self.control.report("L4", "leftover_cancel", f"Chưa xác nhận được hủy {kind.upper()} {leg_id}: BingX GET trả {ev.status}"
                            f"{' — ' + ev.error if ev.error else ''}", symbol=symbol, event_id=ex.get("event_id"), order_id=leg_id,
                            error_code=ev.error_code)
        return False

    # -------------------------------------------------------------- leg proof
    def _leg_view(self, ex, kind: str, leg_id: str, open_by_id: dict, state: dict, history: dict) -> dict:
        memo = state["legs"].get(leg_id)
        if memo and memo.get("status") == "TERMINAL":
            return {"status": "TERMINAL", "executed": memo["executed"], "orig": memo.get("orig", memo["executed"]),
                    "price": memo.get("price")}
        row = open_by_id.get(leg_id)
        if row is not None:
            self._check_identity(ex, kind, row)
            status = str(row.get("status") or "").upper()
            if status not in OPEN_STATUSES:
                raise Unproven(f"openOrders trả trạng thái lạ {status or 'rỗng'}")
            return self._active(row)
        ev = self.reader.confirm_order(str(ex["symbol"]), order_id=leg_id)
        status = ev.status
        if status in OPEN_STATUSES:
            if ev.order:
                self._check_identity(ex, kind, ev.order)
            self._warn_once(ex, state, f"NOT_IN_OPEN_ORDERS:{kind}",
                            f"Chân {kind.upper()} {leg_id} còn mở theo GET theo ID nhưng KHÔNG có trong openOrders (cần ghi nhận để xác minh)",
                            order_id=leg_id, quiet=True)
            return self._active(ev.order)
        if status in TERMINAL_UNFILLED:
            if ev.order:
                self._check_identity(ex, kind, ev.order)
            return self._terminal(state, leg_id, Decimal(str(ev.executed_qty_raw or "0")), "get_cancelled", ev.avg_price)
        parent = None
        if status == FILLED:
            self._check_identity(ex, kind, ev.order)
            parent = _dec(ev.executed_qty_raw)
        elif status not in {NOT_FOUND, UNKNOWN, "TRIGGERED"}:
            raise Unproven(f"trạng thái BingX không nhận diện được: {status}")
        child = self._child(ex, kind, leg_id, history)
        if child is not None:
            qty, price = child
            if parent is not None and parent not in (Decimal(0), qty):
                raise Unproven("khối lượng lệnh cha và lệnh con khi kích hoạt không khớp")
            return self._terminal(state, leg_id, qty, "triggered_child", price)
        if parent is not None and parent > 0:
            return self._terminal(state, leg_id, parent, "get_filled", ev.avg_price)
        raise Unproven(f"GET theo ID trả {status} và không tìm thấy lệnh con khớp theo triggerOrderId")

    @staticmethod
    def _active(row: dict) -> dict:
        orig = _dec(row.get("origQty") or row.get("quantity"))
        done = _dec(row.get("executedQty") or "0")
        if orig is None or done is None or done > orig:
            raise Unproven("origQty/executedQty của lệnh đang mở không hợp lệ")
        return {"status": "ACTIVE", "executed": str(done), "orig": str(orig)}

    @staticmethod
    def _terminal(state, leg_id, executed: Decimal, source: str, price=None) -> dict:
        price = float(price) if price else None
        state["legs"][leg_id] = {"status": "TERMINAL", "executed": str(executed), "source": source, "price": price}
        return {"status": "TERMINAL", "executed": str(executed), "orig": str(executed), "price": price}

    @staticmethod
    def _check_identity(ex, kind: str, row: dict) -> None:
        side = str(ex["side"]).upper()
        want_side = "SELL" if side == "BUY" else "BUY"
        want_pos = "LONG" if side == "BUY" else "SHORT"
        got = (str(row.get("symbol") or ""), str(row.get("side") or "").upper(), str(row.get("positionSide") or "").upper(),
               str(row.get("type") or "").upper())
        if got != (str(ex["symbol"]), want_side, want_pos, TYPES[kind]):
            raise Unproven(f"danh tính lệnh BingX không khớp ({got})")

    def _child(self, ex, kind: str, leg_id: str, history: dict) -> tuple[Decimal, float | None] | None:
        """Exact triggered execution: ONE FILLED child whose triggerOrderId is this leg's id."""
        if "rows" not in history:
            now = self._now_ms()
            created = ex.get("created_at")
            start = int(created.timestamp() * 1000) - 120_000 if isinstance(created, datetime) else now - 86400_000
            start = max(start, now - int(6.5 * 86400_000))
            ids = {norm_id(ex.get(col)) for _, col in LEG_COLUMNS if ex.get(col)}
            try:
                history["rows"] = self.client.get_order_history(str(ex["symbol"]), start, now + 60_000, required_ids=ids)
            except Exception as exc:
                raise Unproven(f"không đọc được lịch sử lệnh BingX: {type(exc).__name__}: {clean_text(exc, 200)}") from exc
        matches = [r for r in history["rows"] if trigger_id(r) == leg_id and row_id(r) != leg_id]
        if not matches:
            return None
        if len(matches) != 1:
            raise Unproven("nhiều lệnh con cho cùng một lệnh kích hoạt")
        child = matches[0]
        self._check_identity(ex, kind, child)
        if str(child.get("status") or "").upper() != "FILLED":
            raise Unproven("lệnh con kích hoạt chưa FILLED")
        qty = _dec(child.get("executedQty"))
        if qty is None or qty <= 0:
            raise Unproven("lệnh con kích hoạt không có khối lượng khớp")
        price = _dec(child.get("avgPrice"))
        return qty, (float(price) if price else None)

    # ---------------------------------------------------------------- L5 alert
    def _notify_closed(self, ex, state, views: dict, active: dict, legs: dict, outcome: str) -> None:
        """Discord 'combo closed' note. No exchange fill data -> no message."""
        filled = []
        for kind, v in views.items():
            qty = _dec(v.get("executed"))
            if v["status"] == "TERMINAL" and qty and qty > 0 and v.get("price"):
                filled.append({"kind": kind, "qty": str(qty), "price": v["price"]})
        if not filled or self.alert is None:
            return
        if outcome == "would_cancel":
            left = {"mode": "shadow", "legs": [k for k in active]}
        else:
            left = {"mode": self.mode, "legs": list(state.get("cleaned") or [])}
        try:
            self.alert.trade_closed({"event_id": ex["event_id"], "symbol": ex["symbol"], "combo": ex.get("combo"),
                                     "side": ex.get("side"), "avg_price": ex.get("avg_price"),
                                     "entry_qty": str(ex.get("executed_qty")), "filled": filled, "leftover": left,
                                     "closed_at_ms": self._now_ms()})
        except Exception as exc:
            log.error("V3_L5_CLOSED_SUBMIT_FAILED error_class=%s", type(exc).__name__)

    # ---------------------------------------------------------------- helpers
    def _persist(self, ex: dict, state: dict) -> None:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        raw = _protection(ex)
        raw["cleanup"] = clean(state)
        ex["raw_protection"] = raw
        try:
            self.store.update_execution(ex["event_id"], {"raw_protection": raw})
        except Exception as exc:
            self.control.report("L4", "leftover_persist", f"Không ghi được trạng thái dọn lệnh: {exc}",
                                symbol=ex.get("symbol"), event_id=ex.get("event_id"))

    def _warn_once(self, ex, state, key: str, message: str, *, order_id=None, quiet: bool = False) -> None:
        if key in state["warnings"]:
            return
        state["warnings"].append(key)
        if quiet:
            self._note(ex, "leftover_observation", message, order_id=order_id)
        else:
            self.control.report("L4", "leftover_cleanup", message, symbol=ex.get("symbol"), event_id=ex.get("event_id"),
                                order_id=order_id, error_code=key.split(":")[0])

    def _note(self, ex, step: str, message: str, *, order_id=None, details=None) -> None:
        """A control_logs row WITHOUT an ERROR alert: information, not a fault."""
        log.warning("V3_LEFTOVER step=%s event_id=%s order_id=%s %s", step, ex.get("event_id"), order_id, message)
        try:
            self.store.insert_control_log({"layer": "L4", "step": step, "message": clean_text(message), "symbol": ex.get("symbol"),
                                           "event_id": ex.get("event_id"), "order_id": str(order_id) if order_id else None,
                                           "error_code": None, "details": clean(details or {}), "discord_status": "skipped"})
        except Exception as exc:
            log.error("V3_LEFTOVER_NOTE_DB_FAILED %s", clean_text(exc, 200))

    def status(self) -> dict:
        return clean({"mode": self.mode, "last": self.last})


def _protection(ex: dict) -> dict:
    raw = ex.get("raw_protection") or {}
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    return dict(raw) if isinstance(raw, dict) else {}


def v3_threads_busy() -> bool:
    return any(t.is_alive() for t in threading.enumerate() if t.name.startswith("v3-l3-"))
