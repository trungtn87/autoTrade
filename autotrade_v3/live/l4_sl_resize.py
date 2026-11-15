"""L4 proactive SL quantity resize, preserving the same exact BingX SL order ID.

Issue #121: a full-entry SL must shrink when this combo's TP/trailing executes.
Never cancels or places a replacement SL, never adjusts other combo's order, and
never claims success from a POST response alone. Real amendment is explicitly
opt-in until STOP_MARKET has been validated against BingX on a controlled trade.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .sanitize import clean, clean_text

log = logging.getLogger("autotrade_v3.l4.sl_resize")
ACTIVE = {"NEW", "PARTIALLY_FILLED"}
MODES = {"off", "shadow", "live"}


def positive(value) -> Decimal | None:
    try:
        value = Decimal(str(value))
        return value if value.is_finite() and value > 0 else None
    except (TypeError, ValueError, InvalidOperation):
        return None


class SLResizer:
    def __init__(self, client, reader, store, control, *, mode="shadow", now_ms=None):
        self.client = client
        self.reader = reader
        self.store = store
        self.control = control
        self.mode = str(mode).lower() if mode in MODES else "shadow"
        self.now_ms = now_ms

    def _error(self, ex, sl_id, code, text, details=None):
        self.control.report("L4", "sl_resize", clean_text(text), symbol=ex.get("symbol"),
                            event_id=ex.get("event_id"), order_id=sl_id,
                            error_code=code, details=details)

    def _persist(self, ex, state):
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        raw = dict(ex.get("raw_protection") or {})
        raw["cleanup"] = clean(state)
        # Write-ahead is required BEFORE every exchange mutation.
        self.store.update_execution(ex["event_id"], {"raw_protection": raw})
        ex["raw_protection"] = raw

    def _log_note(self, ex, sl_id, text):
        try:
            self.store.insert_control_log({"layer": "L4", "symbol": ex.get("symbol"),
                                           "event_id": ex.get("event_id"), "order_id": sl_id,
                                           "step": "sl_resize_observation", "error_code": None,
                                           "message": clean_text(text), "details": {},
                                           "discord_status": "disabled"})
        except Exception as exc:
            log.error("V3_SL_RESIZE_NOTE_DB_FAILED error=%s", type(exc).__name__)

    @staticmethod
    def _check_identity(ex, sl_id, ev, expected_qty=None):
        r = ev.order or {}
        side = "BUY" if str(ex.get("side")).upper() == "SELL" else "SELL"
        pos = "SHORT" if str(ex.get("side")).upper() == "SELL" else "LONG"
        if (ev.status not in ACTIVE or str(r.get("orderId") or "") != sl_id
                or str(r.get("symbol")) != str(ex.get("symbol"))
                or str(r.get("side")).upper() != side
                or str(r.get("positionSide")).upper() != pos
                or str(r.get("type")).upper() != "STOP_MARKET"):
            raise ValueError("BingX SL order identity/status mismatch")
        proof = ((ex.get("raw_protection") or {}).get("sl") or {})
        expected_client = (((proof.get("evidence") or {}).get("params") or {}).get("clientOrderId"))
        got_client = r.get("clientOrderId")
        if not expected_client or not got_client or str(expected_client) != str(got_client):
            raise ValueError("SL clientOrderId has no matching original L3 proof")
        price = positive(r.get("stopPrice"))
        desired_price = positive(ex.get("sl_price"))
        if not price or not desired_price or abs(price - desired_price) > Decimal("0.02"):
            raise ValueError("SL stopPrice differs from original protection")
        if positive(r.get("executedQty") or "0") is not None:
            raise ValueError("partially filled SL cannot be amended safely")
        qty = positive(r.get("origQty") or r.get("quantity"))
        if qty is None:
            raise ValueError("BingX GET has no valid SL quantity")
        if expected_qty is not None and qty != expected_qty:
            raise ValueError("post-amend SL quantity does not match requested value")
        return qty

    def adjust(self, ex, state: dict, views: dict, sl_id: str, remaining: Decimal) -> bool:
        """Return True only after exact-ID GET confirms the smaller SL quantity."""
        sl = views.get("sl") or {}
        other = [views.get("tp"), views.get("trailing")]
        if self.mode == "off" or sl.get("status") != "ACTIVE":
            return False
        if not re.fullmatch(r"[1-9][0-9]*", sl_id):
            return False
        want = positive(remaining)
        current = positive(sl.get("orig"))
        if not want or not current or want >= current:
            return False
        # Require at least one other leg PROVEN terminal AND filled. Never infer
        # from total exchange position size or another combo's orders.
        if not any(v and v.get("status") == "TERMINAL"
                   and positive(v.get("executed")) for v in other):
            return False
        key = f"{sl_id}:{want}"
        events = state.setdefault("sl_resizes", {})
        prev = events.get(key)
        if prev and prev.get("state") == "confirmed":
            return True
        if self.mode == "shadow":
            if not prev:
                events[key] = {"state": "shadow", "sl_order_id": sl_id,
                               "requested_qty": str(want), "mode": "shadow"}
                try:
                    self._persist(ex, state)
                    self._log_note(ex, sl_id, f"SHADOW: SL cần giảm {current} → {want}, không POST")
                except Exception as exc:
                    self._error(ex, sl_id, "SL_RESIZE_STORE_FAILED",
                                f"Không ghi được dự tính sửa SL: {type(exc).__name__}")
            return False

        # Re-GET exact order immediately before deciding. If an earlier POST
        # was sent and reply lost, inspect before permitting any further action.
        ev = self.reader.confirm_order(str(ex["symbol"]), order_id=sl_id)
        try:
            live_qty = self._check_identity(ex, sl_id, ev)
        except ValueError as exc:
            self._error(ex, sl_id, "SL_RESIZE_IDENTITY", str(exc))
            return False
        if live_qty == want:
            events[key] = {"state": "confirmed", "requested_qty": str(want),
                           "sl_order_id": sl_id, "proof": "exact_id_get"}
            try:
                self._persist(ex, state)
            except Exception as exc:
                self._error(ex, sl_id, "SL_RESIZE_STORE_FAILED",
                            f"Không lưu được xác nhận SL: {type(exc).__name__}")
            return True
        if live_qty < want:
            self._error(ex, sl_id, "SL_RESIZE_UNEXPECTED_QTY",
                        f"SL {sl_id} đang nhỏ hơn mục tiêu: {live_qty} < {want}; không tăng SL")
            return False
        if prev and prev.get("state") in {"sent", "unknown", "rejected"}:
            return False  # at-most-once per exact ID + desired qty
        try:
            rules = self.client.get_contract_rules(str(ex["symbol"]))
            precision = int(rules["quantityPrecision"])
            step = Decimal(1).scaleb(-precision)
            min_qty = positive(rules.get("tradeMinQuantity"))
            if precision < 0 or precision > 12 or want != want.quantize(step) or (min_qty and want < min_qty):
                raise ValueError("quantity violates BingX contract rules")
        except Exception as exc:
            self._error(ex, sl_id, "SL_RESIZE_RULES", f"Không thể xác minh khối lượng SL: {type(exc).__name__}")
            return False

        events[key] = {"state": "sent", "sl_order_id": sl_id, "old_qty": str(live_qty),
                       "requested_qty": str(want), "at_ms": int(self.now_ms()) if self.now_ms else None,
                       "proof": "exact_id_get_before_post", "mode": "live"}
        try:
            self._persist(ex, state)
        except Exception as exc:
            self._error(ex, sl_id, "SL_RESIZE_STORE_FAILED",
                        f"Không ghi được write-ahead, không POST SL: {type(exc).__name__}")
            return False
        try:
            self.client.amend_order(str(ex["symbol"]), order_id=sl_id, quantity=format(want, "f"))
        except Exception as exc:
            events[key]["exchange_error"] = type(exc).__name__
            # Cannot infer success/failure from the response; exact-ID GET below.
        after = self.reader.confirm_order(str(ex["symbol"]), order_id=sl_id)
        try:
            self._check_identity(ex, sl_id, after, expected_qty=want)
        except ValueError as exc:
            events[key]["state"] = "unknown"
            try:
                self._persist(ex, state)
            except Exception:
                log.exception("V3_SL_RESIZE_PERSIST_FAILED")
            self._error(ex, sl_id, "SL_RESIZE_NOT_CONFIRMED",
                        f"Sửa SL chưa được GET xác nhận ({exc}); không POST lại", details={"target": str(want)})
            return False
        events[key]["state"] = "confirmed"
        events[key]["proof"] = "exact_id_get_after_post"
        try:
            self._persist(ex, state)
        except Exception as exc:
            self._error(ex, sl_id, "SL_RESIZE_STORE_FAILED",
                        f"Đã GET xác nhận SL nhưng không lưu được DB: {type(exc).__name__}")
        self._log_note(ex, sl_id, f"SL đã giảm {live_qty} → {want}; orderId {sl_id} được GET xác nhận")
        return True
