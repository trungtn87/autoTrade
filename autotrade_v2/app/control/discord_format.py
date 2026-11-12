"""Vietnamese, action-oriented Discord summaries (presentation only).

Never reinterpret exchange state from text: "unverified" is NOT "absent".
Raw errors/IDs remain in the durable system_events/discord_trade_outbox records.
"""
from __future__ import annotations

import re


RISK_NAMES = {
    "NO_VERIFIED_ACTIVE_SL": "Chưa xác minh được Stop Loss (SL)",
    "NO_VERIFIED_ACTIVE_TP": "Chưa xác minh được Take Profit (TP)",
    "NO_VERIFIED_ACTIVE_TRAILING": "Chưa xác minh được trailing stop",
    "PROTECTION_SUBMISSION_UNRESOLVED": "Chưa rõ kết quả gửi lệnh bảo vệ lên BingX",
    "PROTECTION_UNVERIFIED": "Chưa xác minh đủ lệnh bảo vệ",
    "UNOWNED_EXIT_ORDER": "Có lệnh thoát chưa xác định được combo sở hữu",
    "EXIT_HISTORY_UNVERIFIED": "Chưa đối chiếu được lịch sử đóng lệnh",
    "POSITION_LEDGER_MISMATCH": "Khối lượng BingX và database chưa khớp",
    "EXECUTABLE_EXIT_BUDGET_EXCEEDED": "Tổng khối lượng lệnh thoát có thể vượt vị thế",
    "SL_QUANTITY_MISMATCH": "Khối lượng SL không khớp vị thế còn lại",
    "UNTRACKED_ENTRY_ORDER": "Có lệnh Entry chưa được ghi nhận",
    "UNTRACKED_POSITION": "Có vị thế BingX chưa được ghi nhận",
    "UNOWNED_OPEN_ORDER": "Có lệnh mở chưa xác định chủ sở hữu",
    "ABORT_IN_PROGRESS": "Đang xử lý đóng khẩn cấp",
    "ENTRY_UNVERIFIED": "Chưa xác minh Entry có khớp hay chưa",
    "PROTECTION_CHANGED_DURING_SETUP": "Lệnh bảo vệ thay đổi trong lúc thiết lập",
}
_PREFIXES = (
    "NO_VERIFIED_ACTIVE_", "PROTECTION_", "UNOWNED_", "EXIT_",
    "POSITION_", "SL_", "UNTRACKED_", "ABORT_", "ENTRY_",
    "EXECUTABLE_",
)


def risk_codes(*sources) -> list[str]:
    """Extract known risk suffixes from a list or legacy 'event_id:CODE|...' string."""
    found: list[str] = []
    for source in sources:
        items = source if isinstance(source, (list, tuple, set)) else [source]
        for item in items:
            for segment in str(item or "").split("|"):
                candidate = segment.rsplit(":", 1)[-1].strip()
                if candidate in RISK_NAMES and candidate not in found:
                    found.append(candidate)
    return found


def risk_lines(*sources, maximum=4) -> list[str]:
    codes = risk_codes(*sources)
    lines = [f"• {RISK_NAMES[code]}" for code in codes[:maximum]]
    if len(codes) > maximum:
        lines.append(f"• Và {len(codes) - maximum} vấn đề khác")
    return lines


def trade_status(data: dict) -> tuple[str, str, str]:
    """Title, verified status and next action for a *final notification event*."""
    key = str(data.get("event_key") or "")
    stage = str(data.get("stage") or "")
    if key == "L3.EXEC.ORDER_COMPLETE":
        return ("✅ ĐẶT LỆNH HOÀN TẤT",
                "Entry, SL và TP đã được xác minh trên BingX.",
                "Không cần thao tác.")
    if stage in {"protection_setup_aborted_closed", "force_close_confirmed",
                 "protection_sl_failed_closed", "partial_fill_closed",
                 "fill_outside_final14_range_closed"}:
        return ("⚠️ ĐÃ ĐÓNG KHẨN CẤP",
                "Hệ thống báo đã xác nhận đóng vị thế để hạn chế rủi ro.",
                "Kiểm tra vị thế và lệnh thoát còn treo trên BingX.")
    if stage in {"entry_absent_reconciled", "entry_not_filled_terminal"}:
        return ("ℹ️ ENTRY KHÔNG KHỚP",
                "Không xác nhận được vị thế mới từ tín hiệu này.",
                "Không đặt lệnh lại nếu chưa đối chiếu BingX.")
    if data.get("entry_filled") or data.get("entry_accepted"):
        if data.get("sl_protected") and stage in {
            "protection_tp_reconcile_pending", "protection_tp_failed_sl_active",
            "protection_trailing_reconcile_pending", "protection_trailing_failed_sl_active",
        }:
            detail = ("SL đã xác minh; TP chưa xác minh."
                      if "trailing" not in stage
                      else "SL đã xác minh; trailing chưa xác minh.")
        else:
            detail = "Entry đã khớp nhưng trạng thái SL/TP chưa được xác minh đầy đủ."
        return ("🚨 ENTRY ĐÃ KHỚP · BẢO VỆ CHƯA HOÀN TẤT", detail,
                "Kiểm tra vị thế, SL, TP trên BingX; không đặt Entry hoặc SL trùng.")
    return ("❌ KHÔNG ĐẶT ĐƯỢC ENTRY",
            "Không có xác nhận Entry mới cho tín hiệu này.",
            "Kiểm tra BingX và lý do trong nhật ký trước khi thử lại.")


def incident_view(key: str, message: str, details: dict) -> tuple[str, str, str, list[str]]:
    """Human-facing title, meaning, action and bounded risks for an operational event."""
    codes = risk_codes(details.get("risks"), message)
    risks = risk_lines(details.get("risks"), message)
    if key == "L3.EXEC.MERGED_RECONCILE_REQUIRED":
        return ("⛔ ENTRY TẠM CHẶN",
                "Entry chưa được gửi vì BingX và database chưa khớp bằng chứng bảo vệ vị thế.",
                "Không mở thêm lệnh cùng chiều; kiểm tra SL/TP và các vị thế đang mở.", risks)
    if key == "L3.EXEC.EXIT_CLEANUP_RISK":
        if any(code in codes for code in (
            "NO_VERIFIED_ACTIVE_SL", "NO_VERIFIED_ACTIVE_TP",
            "PROTECTION_UNVERIFIED", "PROTECTION_SUBMISSION_UNRESOLVED",
        )):
            return ("🚨 VỊ THẾ CHƯA ĐƯỢC XÁC MINH BẢO VỆ",
                    "Hệ thống chưa xác minh đầy đủ SL/TP; không có nghĩa BingX chắc chắn thiếu SL.",
                    "Kiểm tra SL/TP trên BingX. Không hủy lệnh thoát chưa rõ chủ sở hữu.", risks)
        return ("🚨 ĐỐI SOÁT LỆNH THOÁT CÓ RỦI RO",
                "Chưa đủ bằng chứng để tự động dọn lệnh an toàn.",
                "Kiểm tra vị thế và các lệnh thoát BingX trước khi xử lý.", risks)
    if key in {"L3.EXEC.FORCE_CLOSE_UNRESOLVED", "L3.EXEC.UNSAFE_OPEN_POSITION",
               "L3.EXEC.RECOVERY_EXHAUSTED", "L3.EXEC.RECONCILIATION_STUCK"}:
        return ("🚨 CẦN KIỂM TRA VỊ THẾ NGAY",
                "Đóng khẩn cấp hoặc đối soát chưa được xác nhận hoàn tất.",
                "Kiểm tra vị thế BingX và lệnh TP/SL; ưu tiên an toàn tài khoản.", risks)
    if key in {"L3.EXEC.FORCE_CLOSE_CONFIRMED", "L3.EXEC.EMERGENCY_CLOSE"}:
        return ("⚠️ ĐÃ KÍCH HOẠT ĐÓNG KHẨN CẤP",
                "Hệ thống báo đã xử lý đóng vì rủi ro bảo vệ.",
                "Kiểm tra vị thế còn lại và các lệnh TP/SL trên BingX.", risks)
    if key in {"L1.DATA.CANDLE_STALE", "L1.DATA.RECOVERY_FAIL"}:
        return ("⚠️ DỮ LIỆU NẾN CHẬM",
                "Dữ liệu thị trường chưa cập nhật hoặc tự phục hồi thất bại.",
                "Theo dõi khôi phục dữ liệu; không suy diễn có lệnh đã vào.", risks)
    if key in {"L3.EXEC.PREFLIGHT_BLOCK", "L3.EXEC.PREFLIGHT_FAIL",
               "L3.EXEC.PREFLIGHT_STILL_BLOCKED"}:
        error = str(details.get("error") or message or "")
        lowered = error.lower()
        if "timeout" in lowered or "timed out" in lowered:
            cause = "Kết nối API BingX bị quá thời gian (SSL/TLS hoặc mạng)."
        elif "missing bingx_api" in lowered:
            cause = "Thiếu cấu hình khóa API BingX."
        elif any(marker in lowered for marker in ("401", "unauthorized", "invalid api", "signature")):
            cause = "BingX có thể từ chối xác thực API; cần đối chiếu mã lỗi."
        elif "429" in lowered or "418" in lowered or "rate limit" in lowered:
            cause = "BingX đang giới hạn hoặc tạm chặn yêu cầu API."
        elif "recovery failed" in lowered:
            cause = "Bước khôi phục trạng thái lệnh chưa hoàn tất."
        else:
            cause = "Chưa xác minh được quyền kết nối API BingX."
        return ("🚨 BOT TẠM DỪNG ENTRY",
                cause,
                "Bot sẽ tự kiểm tra lại; xem trạng thái API và log lỗi trước khi can thiệp.", risks)
    if key == "L3.EXEC.PREFLIGHT_RECOVERED":
        return ("✅ ĐÃ KHÔI PHỤC KHẢ NĂNG ĐẶT LỆNH",
                "BingX đã xác minh API và hoàn thành bước khôi phục lệnh tồn đọng.",
                "Chỉ nhận tín hiệu mới; không vào lại lệnh cũ.", risks)
    if key == "L3.EXEC.POSITION_PREFLIGHT_BLOCK":
        return ("⛔ TẠM CHẶN VÀO LỆNH",
                "Chưa có đủ bằng chứng về vị thế/lệnh bảo vệ hiện tại trên BingX.",
                "Đối soát vị thế và lệnh bảo vệ trước khi mở Entry mới.", risks)
    if key in {"L3.EXEC.EXCHANGE_FLAT_RECONCILED", "L3.EXEC.RECONCILE_CLEARED"}:
        return ("✅ ĐÃ ĐỐI SOÁT XONG",
                "BingX đã được xác minh không còn vị thế liên quan.",
                "Không cần thao tác nếu BingX vẫn không có vị thế.", risks)
    return ("⚠️ CẢNH BÁO HỆ THỐNG",
            "AutoTrade phát hiện sự cố cần kiểm tra; nội dung kỹ thuật đã lưu ở database.",
            "Kiểm tra trạng thái AutoTrade và sự kiện tương ứng trong system_events.", risks)


def safe_line(value, maximum=120) -> str:
    return re.sub(r"\s+", " ", str(value or "-")).replace("@", "＠")[:maximum]
