"""Nhãn hiển thị tiếng Việt cho báo cáo đối soát V3-17a (tái sử dụng từ reports/labels_vi.py)."""
from __future__ import annotations

from autotrade_v3.reports.labels_vi import (
    FORBIDDEN_ENGLISH_LABELS,
    LABELS_VI as BASE_LABELS_VI,
    extract_visible_html_text,
    find_english_label_leaks as _base_find_leaks,
)

RECONCILE_LABELS_VI: dict[str, str] = {
    **BASE_LABELS_VI,
    "page_title": "Báo cáo đối soát Backtest vs Live (V3-17a)",
    "subtitle": "Đối soát dữ liệu thực tế từ kho local (chỉ đọc, không gọi API sàn/DB)",
    "small_sample_warning": "Cảnh báo: Mẫu nhỏ ({n_closed} lệnh đóng hẳn < {min_sample}), chưa kết luận.",
    "sec_sources": "Phạm vi dữ liệu & xác minh kho (SHA-256)",
    "sec_eras": "Phân tách kỷ nguyên cấu trúc thoát & lịch sử live",
    "sec_vps_off": "Cửa sổ VPS_TẮT suy ra từ nhật ký & nến",
    "sec_signal_reconcile": "Đối soát tín hiệu (Lab vs Live)",
    "sec_summary": "Tóm tắt đối soát tổng thể theo kỷ nguyên",
    "sec_causes": "Phân bố nguyên nhân sai lệch",
    "sec_system_issues": "Danh sách HỆ_THỐNG cần mở issue",
    "sec_orders_table": "Bảng đối soát từng lệnh (9 hạng mục / mỗi event_id)",
    "sec_tolerances": "Cấu hình dung sai đối soát",
    "sec_git_commits": "Danh sách commit/PR thay đổi autotrade_v3/live (chờ Owner xác nhận biên kỷ nguyên)",
    "era_1_name": "Kỷ nguyên 1: 50TP_50TRAIL (trước PR #125)",
    "era_2_name": "Kỷ nguyên 2: FIXED_TP_SL (sau PR #125)",
    "cost_base_label": "Mô phỏng BASE (mặc định: phí 0.05% + trượt giá 2 bps/chiều)",
    "cost_ideal_label": "Mô phỏng IDEAL (tham chiếu: phí 0.05% + trượt giá 0 bps)",
    "sig_matched": "Khớp chính xác",
    "sig_missing": "Thiếu (Lab có, Live không)",
    "sig_extra": "Thừa (Live có, Lab không)",
    "sig_vps_off": "Nằm trong cửa sổ VPS_TẮT (tách riêng)",
    "sig_time_skew": "Lệch thời gian",
    "col_event_id": "Mã sự kiện (event_id)",
    "col_era": "Kỷ nguyên",
    "col_signal": "1. Tín hiệu",
    "col_entry": "2. Giá vào & trượt giá",
    "col_tp_sl": "3. TP / SL",
    "col_trailing": "4. Trailing",
    "col_legs": "5. Từng leg & vị thế gộp",
    "col_fees": "6. Phí vào / ra",
    "col_funding": "7. Funding (ESTIMATED)",
    "col_pnl": "8. PnL Gộp / Ròng",
    "col_outcome": "9. Kết quả đóng",
    "col_cause": "Nguyên nhân",
    "col_provenance": "Cấp bằng chứng",
    "prov_EXACT": "EXACT (Chính xác)",
    "prov_RECONCILED": "RECONCILED (Đã đối chiếu)",
    "prov_ESTIMATED": "ESTIMATED (Ước lượng)",
    "prov_UNRESOLVED": "UNRESOLVED (Chưa giải quyết)",
    "cause_DATA": "DỮ_LIỆU",
    "cause_STRATEGY": "CHIẾN_LƯỢC",
    "cause_EXEC_MODEL": "MÔ_HÌNH_THỰC_THI",
    "cause_SYSTEM": "HỆ_THỐNG",
    "cause_VPS_OFF": "VPS_TẮT",
    "cause_UNEXPLAINED": "CHƯA_GIẢI_THÍCH",
    "cause_SHARED_UNRESOLVED": "VỊ_THẾ_GỘP_KHÔNG_PHÂN_BỔ_ĐƯỢC",
    "cause_WITHIN_TOL": "TRONG_DUNG_SAI",
    "verify_pass": "KIỂM TRA TÁI LẬP & TỔNG HỢP: ĐẠT",
    "verify_fail": "KIỂM TRA TÁI LẬP & TỔNG HỢP: KHÔNG ĐẠT",
    "funding_note": "Lưu ý: execution_finances.funding = NULL theo thiết kế; cột Funding chỉ mang tính ESTIMATED (notional * funding_rate), KHÔNG cộng vào PnL ròng thực tế.",
    "compare_from_note": "Mốc bắt đầu so sánh chính thức (compare_from): {compare_from}. Mọi số tổng chính chỉ tính từ mốc này.",
    "no_orders_after_compare_from": "CHƯA CÓ LỆNH SAU MỐC SO SÁNH",
    "sec_historical": "Lịch sử (không dùng để kết luận)",
    "historical_note": "Các lệnh và tín hiệu trước mốc {compare_from} chỉ hiển thị tham khảo theo từng kỷ nguyên cũ, không cộng vào số tổng chính.",
    "slippage_convention_title": "Quy ước dấu và công thức trượt giá vào lệnh (đơn vị: bps)",
    "slippage_convention_desc": "Nguồn giá tham chiếu: strategy_signals.entry. Dấu (+) là BẤT LỢI (mua cao hơn hoặc bán thấp hơn tham chiếu); dấu (-) là CÓ LỢI (mua thấp hơn hoặc bán cao hơn tham chiếu). Công thức: BUY = ((avg_price - entry) / entry) * 10000; SELL = ((entry - avg_price) / entry) * 10000.",
    "col_latency_sec": "Độ trễ khớp (giây)",
    "sig_new6_vp": "NEW6_VIRTUAL_POSITION (tách riêng)",
    "engine_unavailable": "KHÔNG_DỰNG_ĐƯỢC_ENGINE",
    "sync_btn_label": "Đồng bộ",
    "sync_running": "Đang đồng bộ...",
    "sync_done_at": "Xong lúc {time}",
    "sync_error_prefix": "Lỗi: {reason}",
    "sync_last_at": "Lần đồng bộ cuối: {time} (giờ VN)",
    "sync_missing_env": "Thiếu biến môi trường SUPABASE_DB_URL — không thể đồng bộ.",
    "sync_cooldown_msg": "Vui lòng đợi tối thiểu {sec} giây giữa hai lần đồng bộ.",
    "sync_already_running": "Đang có tiến trình đồng bộ chạy nền, không thể chạy đồng thời.",
    "sync_readonly_exception_note": (
        "Ngoại lệ có chủ ý của nguyên tắc dashboard chỉ đọc: nút 'Đồng bộ' chỉ ghi vào "
        "data/bingx/, evidence/v3-17a/ và cache dashboard; chỉ đọc Supabase qua collector_v3 (SUPABASE_DB_URL)."
    ),
}

RECONCILE_FORBIDDEN_ENGLISH: tuple[str, ...] = FORBIDDEN_ENGLISH_LABELS + (
    "Match Rate",
    "Entry Slippage",
    "Root Cause",
    "Sample Size",
    "Closed Orders",
    "Signal Reconciliation",
    "Order Reconciliation",
    "Live PnL",
    "Simulated PnL",
)


def find_english_label_leaks(html_content: str) -> list[str]:
    """Kiểm tra rò rỉ nhãn tiếng Anh trong phần văn bản hiển thị của HTML."""
    leaks = list(_base_find_leaks(html_content))
    visible_lower = extract_visible_html_text(html_content).lower()
    for phrase in RECONCILE_FORBIDDEN_ENGLISH:
        if phrase in FORBIDDEN_ENGLISH_LABELS:
            continue
        if phrase.lower() in visible_lower:
            leaks.append(phrase)
    return leaks
