"""Bảng nhãn tiếng Việt thống nhất và bộ phát hiện rò rỉ nhãn tiếng Anh cho V3-18."""
from __future__ import annotations

from autotrade_v3.reports.labels_vi import (
    FORBIDDEN_ENGLISH_LABELS as BASE_FORBIDDEN_ENGLISH,
    extract_visible_html_text,
    find_english_label_leaks as _base_find_leaks,
)

LABELS_VI: dict[str, str] = {
    "page_title": "AutoTrade V3-18 · Nghiên cứu chế độ thị trường & SMC (30 mã)",
    "subtitle": "Phòng lab cục bộ · Chỉ đọc dữ liệu lịch sử · Chi phí BASE (2 bps/chiều) · Không tinh chỉnh theo kết quả",
    "kpi_total_combos": "Tổng số mã khảo sát",
    "kpi_live_split": "Đang live / Ngoài live",
    "kpi_total_signals": "Tổng tín hiệu (Độc lập 30 mã)",
    "kpi_multiple_tests": "Số phép thử danh mục ghép",
    "kpi_holdout_rule": "Quy tắc đề xuất theo dõi",
    "kpi_verify_status": "Trạng thái xác minh SHA-256",
    "sec_30_combos": "1. Danh sách đủ 30 mã (Đợt chạy chéo 30 · AUDIT_20_POSITIVE_FIXED_TPSL_20261116)",
    "sec_splits_and_regime": "2. Phân chia dữ liệu tĩnh & định nghĩa chế độ thị trường / SMC",
    "sec_heatmap": "3. Bản đồ nhiệt Mã × Chế độ thị trường (Chi phí BASE · Chạy độc lập)",
    "sec_smc_summary": "4. Phân tích theo đặc trưng SMC, Thanh khoản & Phiên giao dịch",
    "sec_portfolio": "5. Chạy song song / ghép mã (Sổ tài khoản chung · 2 bps/chiều)",
    "sec_equity_chart": "6. Đường vốn danh mục ghép qua 3 giai đoạn thời gian",
    "sec_candidates": "7. Danh sách mã / cấu hình có hướng phát triển (đề xuất theo dõi)",
    "insufficient_sample": "CHƯA ĐỦ MẪU",
    "live_badge": "ĐANG LIVE (23)",
    "non_live_badge": "NGOÀI LIVE (7)",
    "multiple_testing_warning": (
        "CẢNH BÁO NHIỀU PHÉP THỬ: Khi chạy đồng thời {m_tests} phép thử danh mục, "
        "xác suất xuất hiện kết quả dương ngẫu nhiên tăng theo số phép thử. "
        "Áp dụng hiệu chỉnh Holm-Bonferroni (mức ý nghĩa gia đình α={alpha:.2f}, ngưỡng giá trị p hiệu chỉnh nghiêm ngặt nhất α/M={alpha_min:.5f})."
    ),
    "neutral_note": (
        "Báo cáo chỉ trình bày số liệu thực nghiệm trung lập và khoảng tin cậy tái lấy mẫu 95% trên 3 giai đoạn khóa sẵn; "
        "không tự động thay đổi cấu hình live."
    ),
    "split_dev": "PHÁT_TRIỂN (24T: 2023-11..2025-10)",
    "split_val": "KIỂM_ĐỊNH (6T: 2025-11..2026-04)",
    "split_hold": "KIỂM_CHỨNG_CUỐI (6T: 2026-05..2026-10)",
    "split_all": "TOÀN_KỲ (36T)",
    "verify_pass": "ĐẠT — Khớp toàn bộ SHA-256, tổng số và quy tắc kiểm chứng",
    "verify_fail": "KHÔNG ĐẠT — Phát hiện sai lệch xác minh",
}

V3_18_FORBIDDEN_ENGLISH: tuple[str, ...] = BASE_FORBIDDEN_ENGLISH + (
    "Win Rate",
    "Max Drawdown",
    "Net PnL",
    "Sample Size",
    "Validation",
    "Holdout",
    "Development",
    "Heat Map",
    "Heatmap",
    "Equity Curve",
    "Portfolio",
    "Regime",
    "Liquidity Sweep",
    "Order Block",
    "Insufficient Sample",
    "Bootstrap",
    "Candidates",
    "Baseline",
)


def find_english_label_leaks(html_content: str) -> list[str]:
    """Kiểm tra rò rỉ nhãn tiếng Anh trong phần văn bản hiển thị của HTML V3-18."""
    leaks = list(_base_find_leaks(html_content))
    visible_lower = extract_visible_html_text(html_content).lower()
    for phrase in V3_18_FORBIDDEN_ENGLISH:
        if phrase in BASE_FORBIDDEN_ENGLISH:
            continue
        if phrase.lower() in visible_lower:
            leaks.append(phrase)
    return leaks
