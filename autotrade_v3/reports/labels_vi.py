"""Single source of truth for Vietnamese labels in MT5-style reports (Issue #102)."""
from __future__ import annotations

import re

LABELS_VI: dict[str, str] = {
    # Dashboard V3-16 (mọi chuỗi giao diện phải đi qua bảng này)
    "dashboard_page_title": "AutoTrade V3 — Báo cáo phòng lab",
    "dashboard_sidebar_title": "AutoTrade V3",
    "dashboard_sidebar_caption": "Phòng lab cục bộ · Chỉ đọc · Không kết nối mạng",
    "dashboard_runs_nav": "Danh sách run",
    "dashboard_report_nav": "Báo cáo MT5",
    "runs_title": "Danh sách run ALL_SIGNALS",
    "runs_subtitle": "Đọc trực tiếp từ kho run trên máy này; mới nhất hiển thị trước.",
    "reload": "Tải lại",
    "no_runs": "Không tìm thấy run ALL_SIGNALS_PORTFOLIO trong kho run.",
    "run_id": "Mã run",
    "generated_at": "Thời điểm sinh",
    "cost_profile": "Mức chi phí",
    "cost_profile_hash": "Băm mức chi phí",
    "commit": "Commit",
    "verification_status": "Trạng thái xác minh",
    "validation_file_status": "Trạng thái từ validation.json",
    "net_profit": "Tổng lãi ròng",
    "max_drawdown": "Sụt giảm tối đa M2M",
    "entry_count": "Số entry",
    "open_report": "Mở báo cáo",
    "report_title": "Báo cáo MT5 — ALL_SIGNALS",
    "report_subtitle": "Số liệu chỉ xuất hiện sau khi kiểm tra lại artifact thành công.",
    "choose_cost_profile": "Chuyển nhanh mức chi phí",
    "back_to_runs": "Về danh sách run",
    "export_ai_json": "Xuất JSON cho AI",
    "comparison_title": "So sánh ba mức chi phí",
    "profit_factor": "Hệ số lợi nhuận (PF)",
    "break_even_slippage": "Trượt giá hòa vốn mỗi chiều",
    "report_content": "Nội dung báo cáo MT5",
    "verify_pass": "ĐẠT — artifact và số liệu khớp ledger",
    "verify_fail": "LỖI — artifact hoặc số liệu không khớp ledger",
    "verified": "Đã xác minh",
    "partially_verified": "Xác minh một phần",
    "failed": "Không đạt",
    "unverified": "Chưa xác minh",
    "validation_not_verified_warning": "validation.json chưa xác nhận run ở trạng thái đã xác minh.",
    "dirty_run_warning": "CẢNH BÁO: run được sinh khi mã nguồn có thay đổi chưa commit.",
    "commit_mismatch_warning": "CẢNH BÁO: commit sinh run khác commit hiện tại.",
    "current_commit": "Commit hiện tại",
    "report_commit": "Commit sinh run",
    "missing_report": "Không tìm thấy đủ report_v1.json và report_mt5.html cho run này.",
    "verification_running": "Đang kiểm tra lại báo cáo từ ledger…",
    "verification_hidden": "Đã ẩn toàn bộ số liệu vì kiểm tra báo cáo không đạt.",
    "profile_unavailable": "Chưa có báo cáo cho mức chi phí này.",
    "report_metadata": "Thông tin run",
    "all_signals_only": "Chỉ hiển thị chế độ ALL_SIGNALS_PORTFOLIO",
    "table_empty_value": "Không có",
    # Banner & Governance
    "fee_mode_prefix": "CHẾ ĐỘ CHI PHÍ",
    "fee_mode_base_detail": "BASE (giả định 2 bps)",
    "fee_mode_stress_detail": "STRESS (giả định 5 bps)",
    "fee_mode_ideal_ref": "IDEAL (tham chiếu 0 bps)",
    "ideal_warning": (
        "Cảnh báo: Hồ sơ IDEAL giả định trượt giá = 0, chênh lệch giá = 0, "
        "phí funding = Chưa mô hình hóa. Chỉ dùng làm mốc tham chiếu, không phản ánh chi phí thực tế."
    ),
    "other_modes_out_of_scope": "Chế độ khác (ONE_TRADE_PER_COMBO, BINGX_PORTFOLIO): ngoài phạm vi báo cáo chính từ V3-16 Bước 3, xem evidence cũ.",
    "gov_2026_validation": "VALIDATION (đã dùng để chọn biến thể)",
    "gov_development": "GIAI ĐOẠN PHÁT TRIỂN (24M)",
    "gov_forward": "BẰNG CHỨNG FORWARD (Cấu hình đã khóa)",
    "sample_badge": "MẪU THỬ",
    "na_text": "Không có",
    "na_no_snapshots": "Không có: không có snapshot M2M",
    "na_not_run": "Không có (chưa chạy)",
    "na_no_open_margin": "Không có: chưa phát sinh ký quỹ đang dùng",
    "na_insufficient_days": "Không có: dữ liệu dưới 2 ngày hoặc độ lệch chuẩn bằng 0",

    # Section Titles
    "sec_account": "Tài khoản",
    "sec_cost_reality": "Thực tế chi phí",
    "sec_risk": "Rủi ro",
    "sec_volume": "Khối lượng lệnh (tách đơn vị)",
    "sec_balance_equity_chart": "Biểu đồ Số dư & Vốn thực (M2M)",
    "sec_dd_dollars_chart": "Biểu đồ Sụt giảm ($)",
    "sec_dd_pct_chart": "Biểu đồ Sụt giảm (%)",
    "sec_mt5_results": "Kết quả giao dịch (kiểu MT5)",
    "sec_long_short": "Phân tích Lệnh Mua / Bán",
    "sec_streaks": "Chuỗi thắng / thua (kiểu MT5)",
    "sec_cost_sensitivity": "Độ nhạy chi phí (trượt giá mỗi chiều — chỉ hiện run thật)",
    "sec_combos": "Bảng 20 combo hợp nhất (cầu nối cơ sở Mode C)",
    "sec_symbols": "Phân tách theo cặp giao dịch (BTC/ETH)",
    "sec_monthly": "Báo cáo theo tháng",
    "sec_mode_comparison": "Phạm vi chế độ mô phỏng",
    "sec_concentration": "Mức độ tập trung & đồng thời (cảnh báo rủi ro)",

    # Concentration
    "max_open_entries_any_sym_side": "Số lệnh mở đồng thời tối đa (cặp-chiều)",
    "max_open_notional_any_sym_side": "Notional mở tối đa (cặp-chiều)",
    "max_open_notional_total": "Notional mở tối đa toàn danh mục",
    "btc_c11_own_entry_pnl": "Lãi lỗ BTC-USDT/C11 (entry riêng)",
    "btc_c11_share_of_total_net_pct": "Tỷ trọng BTC-USDT/C11 trong tổng lãi ròng",

    # Account
    "initial_capital": "Vốn ban đầu",
    "final_balance": "Số dư cuối kỳ",
    "final_equity_mtm": "Vốn thực (M2M) cuối kỳ",
    "max_used_margin": "Ký quỹ đang dùng tối đa",
    "min_free_margin": "Ký quỹ khả dụng tối thiểu",
    "min_margin_level_pct": "Mức ký quỹ tối thiểu",

    # Cost Reality
    "gross_pnl_before_costs": "Lãi gộp trước phí",
    "total_fees": "Tổng phí giao dịch",
    "fee_drag_pct": "Phí chiếm % lãi gộp",
    "break_even_slippage_per_side_pct": "Trượt giá hòa vốn mỗi chiều (%)",
    "break_even_slippage_per_side_bps": "Trượt giá hòa vốn mỗi chiều (bps)",
    "spread_cost": "Chi phí chênh lệch giá",
    "slippage_cost": "Chi phí trượt giá",
    "funding_cost": "Chi phí funding",

    # Risk
    "net_pf_entry": "Hệ số lợi nhuận (PF) theo entry",
    "net_pf_trade_vwap": "Hệ số lợi nhuận (PF) theo entry (VWAP)",
    "net_pf_leg": "Hệ số lợi nhuận (PF) theo leg",
    "gross_pf_leg": "PF gộp theo leg",
    "recovery_factor_closed": "Hệ số phục hồi (DD đã chốt)",
    "recovery_factor_m2m": "Hệ số phục hồi (DD M2M)",
    "sharpe_daily_365": "Chỉ số Sharpe (theo ngày, rf=0, √365)",
    "closed_max_dd_dollars": "Sụt giảm tối đa đã chốt ($)",
    "closed_max_dd_pct": "Sụt giảm tối đa đã chốt (%)",
    "m2m_max_dd_dollars": "Sụt giảm tối đa M2M ($)",
    "m2m_max_dd_pct": "Sụt giảm tối đa M2M (%)",

    # Volume
    "total_signals": "Tín hiệu",
    "accepted_entries": "Entry đã khớp",
    "entry_fill_rate_pct": "Tỷ lệ khớp (%)",
    "total_legs": "Chân lệnh (leg)",
    "skipped_by_concurrency": "Bỏ qua do giới hạn đồng thời",
    "rejected_by_margin": "Từ chối do thiếu ký quỹ",

    # MT5 Results
    "total_net_profit": "Tổng lãi ròng",
    "roi_pct": "Tỷ suất sinh lời (ROI)",
    "gross_profit_entry": "Tổng lãi gộp (entry thắng sau phí)",
    "gross_loss_entry": "Tổng lỗ gộp (entry thua sau phí)",
    "expected_payoff_entry": "Kỳ vọng mỗi lệnh (entry)",
    "expected_payoff_leg": "Kỳ vọng mỗi chân lệnh (leg)",
    "balance_dd_absolute_dollars": "Sụt giảm số dư tuyệt đối ($)",
    "balance_dd_absolute_pct": "Sụt giảm số dư tuyệt đối (%)",
    "balance_dd_maximal_dollars": "Sụt giảm số dư lớn nhất ($)",
    "balance_dd_relative_pct": "Sụt giảm số dư tương đối (%)",
    "winning_entries": "Lệnh thắng (entry)",
    "losing_entries": "Lệnh thua (entry)",
    "entry_win_rate_pct": "Tỷ lệ thắng theo entry (%)",
    "leg_win_rate_pct": "Tỷ lệ thắng theo leg (%)",
    "largest_profit_entry": "Lệnh thắng lớn nhất (entry)",
    "largest_loss_entry": "Lệnh thua lớn nhất (entry)",
    "avg_profit_entry": "Lệnh thắng trung bình (entry)",
    "avg_loss_entry": "Lệnh thua trung bình (entry)",

    # Long / Short
    "long_entries": "Lệnh Mua (entry)",
    "long_won": "Lệnh Mua thắng",
    "long_win_rate_pct": "Lệnh Mua (thắng %)",
    "long_net_pnl": "Lãi ròng lệnh Mua",
    "long_pf": "PF lệnh Mua",
    "short_entries": "Lệnh Bán (entry)",
    "short_won": "Lệnh Bán thắng",
    "short_win_rate_pct": "Lệnh Bán (thắng %)",
    "short_net_pnl": "Lãi ròng lệnh Bán",
    "short_pf": "PF lệnh Bán",

    # Streaks
    "max_consecutive_wins_count": "Chuỗi thắng dài nhất (số lệnh)",
    "max_consecutive_wins_dollars": "Chuỗi thắng dài nhất ($)",
    "max_consecutive_losses_count": "Chuỗi thua dài nhất (số lệnh)",
    "max_consecutive_losses_dollars": "Chuỗi thua dài nhất ($)",
    "maximal_consecutive_profit_dollars": "Chuỗi lãi lớn nhất ($)",
    "maximal_consecutive_profit_count": "Chuỗi lãi lớn nhất (số lệnh)",
    "maximal_consecutive_loss_dollars": "Chuỗi lỗ lớn nhất ($)",
    "maximal_consecutive_loss_count": "Chuỗi lỗ lớn nhất (số lệnh)",
    "avg_consecutive_wins": "Chuỗi thắng trung bình (số lệnh)",
    "avg_consecutive_losses": "Chuỗi thua trung bình (số lệnh)",

    # Table columns
    "col_own_entry_pnl": "Lãi lỗ theo entry riêng ($)",
    "col_portfolio_net_pnl": "Lãi lỗ ròng theo danh mục (VWAP) ($)",
    "col_basis_shift": "Chênh lệch cơ sở ($)",
    "col_fees": "Phí ($)",
    "col_pf": "PF",
}

# English metric/UI labels that must NOT leak into rendered Vietnamese HTML.
FORBIDDEN_ENGLISH_LABELS: tuple[str, ...] = (
    "Total Net Profit",
    "Gross Profit",
    "Gross Loss",
    "Profit Factor",
    "Expected Payoff",
    "Recovery Factor",
    "Sharpe Ratio",
    "Free Margin",
    "Margin Level",
    "Balance Drawdown",
    "Total Trades",
    "Long Trades",
    "Short Trades",
    "Profit Trades",
    "Loss Trades",
    "Largest profit trade",
    "Largest loss trade",
    "Average profit trade",
    "Average loss trade",
    "Maximum consecutive wins",
    "Maximum consecutive losses",
    "Maximal consecutive profit",
    "Maximal consecutive loss",
    "Average consecutive wins",
    "Average consecutive losses",
    "Cost Reality",
    "Cost Sensitivity",
    "FEE MODE",
    "Own-entry PnL",
    "Portfolio Net PnL",
    "Break-even slippage",
    "Fee drag",
    "Fill rate",
    "Out-of-Sample",
    "Holdout",
    "SAMPLE LAYOUT",
)

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def extract_visible_html_text(html_content: str) -> str:
    """Strip <script>, <style>, and HTML tags to inspect visible UI text."""
    no_scripts = _SCRIPT_STYLE_RE.sub(" ", html_content)
    no_tags = _TAG_RE.sub(" ", no_scripts)
    return re.sub(r"\s+", " ", no_tags).strip()


def find_english_label_leaks(html_content: str) -> list[str]:
    """Return any forbidden English labels found in visible HTML text."""
    visible = extract_visible_html_text(html_content)
    visible_lower = visible.lower()
    leaks: list[str] = []
    for phrase in FORBIDDEN_ENGLISH_LABELS:
        if phrase.lower() in visible_lower:
            leaks.append(phrase)
    return leaks
