"""L5 ALERT: brand-new Discord notifier with exactly three webhooks.

Routing (Owner decision, Issue #108):
  L2 signal-only BTC/ETH -> symbol webhook (only with no live executor)
  L3 BingX-confirmed BTC/ETH order -> symbol webhook (single live trade message)
  any error from L1..L5 -> DISCORD_WEBHOOK_ERROR

No durable outbox or batching. Each trade event is one message; repeated trade
refs are suppressed within the running process. Error alerts have priority in
the worker queue, with the existing per-error cooldown. Sending happens on a background thread so trading never waits
for Discord; a Discord failure is written to control_logs (layer L5) and is
never re-alerted, so it cannot loop.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from itertools import count
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable

import requests

from .sanitize import clean_text

log = logging.getLogger("autotrade_v3.l5")

VN = timezone(timedelta(hours=7))
HEADERS = {"User-Agent": "DiscordBot (https://github.com/trungtn87/autoTrade, 3.0)", "Accept": "application/json"}
LAYER_NAMES = {"L1": "Tầng 1 DATA", "L2": "Tầng 2 STRATEGY", "L3": "Tầng 3 EXECUTION",
               "L4": "Tầng 4 CONTROL", "L5": "Tầng 5 ALERT"}


def vn_time(ms: int | None) -> str:
    if not ms:
        return "?"
    return datetime.fromtimestamp(int(ms) / 1000, tz=VN).strftime("%d/%m/%Y %H:%M:%S") + " (giờ VN)"


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "?"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return clean_text(value, 300)


def default_sender(url: str, content: str | dict) -> tuple[bool, str]:
    """One POST; a Discord 429 with a short Retry-After is honoured once."""
    payload = ({"content": content[:1990]} if isinstance(content, str) else dict(content))
    payload["allowed_mentions"] = {"parse": []}
    for attempt in range(2):
        resp = requests.post(url, json=payload, headers=HEADERS, timeout=8)
        if resp.status_code in (200, 204):
            return True, f"HTTP {resp.status_code}"
        if resp.status_code == 429 and attempt == 0:
            try:
                delay = float(resp.headers.get("Retry-After") or 1.0)
            except (TypeError, ValueError):
                delay = 1.0
            if delay <= 10:
                time.sleep(max(0.25, delay))
                continue
        return False, f"HTTP {resp.status_code}: {clean_text(resp.text, 200)}"
    return False, "HTTP 429 after one retry"


def vn_clock(ms: int | None) -> str:
    """HH:MM:SS · DD/MM/YYYY in Vietnam time."""
    stamp = int(ms) if ms else int(time.time() * 1000)
    return datetime.fromtimestamp(stamp / 1000, tz=VN).strftime("%H:%M:%S · %d/%m/%Y")


def _fmt_price(value: Any) -> str:
    """Round display-only prices to one decimal; never modify order payloads."""
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return "?"
        return f"{number.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):.1f}"
    except (InvalidOperation, ValueError, TypeError):
        return "?"


def _symbol_label(symbol: Any) -> str:
    """Short display label, not an exchange symbol rewrite."""
    return str(symbol or "?").upper().split("-")[0]


def _confirmed_stop_value(row: dict, kind: str) -> Decimal | None:
    """Price actually confirmed for the protection leg, not the strategy estimate.

    Prefer BingX GET, then its POST acknowledgement, then the exact submitted
    stopPrice verified by L3. Never use the theoretical tp_price/sl_price fields.
    """
    leg = (row.get("protection") or {}).get(kind) or {}
    if leg.get("status") != "confirmed":
        return None
    evidence = leg.get("evidence") or {}
    confirmed = ((evidence.get("confirm") or {}).get("order") or {})
    for order in (confirmed, evidence.get("ack") or {}, evidence.get("params") or {}):
        raw = order.get("stopPrice")
        try:
            number = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if number.is_finite() and number > 0:
            return number
    return None


def _confirmed_stop_price(row: dict, kind: str) -> str:
    return _fmt_price(_confirmed_stop_value(row, kind))


def signal_message(sig: dict) -> str:
    """Signal-only mode. A real trading run never emits this provisional note."""
    return "\n".join([
        f"📡 **{_symbol_label(sig['symbol'])} {sig['side']} · {sig['combo']} | TÍN HIỆU**",
        f"Entry {_fmt_price(sig.get('entry'))} · TP {_fmt_price(sig.get('tp'))} · SL {_fmt_price(sig.get('sl'))}",
        f"🕒 {vn_clock(sig.get('close_time_ms')) if sig.get('close_time_ms') else '?'}",
    ])


def success_message(r: dict) -> str:
    """One compact message from actual BingX fill and confirmed stop orders."""
    return "\n".join([
        f"✅ **{_symbol_label(r['symbol'])} {r['side']} · {r['combo']} | ĐẶT LỆNH THÀNH CÔNG**",
        f"Entry **{_fmt_price(r.get('avg_price'))}** · TP **{_confirmed_stop_price(r, 'tp')}** · SL **{_confirmed_stop_price(r, 'sl')}**",
        f"Khối lượng: **{_fmt(r.get('executed_qty'))}**",
        "✅ Entry đã khớp — TP/SL đã xác nhận",
        f"🕒 {vn_clock(r.get('confirmed_at_ms'))}",
    ])


LEG_LABELS = {"sl": "SL", "tp": "TP", "trailing": "Trailing"}


def closed_message(r: dict) -> str:
    """Compact, price-rounded close summary; quantities retain their precision."""
    left = r.get("leftover") or {}
    names = ", ".join(LEG_LABELS.get(k, k) for k in left.get("legs") or [])
    if left.get("mode") == "shadow" and names:
        cleanup = f"🧹 Còn lệnh dư: {names} (chế độ shadow, chưa hủy)"
    elif names:
        cleanup = f"🧹 Đã dọn lệnh dư: {names}"
    else:
        cleanup = "🧹 Không còn lệnh dư"
    lines = [
        f"🏁 **{_symbol_label(r['symbol'])} {r['side']} · {r['combo']} | ĐÃ ĐÓNG**",
        f"Entry **{_fmt_price(r.get('avg_price'))}** · KL **{_fmt(r.get('entry_qty'))}**",
    ]
    for fill in r.get("filled") or []:
        name = LEG_LABELS.get(fill["kind"], fill["kind"])
        lines.append(f"{name}: **{_fmt(fill.get('qty'))} @ {_fmt_price(fill.get('price'))}**")
    lines.append(f"{cleanup} · 🕒 {vn_clock(r.get('closed_at_ms'))}")
    return "\n".join(lines)


def error_message(rec: dict, log_id: int | None, repeats: int = 0) -> str:
    layer = str(rec.get("layer") or "?")
    lines = [
        f"🚨 **LỖI {LAYER_NAMES.get(layer, layer)}** — bước `{_fmt(rec.get('step'))}`",
        f"Nguyên nhân: {clean_text(rec.get('message'), 900)}",
    ]
    for label, key in (("Symbol", "symbol"), ("Event ID", "event_id"), ("Order ID", "order_id"), ("Mã lỗi", "error_code")):
        if rec.get(key):
            lines.append(f"{label}: `{_fmt(rec.get(key))}`")
    if repeats:
        lines.append(f"Đã gộp {repeats} cảnh báo cùng bước và mã lỗi trong giờ trước.")
    lines.append(f"control_logs id: {log_id if log_id is not None else 'không ghi được DB'}")
    return "\n".join(lines)



# Discord presentation only. Keep old string formatters for injected legacy senders.
BLUE, GREEN, RED, YELLOW, ORANGE, GREY = (
    0x3498DB, 0x23A559, 0xED4245, 0xF0B232, 0xE67E22, 0x949BA4,
)


def _decimal(value: Any) -> Decimal | None:
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _positive(value: Any) -> Decimal | None:
    n = _decimal(value)
    return n if n is not None and n > 0 else None


def _money(value: Decimal | None) -> str:
    if value is None:
        return "Chưa có dữ liệu"
    return f"{value:+.1f} USDT"


def _qty(value: Any, symbol: Any) -> str:
    n = _positive(value)
    return f"{format(n, 'f')} {_symbol_label(symbol)}" if n is not None else "Chưa xác minh"


def _title(row: dict, status: str) -> str:
    return clean_text(f"{_symbol_label(row.get('symbol'))}/USDT · {row.get('combo', '?')} · "
                      f"{row.get('side', '?')} | {status}", 250)


def _field(name: str, value: Any, inline: bool = True) -> dict:
    return {"name": name, "value": clean_text(value, 1000) or "Chưa có dữ liệu", "inline": inline}


def _footer(ms: Any) -> dict:
    try:
        value = vn_clock(ms) if ms else "Chưa có thời điểm xác nhận"
    except (ValueError, TypeError, OverflowError, OSError):
        value = "Chưa có thời điểm xác nhận"
    return {"text": value + " · giờ VN"}


def _embed(title: str, color: int, fields: list[dict], *, description: str = "", footer=None) -> dict:
    item = {"title": clean_text(title, 250), "color": color, "fields": fields[:12]}
    if description:
        item["description"] = clean_text(description, 1500)
    if footer:
        item["footer"] = footer
    # Discord's aggregate embed text limit is 6000 characters. All our bounded
    # fields are short except incident diagnostics, which are also capped.
    return {"embeds": [item]}


def success_embed(r: dict) -> dict:
    tp = _confirmed_stop_value(r, "tp")
    sl = _confirmed_stop_value(r, "sl")
    verified = all((r.get("protection") or {}).get(k, {}).get("status") == "confirmed" for k in ("tp", "sl"))
    status = "MỞ LỆNH" if verified else "BẢO VỆ CHƯA XÁC MINH"
    description = "\n".join([
        f"Entry **{_fmt_price(r.get('avg_price'))}** · KL **{_qty(r.get('executed_qty'), r.get('symbol'))}**",
        f"TP **{_fmt_price(tp)}** · SL **{_fmt_price(sl)}**",
        "✅ Đã khớp · TP/SL xác nhận" if verified else "⚠️ Kiểm tra TP/SL trên BingX",
    ])
    return _embed(_title(r, status), BLUE if verified else ORANGE, [],
                  description=description, footer=_footer(r.get("confirmed_at_ms")))


def _duration(r: dict) -> str:
    # L4 close notification time is detection time, not exchange fill time.
    try:
        value = r.get("filled_at")
        start = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if start.tzinfo is None or not r.get("closed_at_ms"):
            return "Chưa có dữ liệu"
        seconds = int(int(r["closed_at_ms"]) / 1000 - start.timestamp())
        if seconds < 0:
            return "Chưa có dữ liệu"
        hours, rem = divmod(seconds, 3600)
        mins = rem // 60
        return f"~{hours}h {mins:02d}m" if hours else f"~{mins}m"
    except (ValueError, TypeError, OverflowError, OSError):
        return "Chưa có dữ liệu"


def _finance_ready(row: dict, finance: dict | None) -> bool:
    """No notification from a partial, synthetic-zero or unverified projection."""
    if not finance or finance.get("is_fully_closed") is not True:
        return False
    raw = finance.get("raw") or {}
    if raw.get("discord_finance_verified") is not True:
        return False
    qty = _positive(row.get("entry_qty"))
    closed = _positive(finance.get("closed_qty"))
    if qty is None or closed is None or abs(qty - closed) > Decimal("0.000000000001"):
        return False
    fills = [(_positive(f.get("qty")), _positive(f.get("price"))) for f in row.get("filled") or []]
    if not fills or not all(q is not None and p is not None for q, p in fills):
        return False
    if sum((q for q, _ in fills), Decimal(0)) != qty or _positive(row.get("avg_price")) is None:
        return False
    return all(_decimal(finance.get(k)) is not None for k in
               ("net_pnl", "roi_pct", "open_fee", "close_fee", "gross_pnl"))


def closed_embed(r: dict, finance: dict) -> dict:
    if not _finance_ready(r, finance):
        raise ValueError("close finance not reconciled")
    entry = _positive(r.get("avg_price"))
    qty = _positive(r.get("entry_qty"))
    fills = r.get("filled") or []
    kinds = {f.get("kind") for f in fills}
    valid = [(_positive(f.get("qty")), _positive(f.get("price"))) for f in fills]
    total = sum((q for q, _ in valid), Decimal(0))
    exit_price = sum((q * p for q, p in valid), Decimal(0)) / total
    status, color = ("SL", RED) if kinds == {"sl"} else ("TP", GREEN) if kinds == {"tp"} else ("ĐÃ ĐÓNG", GREY)
    left = r.get("leftover") or {}
    names = ", ".join(LEG_LABELS.get(k, k) for k in left.get("legs") or [])
    if left.get("mode") == "shadow" and names:
        cleanup = f"⚠️ Còn lệnh dư: {names} (shadow)"
        color = ORANGE
    elif left.get("verified") is True:
        cleanup = "✅ Đã đóng · Không còn lệnh dư"
    else:
        cleanup = "⚠️ Chưa xác minh dọn lệnh"
    net = _decimal(finance.get("net_pnl"))
    roi = _decimal(finance.get("roi_pct"))
    lines = [
        f"**{_money(net)}** · ROI **{roi:+.2f}%**",
        f"{_fmt_price(entry)} → {_fmt_price(exit_price)} · KL {_qty(qty, r.get('symbol'))}",
        f"Giữ lệnh {_duration(r)} · {cleanup}",
    ]
    if (finance.get("raw") or {}).get("source") == "BingX exact close orders; logical shared-position allocation":
        lines.append("PnL phân bổ theo vị thế gộp.")
    return _embed(_title(r, status), color, [], description="\n".join(lines),
                  footer=_footer(r.get("closed_at_ms")))


def signal_embed(sig: dict) -> dict:
    return _embed(_title(sig, "TÍN HIỆU · CHƯA KHỚP"), GREY, [
        _field("Entry dự kiến", _fmt_price(sig.get("entry"))),
        _field("Take Profit dự kiến", _fmt_price(sig.get("tp"))),
        _field("Stop Loss dự kiến", _fmt_price(sig.get("sl"))),
    ], footer=_footer(sig.get("close_time_ms")))


def error_embed(rec: dict, log_id: int | None, repeats: int = 0) -> dict:
    layer = str(rec.get("layer") or "?")
    fields = [_field("Bước", _fmt(rec.get("step")))]
    for label, key in (("Symbol", "symbol"), ("Mã lỗi", "error_code"), ("Event ID", "event_id"), ("Order ID", "order_id")):
        if rec.get(key):
            fields.append(_field(label, _fmt(rec[key]), key not in ("event_id", "order_id")))
    if repeats:
        fields.append(_field("Cảnh báo đã gộp", str(repeats)))
    fields.append(_field("control_logs ID", str(log_id) if log_id is not None else "Không ghi được DB"))
    return _embed(f"LỖI {LAYER_NAMES.get(layer, layer)}", YELLOW if layer in ("L1", "L2") else ORANGE,
                  fields, description=clean_text(rec.get("message"), 900))



class L5Alert:
    def __init__(self, *, enabled: bool, webhook_btc: str, webhook_eth: str, webhook_error: str,
                 store=None, sender: Callable[[str, str], tuple[bool, str]] | None = None, synchronous: bool = False,
                 error_window_sec: float = 3600.0, clock: Callable[[], float] = time.monotonic):
        self.enabled = bool(enabled)
        self.error_window_sec = float(error_window_sec)
        self.clock = clock
        self._error_seen: dict[tuple, dict] = {}  # (layer, step, symbol, error_code) -> {"at", "suppressed"}
        self.webhooks = {"BTC": webhook_btc or "", "ETH": webhook_eth or "", "ERROR": webhook_error or ""}
        self.store = store
        self.sender = sender or default_sender
        # Existing injected senders accept text; production uses JSON embeds.
        self._use_embeds = sender is None or sender is default_sender
        self.synchronous = synchronous
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._sequence = count()
        self._submit_lock = threading.Lock()
        self._trade_seen: OrderedDict[tuple, None] = OrderedDict()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.sent: list[dict] = []        # last delivery results (bounded), for /status and tests
        self.failures = 0

    # ---- routing -------------------------------------------------------
    @staticmethod
    def channel_for_symbol(symbol: str) -> str:
        s = str(symbol or "").upper()
        if s.startswith("BTC"):
            return "BTC"
        if s.startswith("ETH"):
            return "ETH"
        return "ERROR"

    def signal(self, sig: dict) -> None:
        self._submit(self.channel_for_symbol(sig["symbol"]), (signal_embed(sig) if self._use_embeds else signal_message(sig)), kind="signal", ref=sig["event_id"])

    def trade_success(self, row: dict) -> None:
        # Capture confirmation when L3 calls L5, never when HTTP sends/retries.
        if not row.get("confirmed_at_ms"):
            row = {**row, "confirmed_at_ms": int(time.time() * 1000)}
        self._submit(self.channel_for_symbol(row["symbol"]), (success_embed(row) if self._use_embeds else success_message(row)), kind="success", ref=row["event_id"])

    @property
    def requires_close_reconciliation(self) -> bool:
        return self._use_embeds

    def trade_closed(self, row: dict, finance: dict | None = None) -> None:
        if self._use_embeds:
            if not _finance_ready(row, finance):
                return
            self._submit(self.channel_for_symbol(row["symbol"]), closed_embed(row, finance),
                         kind="closed", ref=row["event_id"], close_row=dict(row))
        else:
            self._submit(self.channel_for_symbol(row["symbol"]), closed_message(row),
                         kind="closed", ref=row["event_id"])

    def flush_reconciled(self, blocked_events=()) -> None:
        """Called after the existing L4 hourly accounting pass, never from L3."""
        if not self._use_embeds or not self.enabled or self.store is None:
            return
        blocked = set(blocked_events)
        try:
            for row in self.store.pending_discord_closes():
                if row.get("event_id") in blocked:
                    continue
                try:
                    self.trade_closed(row, self.store.finance_for_discord(row["event_id"]))
                except Exception as exc:
                    log.warning("V3_L5_FINANCE_READ_FAILED error_class=%s", type(exc).__name__)
        except Exception as exc:
            log.warning("V3_L5_PENDING_CLOSE_READ_FAILED error_class=%s", type(exc).__name__)

    def notice(self, title: str, lines: list[str]) -> None:
        """Startup/status notice to the ERROR channel. Carries no secrets."""
        content = "\n".join([f"ℹ️ **{clean_text(title, 120)}**"] + [clean_text(x, 300) for x in lines])
        if self._use_embeds:
            content = _embed(title, GREY, [], description="\n".join(clean_text(x, 300) for x in lines[:5]))
        self._submit("ERROR", content, kind="notice", ref=None)

    def error(self, rec: dict, log_id: int | None) -> None:
        """Send an error at once; repeats of the same step and error code are merged for one window.

        The first occurrence is sent immediately. Later repeats are counted and not sent. The next
        occurrence after the window is sent with the merged count. A changed error code is a new key
        and is sent at once. The control log keeps every row either way.
        """
        key = (rec.get("layer"), rec.get("step"), rec.get("symbol"), rec.get("error_code"))
        now = float(self.clock())
        seen = self._error_seen.get(key)
        if seen is not None and now - seen["at"] < self.error_window_sec:
            seen["suppressed"] += 1
            return
        repeats = seen["suppressed"] if seen is not None else 0
        self._error_seen[key] = {"at": now, "suppressed": 0}
        if len(self._error_seen) > 500:  # bounded: drop expired windows
            for k in [k for k, v in self._error_seen.items() if now - v["at"] >= self.error_window_sec]:
                del self._error_seen[k]
        self._submit("ERROR", (error_embed(rec, log_id, repeats) if self._use_embeds else error_message(rec, log_id, repeats)), kind="error", ref=rec.get("event_id"), log_id=log_id)

    # ---- delivery ------------------------------------------------------
    def _submit(self, channel: str, content: str | dict, *, kind: str, ref=None, log_id: int | None = None, close_row: dict | None = None) -> None:
        item = {"channel": channel, "content": content, "kind": kind, "ref": ref, "log_id": log_id, "close_row": close_row}
        try:
            with self._submit_lock:
                if kind in ("success", "closed") and ref:
                    key = (kind, ref)
                    if key in self._trade_seen:
                        return
                    self._trade_seen[key] = None
                    if len(self._trade_seen) > 10000:
                        self._trade_seen.popitem(last=False)
                if not self.synchronous:
                    self._ensure_thread()
                    priority = 0 if kind == "error" else 1
                    self._queue.put_nowait((priority, next(self._sequence), item))
                    return
            self._deliver(item)
        except Exception as exc:  # never raise into trading
            log.error("V3_L5_SUBMIT_FAILED error_class=%s", type(exc).__name__)

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._worker, name="v3-l5-discord", daemon=True)
            self._thread.start()

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                _, _, item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._deliver(item)
            finally:
                self._queue.task_done()

    def drain(self, timeout: float = 10.0) -> None:
        end = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.01)

    def stop(self) -> None:
        self._stop.set()

    def _deliver(self, item: dict) -> None:
        url = self.webhooks.get(item["channel"]) or ""
        if not self.enabled or not url:
            status, ok, detail = "disabled", False, "Discord disabled or webhook not configured"
        else:
            try:
                content = item["content"]
                ok, detail = self.sender(url, content)
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}: {clean_text(exc, 200)}"
            status = "sent" if ok else "failed"
        if item.get("close_row") is not None and self.store is not None:
            if status == "sent":
                try:
                    self.store.mark_discord_close_sent(item["ref"])
                except Exception as exc:
                    log.error("V3_L5_CLOSE_ACK_FAILED error_class=%s", type(exc).__name__)
            else:
                # Keep the durable pending record for a later accounting cycle.
                with self._submit_lock:
                    self._trade_seen.pop(("closed", item["ref"]), None)
        self.sent.append({"channel": item["channel"], "kind": item["kind"], "ref": item["ref"],
                          "status": status, "detail": detail, "at": int(time.time() * 1000)})
        del self.sent[:-200]
        if item.get("log_id") is not None and self.store is not None:
            try:
                self.store.set_discord_status(item["log_id"], status)
            except Exception as exc:
                log.error("V3_L5_STATUS_UPDATE_FAILED log_id=%s error_class=%s", item["log_id"], type(exc).__name__)
        if status == "failed":
            self.failures += 1
            log.error("V3_L5_DISCORD_FAILED channel=%s kind=%s detail=%s", item["channel"], item["kind"], detail)
            # Record the Discord failure itself. Never alert it again (no loop).
            if self.store is not None:
                try:
                    self.store.insert_control_log({
                        "layer": "L5", "step": "discord_send", "error_code": "DISCORD_SEND_FAILED",
                        "message": f"Gửi Discord kênh {item['channel']} thất bại: {detail}",
                        "event_id": item.get("ref"), "details": {"kind": item["kind"], "channel": item["channel"],
                                                                 "source_log_id": item.get("log_id")},
                        "discord_status": "failed"})
                except Exception as exc:
                    log.error("V3_L5_FAILURE_LOG_FAILED error_class=%s", type(exc).__name__)
