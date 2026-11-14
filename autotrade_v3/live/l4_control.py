"""L4 CONTROL: read-only BingX verification, central control_logs, hourly sync.

Exactly three BingX read mechanisms (Owner decision, Issue #109):
  1. ``evidence_at_send``  - GET when L3 sends an Entry. Runs in the background
     after the POST was dispatched; it is evidence only and never blocks/delays
     the POST.
  2. ``confirm_order`` / ``confirm_entry`` - GET the exact order by orderId or
     clientOrderId: status, fill price and fill quantity. Insufficient evidence
     is reported as UNKNOWN; absence is never assumed.
  3. ``HourlySync`` - :07 (Vietnam time) BingX order-history GET for V3's own
     open combos, written straight into execution_results / execution_finances.
     No V2 collector and no autotrade_analytics.* table is used.

L4 never POSTs or modifies orders and no GET result is used to block an
independent signal. The one write L4 owns is the leftover cleanup in
``l4_leftover`` (DELETE of a closed combo's own orphan protection orders by exact
orderId, Owner decision 2026-11-14); it never blocks or delays an Entry.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .sanitize import clean, clean_text

log = logging.getLogger("autotrade_v3.l4")

ORDER_NOT_EXIST_CODE = "109421"
FILLED = "FILLED"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
NOT_FOUND = "NOT_FOUND"
UNKNOWN = "UNKNOWN"
TERMINAL_UNFILLED = {"CANCELED", "CANCELLED", "EXPIRED", "REJECTED", "FAILED"}
ACTIVE = {"NEW", "PENDING", "PARTIALLY_FILLED", "FILLED", "TRIGGERED", "WORKING"}

POSITIONS_PATH = "/openApi/swap/v2/user/positions"
ORDER_PATH = "/openApi/swap/v2/trade/order"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value) -> float:
    try:
        d = Decimal(str(value))
        return float(d) if d.is_finite() else 0.0
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def order_id_of(order: dict) -> str:
    return str(order.get("orderId") or order.get("orderID") or "").strip()


def extract_order(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("order"), dict):
        return data["order"]
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# control_logs + ERROR alert
# --------------------------------------------------------------------------
class ControlLog:
    """Every problem = one control_logs row + one ERROR alert. No dedupe/merge.

    A database failure still sends the ERROR alert (with "no DB id"); a Discord
    failure is handled inside L5. ``report`` never raises.
    """

    def __init__(self, store, alert=None):
        self.store = store
        self.alert = alert
        self.db_failures = 0
        self.recent: list[dict] = []

    def report(self, layer: str, step: str, message: str, *, symbol: str | None = None, event_id: str | None = None,
               order_id: str | None = None, error_code: str | None = None, details: dict | None = None) -> int | None:
        rec = {"layer": layer, "step": step, "message": clean_text(message), "symbol": symbol, "event_id": event_id,
               "order_id": str(order_id) if order_id else None, "error_code": str(error_code) if error_code else None,
               "details": clean(details or {})}
        log.error("V3_CONTROL layer=%s step=%s symbol=%s event_id=%s order_id=%s code=%s message=%s",
                  layer, step, symbol, event_id, order_id, error_code, rec["message"])
        log_id = None
        try:
            log_id = self.store.insert_control_log(rec)
        except Exception as exc:
            self.db_failures += 1
            log.error("V3_CONTROL_LOG_DB_FAILED error_class=%s error=%s", type(exc).__name__, clean_text(exc, 300))
        self.recent.append({**rec, "id": log_id, "at": _now_iso()})
        del self.recent[:-100]
        if self.alert is not None:
            try:
                self.alert.error(rec, log_id)
            except Exception as exc:
                log.error("V3_CONTROL_ALERT_FAILED error_class=%s", type(exc).__name__)
        return log_id


# --------------------------------------------------------------------------
# Mechanisms 1 and 2
# --------------------------------------------------------------------------
@dataclass
class OrderEvidence:
    status: str                      # BingX status, NOT_FOUND or UNKNOWN
    order: dict = field(default_factory=dict)
    raw: Any = None
    error: str | None = None
    error_code: str | None = None
    queried_at: str = field(default_factory=_now_iso)
    lookup: dict = field(default_factory=dict)

    @property
    def order_id(self) -> str:
        return order_id_of(self.order)

    @property
    def executed_qty(self) -> float:
        return _num(self.order.get("executedQty"))

    @property
    def executed_qty_raw(self) -> str:
        return str(self.order.get("executedQty") or "0")

    @property
    def avg_price(self) -> float:
        return _num(self.order.get("avgPrice"))

    def as_dict(self) -> dict:
        return clean({"status": self.status, "order": self.order, "error": self.error, "error_code": self.error_code,
                      "queried_at": self.queried_at, "lookup": self.lookup, "endpoint": ORDER_PATH})


class L4Reader:
    """Read-only BingX access for L3. Uses the shared signed transport (GET only)."""

    def __init__(self, client, store, control: ControlLog, *, confirm_attempts: int = 10,
                 confirm_sleep_sec: float = 1.5, background: bool = True):
        self.client = client
        self.store = store
        self.control = control
        self.confirm_attempts = confirm_attempts
        self.confirm_sleep_sec = confirm_sleep_sec
        self.background = background

    # Mechanism 1 --------------------------------------------------------
    def evidence_at_send(self, *, symbol: str, event_id: str, client_order_id: str, post_outcome: dict) -> None:
        """Record what BingX shows at the time the Entry was sent. Never blocks L3."""
        def run():
            evidence = {"mechanism": 1, "at": _now_iso(), "symbol": symbol, "client_order_id": client_order_id,
                        "post_outcome": post_outcome, "endpoint": POSITIONS_PATH}
            try:
                evidence["positions"] = self.client.get_positions(symbol)
            except Exception as exc:
                evidence["error"] = f"{type(exc).__name__}: {clean_text(exc, 500)}"
                self.control.report("L4", "entry_send_evidence_get", f"GET bằng chứng khi gửi Entry thất bại: {evidence['error']}",
                                    symbol=symbol, event_id=event_id, error_code=getattr(exc, "code", None),
                                    details={"client_order_id": client_order_id})
            try:
                self.store.append_evidence(event_id, evidence)
            except Exception as exc:
                self.control.report("L4", "entry_send_evidence_store", f"Không lưu được bằng chứng L4 cơ chế 1: {exc}",
                                    symbol=symbol, event_id=event_id)
        if self.background:
            threading.Thread(target=run, name=f"v3-l4-evidence-{client_order_id[-6:]}", daemon=True).start()
        else:
            run()

    # Mechanism 2 --------------------------------------------------------
    def confirm_order(self, symbol: str, *, order_id: str | None = None, client_order_id: str | None = None) -> OrderEvidence:
        """One GET of the exact order. Verifies BingX returned that same order."""
        lookup = {"symbol": symbol, "orderId": order_id, "clientOrderId": client_order_id}
        try:
            raw = self.client.get_order_detail(symbol, order_id=order_id or None,
                                               client_order_id=None if order_id else client_order_id)
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "")
            if code == ORDER_NOT_EXIST_CODE:
                return OrderEvidence(NOT_FOUND, error=clean_text(exc, 500), error_code=code, lookup=lookup)
            return OrderEvidence(UNKNOWN, error=f"{type(exc).__name__}: {clean_text(exc, 500)}", error_code=code or None,
                                 lookup=lookup)
        order = extract_order(raw)
        got_id = order_id_of(order)
        got_client = str(order.get("clientOrderId") or order.get("clientOrderID") or "")
        if not got_id:
            return OrderEvidence(UNKNOWN, order=order, raw=raw, error="BingX GET không trả orderId", lookup=lookup)
        if order_id and got_id != str(order_id):
            return OrderEvidence(UNKNOWN, order=order, raw=raw, lookup=lookup,
                                 error=f"BingX trả về orderId khác: yêu cầu {order_id}, nhận {got_id}")
        if client_order_id and got_client and got_client != client_order_id:
            return OrderEvidence(UNKNOWN, order=order, raw=raw, lookup=lookup,
                                 error=f"BingX trả về clientOrderId khác: yêu cầu {client_order_id}, nhận {got_client}")
        if order.get("symbol") and order.get("symbol") != symbol:
            return OrderEvidence(UNKNOWN, order=order, raw=raw, lookup=lookup, error="BingX trả về lệnh của symbol khác")
        status = str(order.get("status") or "").upper() or UNKNOWN
        return OrderEvidence(status, order=order, raw=raw, lookup=lookup)

    def confirm_entry(self, symbol: str, *, event_id: str, order_id: str | None, client_order_id: str) -> OrderEvidence:
        """Poll the exact Entry until FILLED or terminal; returns the last evidence."""
        last = OrderEvidence(UNKNOWN, error="chưa GET")
        for attempt in range(1, self.confirm_attempts + 1):
            last = self.confirm_order(symbol, order_id=order_id, client_order_id=client_order_id)
            last.lookup["attempt"] = attempt
            if last.order_id and not order_id:
                order_id = last.order_id
            if last.status == FILLED and last.executed_qty > 0 and last.avg_price > 0:
                break
            if last.status in TERMINAL_UNFILLED:
                break
            if attempt < self.confirm_attempts:
                time.sleep(self.confirm_sleep_sec)
        try:
            self.store.append_evidence(event_id, {"mechanism": 2, **last.as_dict()})
        except Exception as exc:
            self.control.report("L4", "entry_confirm_store", f"Không lưu được bằng chứng L4 cơ chế 2: {exc}",
                                symbol=symbol, event_id=event_id, order_id=order_id)
        return last


# --------------------------------------------------------------------------
# Mechanism 3: hourly :07 sync (existing collector) + V3 projection
# --------------------------------------------------------------------------
CLOSE_LEGS = (("sl_order_id", "SL"), ("tp_order_id", "TP"), ("trailing_order_id", "TRAILING"))


def _norm_history(row: dict) -> dict:
    """BingX order-history row -> the shape project_results works with."""
    return {"order_id": order_id_of(row), "status": str(row.get("status") or ""), "avg_price": _num(row.get("avgPrice")),
            "filled_qty": _num(row.get("executedQty")),
            "updated_ms": int(_num(row.get("updateTime") or row.get("time"))) or None, "raw": row}


def project_results(store, control: ControlLog, client, *, lookback_days: int = 7, now_ms=None) -> dict:
    """Mechanism 3: GET BingX order history for V3's own open combos and write
    execution_results + execution_finances (V3 tables only).

    No V2 archive (autotrade_analytics.*) is read or written. One history GET per
    open combo, from its own creation time (bounded to < 7 days). Attribution is by
    this trade's own protection order IDs.
    """
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    counts = {"executions": 0, "results": 0, "finances": 0}
    executions = store.executions_for_sync(since)
    counts["executions"] = len(executions)
    for ex in executions:
        try:
            raw_prot = ex.get("raw_protection") or {}
            if isinstance(raw_prot, str):
                raw_prot = json.loads(raw_prot)
            legs = {str(ex[col]): kind for col, kind in CLOSE_LEGS if ex.get(col)}
            force_close_id = (raw_prot.get("force_close") or {}).get("order_id")
            if force_close_id:
                legs[str(force_close_id)] = "FORCE_CLOSE"
            entry_id = str(ex["entry_order_id"])
            symbol = str(ex["symbol"])
            created = ex.get("created_at")
            start = int(created.timestamp() * 1000) - 120_000 if isinstance(created, datetime) else now - 3 * 3600_000
            start = max(start, now - int(6.5 * 86400_000))
            rows = client.get_order_history(symbol, start, now + 60_000, required_ids={entry_id, *legs})
            orders = [_norm_history(r) for r in rows if order_id_of(r)]
            by_id = {o["order_id"]: o for o in orders}
            open_fee = abs(_num((by_id.get(entry_id) or {}).get("raw", {}).get("commission")))
            closed_qty = 0.0
            gross = 0.0
            close_fee = 0.0
            parents_with_child = {str((o["raw"] or {}).get("triggerOrderId") or (o["raw"] or {}).get("triggerOrderID") or "")
                                  for o in orders} - {""}
            for o in orders:
                oid = str(o["order_id"])
                if oid in parents_with_child:
                    continue  # count the triggered child order once, not its parent as well
                raw = o["raw"] or {}
                parent = str(raw.get("triggerOrderId") or raw.get("triggerOrderID") or "")
                leg = legs.get(parent) if parent in legs else legs.get(oid)
                if leg is None or oid == entry_id:
                    continue
                if o["status"].upper() not in {"FILLED", "PARTIALLY_FILLED"} or o["filled_qty"] <= 0:
                    continue
                pnl = _num(raw.get("profit"))
                fee = abs(_num(raw.get("commission")))
                qty = o["filled_qty"]
                closed_qty += qty
                gross += pnl
                close_fee += fee
                store.upsert_result({
                    "execution_order_id": ex["id"], "event_id": ex["event_id"], "close_order_id": oid,
                    "close_type": leg, "closed_qty": qty, "close_price": o["avg_price"],
                    "realized_pnl": pnl, "fee": fee,
                    "closed_at": datetime.fromtimestamp(o["updated_ms"] / 1000, tz=timezone.utc) if o["updated_ms"] else None,
                    "raw": {"order": raw}})
                counts["results"] += 1
            avg = _num(ex.get("avg_price"))
            qty_open = _num(ex.get("executed_qty"))
            lev = _num(ex.get("leverage")) or None
            value = avg * qty_open
            margin = value / lev if lev else None
            net = gross - open_fee - close_fee
            store.upsert_finance({
                "execution_order_id": ex["id"], "event_id": ex["event_id"], "margin": margin, "leverage": lev,
                "position_value": value, "open_fee": open_fee, "close_fee": close_fee,
                "funding": None,  # BingX funding is per symbol/position, not per order: not attributed
                "gross_pnl": gross, "net_pnl": net, "roi_pct": (net / margin * 100) if margin else None,
                "closed_qty": closed_qty, "is_fully_closed": qty_open > 0 and closed_qty + 1e-12 >= qty_open,
                "raw": {"source": "bingx order history GET (v3)", "legs": legs}})
            counts["finances"] += 1
        except Exception as exc:
            control.report("L4", "hourly_projection", f"Không dựng được kết quả/tài chính cho lệnh: {exc}",
                           symbol=ex.get("symbol"), event_id=ex.get("event_id"), order_id=ex.get("entry_order_id"))
    return counts


def _next_slot(now: datetime) -> datetime:
    """Next HH:07 (Vietnam = UTC+7, so the UTC minute is the same)."""
    slot = now.replace(minute=7, second=0, microsecond=0)
    return slot if slot > now else slot + timedelta(hours=1)


class HourlySync:
    """V3-only :07 sync: BingX order-history GET (3h window) -> execution_results/finances.

    Own thread, no V2 collector, no autotrade_analytics.* tables.
    """

    def __init__(self, store, control: ControlLog, admit, client=None, after_sync=None, now=None):
        self.store = store
        self.client = client
        self.after_sync = after_sync
        self.control = control
        self.admit = admit
        self.last: dict = {"state": "not_started"}
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> dict:
        try:
            counts = project_results(self.store, self.control, self.client)
            self.last = {"state": "waiting", "result": "completed", "finished_at": _now_iso(), "v3_projection": counts}
        except Exception as exc:
            self.last = {"state": "failed", "result": "failed", "error_class": type(exc).__name__}
            self.control.report("L4", "hourly_sync", f"Đồng bộ BingX định kỳ :07 lỗi: {exc}")
            return self.last
        if self.after_sync is not None:
            try:
                self.after_sync("hourly_sync")
            except Exception as exc:
                self.control.report("L4", "hourly_after_sync", f"Không kích hoạt được dọn lệnh sau sync :07: {exc}")
        return self.last

    def _loop(self) -> None:
        self.last = {"state": "waiting"}
        slot = _next_slot(self._now())
        while not self._stop.is_set():
            if self._now() >= slot:
                # Yield to trading: wait (bounded) for admission, then run once.
                waited = 0
                while not self._stop.is_set() and waited < 600:
                    try:
                        if self.admit():
                            break
                    except Exception:
                        break
                    self._stop.wait(5)
                    waited += 5
                if self._stop.is_set():
                    break
                self.run_once()
                slot = _next_slot(self._now())
            self._stop.wait(30)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="v3-l4-hourly", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {"last": clean(self.last), "runtime": {"alive": bool(self._thread and self._thread.is_alive())}}
