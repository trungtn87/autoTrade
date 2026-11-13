"""Vietnamese Discord alert formatting and a single-shot L5 router (not wired yet).

Router sends never retry, never raise on delivery errors and never expose the
webhook URL in return values or logs.
"""
from __future__ import annotations

import re
from typing import Any, Callable


WEBHOOK_ENV = {
    "BTC": "DISCORD_WEBHOOK_BTC",
    "ETH": "DISCORD_WEBHOOK_ETH",
    "ERROR": "DISCORD_WEBHOOK_ERROR",
}
SENT = "sent"
FAILED = "failed"
SKIPPED = "skipped"
_SYMBOL_CHANNEL = {"BTC-USDT": "BTC", "ETH-USDT": "ETH"}
_URL_PATTERN = re.compile(r"https?://\S+")
_ENTRY_STATUSES = {"SUCCESS", "FAILED", "UNKNOWN"}


def _safe(value: Any) -> str:
    return _URL_PATTERN.sub("[URL]", str(value))


class DiscordRouter:
    def __init__(self, webhooks: dict | None, post_fn: Callable[[str, str], None]):
        self._webhooks = dict(webhooks or {})
        self._post_fn = post_fn

    def send(self, channel: str, content: str) -> str:
        if channel not in WEBHOOK_ENV:
            raise ValueError(f"unknown discord channel: {channel!r}")
        url = self._webhooks.get(channel)
        if not url:
            return SKIPPED
        try:
            self._post_fn(url, content)
        except Exception:
            return FAILED
        return SENT


def route_for_symbol(symbol: str) -> str:
    channel = _SYMBOL_CHANNEL.get(symbol)
    if channel is None:
        raise ValueError(f"no discord channel for symbol: {symbol!r}")
    return channel


def format_signal(symbol, combo, side, signal_time, entry, tp, sl, event_id) -> str:
    return "\n".join([
        "TÍN HIỆU — CHƯA ĐẶT LỆNH",
        f"Cặp: {_safe(symbol)}",
        f"Combo: {_safe(combo)}",
        f"Hướng: {_safe(side)}",
        f"Thời gian tín hiệu: {_safe(signal_time)}",
        f"Giá vào dự kiến: {_safe(entry)}",
        f"Take Profit (TP): {_safe(tp)}",
        f"Stop Loss (SL): {_safe(sl)}",
        f"Event ID: {_safe(event_id)}",
        "Trạng thái: chỉ là tín hiệu, hệ thống chưa gửi lệnh nào lên BingX.",
    ])


def format_entry_result(
    status, symbol, combo, side, event_id, order_id, avg_price, executed_qty,
    protections, failed_step, failed_reason,
) -> str:
    if status not in _ENTRY_STATUSES:
        raise ValueError(f"unknown entry status: {status!r}")
    header = [
        f"Cặp: {_safe(symbol)}",
        f"Combo: {_safe(combo)}",
        f"Hướng: {_safe(side)}",
        f"Event ID: {_safe(event_id)}",
    ]
    if status == "SUCCESS":
        lines = ["ĐẶT LỆNH THÀNH CÔNG", *header,
                 f"Entry order id: {_safe(order_id)}",
                 f"Giá khớp trung bình: {_safe(avg_price)}",
                 f"Khối lượng khớp: {_safe(executed_qty)}"]
        protections = protections or {}
        if protections:
            lines.append("Bảo vệ:")
            lines += [f"- {_safe(k)}: {_safe(v)}" for k, v in protections.items()]
        else:
            lines.append("Bảo vệ: chưa có thông tin")
    elif status == "FAILED":
        lines = ["ĐẶT LỆNH THẤT BẠI", *header,
                 f"Bước lỗi: {_safe(failed_step or 'không rõ')}",
                 f"Lý do: {_safe(failed_reason or 'không rõ')}"]
        if order_id:
            lines.append(
                f"Lệnh Entry đã tồn tại với order id {_safe(order_id)}; "
                "cần đối chiếu BingX trước khi thao tác."
            )
        else:
            lines.append("Không có order id Entry được ghi nhận.")
    else:
        lines = ["CHƯA XÁC ĐỊNH KẾT QUẢ ĐẶT LỆNH", *header,
                 f"Bước: {_safe(failed_step or 'không rõ')}",
                 f"Lý do: {_safe(failed_reason or 'không rõ')}"]
        if order_id:
            lines.append(f"Entry order id: {_safe(order_id)}")
        lines.append("Chưa xác định được trạng thái lệnh trên BingX; "
                     "đối chiếu xong mới thao tác tiếp.")
    return "\n".join(lines)
