"""L5 ALERT: brand-new Discord notifier with exactly three webhooks.

Routing (Owner decision, Issue #108):
  L2 signal BTC  -> DISCORD_WEBHOOK_BTC      L2 signal ETH  -> DISCORD_WEBHOOK_ETH
  L3 success BTC -> DISCORD_WEBHOOK_BTC      L3 success ETH -> DISCORD_WEBHOOK_ETH
  any error from L1..L5 -> DISCORD_WEBHOOK_ERROR

No outbox, no batching, no cooldown, no dedupe, no recovery alerts. Each event
is one message. Sending happens on a background thread so trading never waits
for Discord; a Discord failure is written to control_logs (layer L5) and is
never re-alerted, so it cannot loop.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
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


def default_sender(url: str, content: str) -> tuple[bool, str]:
    """One POST; a Discord 429 with a short Retry-After is honoured once."""
    payload = {"content": content[:1990], "allowed_mentions": {"parse": []}}
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


def signal_message(sig: dict) -> str:
    return "\n".join([
        f"📡 **TÍN HIỆU 20FINAL** — {sig['symbol']} {sig['combo']} **{sig['side']}**",
        "(Đây là tín hiệu chiến lược, CHƯA phải xác nhận đặt lệnh)",
        f"Event ID: `{sig['event_id']}`",
        f"Nến đóng: {vn_time(sig.get('close_time_ms'))} · khung {sig.get('timeframe', '?')}",
        f"Entry lý thuyết: {_fmt(sig['entry'])} · TP: {_fmt(sig['tp'])} · SL: {_fmt(sig['sl'])}",
    ])


def success_message(r: dict) -> str:
    lines = [
        f"✅ **ĐẶT LỆNH THÀNH CÔNG** — {r['symbol']} {r['combo']} **{r['side']}**",
        "Entry và các lệnh bảo vệ đã được BingX xác nhận.",
        f"Event ID: `{r['event_id']}`",
        f"Entry Order ID: `{_fmt(r.get('entry_order_id'))}` · Client Order ID: `{_fmt(r.get('client_order_id'))}`",
        f"Giá khớp thực tế: {_fmt(r.get('avg_price'))} · Khối lượng khớp: {_fmt(r.get('executed_qty'))} · x{_fmt(r.get('leverage'))}",
        f"SL: `{_fmt(r.get('sl_order_id'))}` @ {_fmt(r.get('sl_price'))} (KL {_fmt(r.get('sl_qty'))})",
        f"TP: `{_fmt(r.get('tp_order_id'))}` @ {_fmt(r.get('tp_price'))} (KL {_fmt(r.get('tp_qty'))})",
    ]
    if r.get("trailing_order_id"):
        lines.append(f"Trailing: `{_fmt(r.get('trailing_order_id'))}` kích hoạt {_fmt(r.get('trailing_activation_price'))}"
                     f" · callback {_fmt(r.get('trailing_callback_rate'))} (KL {_fmt(r.get('trailing_qty'))})")
    else:
        lines.append(f"Chế độ thoát: {_fmt(r.get('exit_mode'))} (không có trailing)")
    return "\n".join(lines)


def error_message(rec: dict, log_id: int | None) -> str:
    layer = str(rec.get("layer") or "?")
    lines = [
        f"🚨 **LỖI {LAYER_NAMES.get(layer, layer)}** — bước `{_fmt(rec.get('step'))}`",
        f"Nguyên nhân: {clean_text(rec.get('message'), 900)}",
    ]
    for label, key in (("Symbol", "symbol"), ("Event ID", "event_id"), ("Order ID", "order_id"), ("Mã lỗi", "error_code")):
        if rec.get(key):
            lines.append(f"{label}: `{_fmt(rec.get(key))}`")
    lines.append(f"control_logs id: {log_id if log_id is not None else 'không ghi được DB'}")
    return "\n".join(lines)


class L5Alert:
    def __init__(self, *, enabled: bool, webhook_btc: str, webhook_eth: str, webhook_error: str,
                 store=None, sender: Callable[[str, str], tuple[bool, str]] | None = None, synchronous: bool = False):
        self.enabled = bool(enabled)
        self.webhooks = {"BTC": webhook_btc or "", "ETH": webhook_eth or "", "ERROR": webhook_error or ""}
        self.store = store
        self.sender = sender or default_sender
        self.synchronous = synchronous
        self._queue: queue.Queue = queue.Queue()
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
        self._submit(self.channel_for_symbol(sig["symbol"]), signal_message(sig), kind="signal", ref=sig["event_id"])

    def trade_success(self, row: dict) -> None:
        self._submit(self.channel_for_symbol(row["symbol"]), success_message(row), kind="success", ref=row["event_id"])

    def error(self, rec: dict, log_id: int | None) -> None:
        self._submit("ERROR", error_message(rec, log_id), kind="error", ref=rec.get("event_id"), log_id=log_id)

    # ---- delivery ------------------------------------------------------
    def _submit(self, channel: str, content: str, *, kind: str, ref=None, log_id: int | None = None) -> None:
        item = {"channel": channel, "content": content, "kind": kind, "ref": ref, "log_id": log_id}
        try:
            if self.synchronous:
                self._deliver(item)
            else:
                self._ensure_thread()
                self._queue.put_nowait(item)
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
                item = self._queue.get(timeout=1.0)
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
                ok, detail = self.sender(url, item["content"])
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}: {clean_text(exc, 200)}"
            status = "sent" if ok else "failed"
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
