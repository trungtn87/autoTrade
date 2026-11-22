"""Nhãn tiếng Việt và bộ kiểm tra rò rỉ nhãn tiếng Anh cho V3-18b."""
from __future__ import annotations

from autotrade_v3.reports.labels_vi import extract_visible_html_text
from autotrade_v3.research_v3_18.labels_vi import (
    V3_18_FORBIDDEN_ENGLISH,
    find_english_label_leaks as _v18_find_leaks,
)

LABELS_VI_18B: dict[str, str] = {
    "page_title": "AutoTrade V3-18b · Nghiên cứu thêm / bớt / lọc mã trên cơ sở Lab 11 live",
    "subtitle": "Cơ sở duy nhất V3_LAB_11_PRUNE23@11.0.0 · Sổ chung kiểu BingX + Funding · Chi phí BASE (2 bps/chiều)",
    "selection_bias_warning": (
        "CẢNH BÁO THIÊN LỆCH CHỌN MẪU & NHIỀU PHÉP THỬ: Danh mục Lab 11 (23 mã) được Owner chọn lọc sau khi quan sát "
        "chính chuỗi dữ liệu 3 năm này, vì vậy KIỂM_ĐỊNH và KIỂM_CHỨNG_CUỐI của Lab 11 KHÔNG còn là ngoài mẫu sạch. "
        "Tổng cộng {m_tests} phép thử (7 thêm + 23 bớt + 3 lọc) được hiệu chỉnh Holm-Bonferroni ở mức α={alpha:.2f} "
        "(ngưỡng nghiêm ngặt nhất α/M={alpha_min:.5f}). Chỉ báo cáo bằng chứng thực nghiệm trung lập, không kết luận tự động."
    ),
    "sec_parity": "1. Đối khớp cơ sở Lab 11 với run V3-16 & giải thích chênh lệch +924,99 vs +593,04 USD",
    "sec_combos_23": "2. Bảng 23 mã đang live — ĐÚNG phiên bản V3_LAB_11_PRUNE23@11.0.0",
    "sec_heatmap_23": "3. Phân rã 23 mã Lab 11 × 6 chế độ thị trường (ô < 30 lệnh ghi CHƯA ĐỦ MẪU)",
    "sec_add_7": "4. Thử nghiệm THÊM 7 mã ngoài live (phiên bản V3_LAB_11@11.0.0) vào cơ sở 23 mã",
    "sec_drop_23": "5. Thử nghiệm BỚT lần lượt 1 mã trong 23 mã live (23 phép thử rời một)",
    "sec_filters_3": "6. Thử nghiệm LỌC theo phiên giao dịch & vùng cung/cầu (tham số khóa trước)",
    "sec_equity": "7. Đường vốn sổ chung kiểu BingX (Lab 11 cơ sở & các cấu hình vượt cả 2 kỳ)",
    "sec_candidates": "8. Danh sách đề xuất theo dõi (vượt cơ sở ở cả KIỂM_ĐỊNH và KIỂM_CHỨNG_CUỐI)",
    "sec_live_placeholder": "9. Khung đối chiếu dữ liệu live sau này (compare_from = 2026-11-22T04:00:00Z)",
}

V3_18B_FORBIDDEN_ENGLISH: tuple[str, ...] = V3_18_FORBIDDEN_ENGLISH + (
    "Leave-One-Out",
    "Filter",
    "Trial",
    "Parity",
)


def find_english_label_leaks_v3_18b(html_content: str) -> list[str]:
    """Kiểm tra rò rỉ nhãn tiếng Anh trong HTML V3-18b."""
    leaks = list(_v18_find_leaks(html_content))
    visible_lower = extract_visible_html_text(html_content).lower()
    for phrase in V3_18B_FORBIDDEN_ENGLISH:
        if phrase in V3_18_FORBIDDEN_ENGLISH:
            continue
        if phrase.lower() in visible_lower:
            leaks.append(phrase)
    return leaks
