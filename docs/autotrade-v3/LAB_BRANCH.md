# v3_lab — Nhánh nghiên cứu độc lập

**Source of truth cho nghiên cứu V3 trên GitHub:** `v3_lab`.  
**Production:** chỉ `main`. Không tạo PR merge toàn bộ `v3_lab` vào `main`.

## Phạm vi đã hợp nhất (2026-11-15)

- V3-07..V3-15C từ lịch sử nhánh Lab, trên đó đã phát triển V3-15D (2 commit sửa kiểm toán).
- ETH/C1 H1_BOTH_1 từ `feat/v3-eth-c1-l2-h1-both-1` (`11d361c`); chỉ nằm trong Lab.
- V3-16 Fixed TP/SL research từ `feat/v3-16-fixed-tpsl-backtest` (`c99e452`): mô phỏng, test, báo cáo và params phiên bản mới; **không thay đổi 20FINAL@1 hoặc Golden lịch sử**.
- Tài liệu governance 5 AI lấy từ `main` PR #126, không đưa mã Live từ `main` sang Lab.

Các thay đổi cùng file được hợp nhất có kiểm soát: `autotrade_v3/lab/experiment.py`, `autotrade_v3/lab/run_manager.py`, `autotrade_v3/validation/gates_c.py`. Không ghi đè sửa lỗi V3-15D bằng bản V3-16.

## Cách làm việc từ nay

1. Antigravity làm việc trên nhánh công việc xuất phát từ `v3_lab` hoặc trực tiếp trên local `v3_lab`, test và tạo PR **base `v3_lab`**.
2. ChatGPT nghiệm thu chứng cứ; Codex kiểm toán độc lập khi cần.
3. Owner chốt cải tiến nào đưa Live; Claude Code mở **PR riêng từ `main` sang `main`**, chỉ port logic/config đã duyệt. Không merge nguyên Lab vào Live.
4. M1 chỉ dùng mô phỏng diễn biến TP/SL và kết quả backtest, không phải phụ thuộc bắt buộc của Live. Tín hiệu dùng M15 và nến tổng hợp đã đóng.
5. Dữ liệu M1 Parquet, kết quả P3 local không được tự nhận là đã có trong GitHub. Đồng bộ các file **chưa commit** trên máy Windows phải được Antigravity rà soát và push riêng; tuyệt đối không push API key, DB dump hoặc dataset gốc ngoài phạm vi được Owner đồng ý.
6. Giữ nguyên mọi nhánh cũ để tra cứu, không force-push hoặc xóa lịch sử. Không nâng GitHub `v3_lab` thành default branch nếu chưa được Owner duyệt.

**Tình trạng kiểm thử sau hợp nhất:** Các test trên từng nhánh là bằng chứng theo SHA gốc; chưa chứng minh tích hợp đa nhánh PASS cho commit hợp nhất. CI/Mypy có lỗi tồn tại tại nhánh V3-15D (Dashboard), phải báo cáo chính xác, không âm thầm nâng baseline hay né gate. Kiểm tra tập trung những file hợp nhất trước khi ghi nhận nghiệm thu Lab.

Xem thêm [quy tắc 5 AI](../governance/AI_ROLES_WORKFLOW.md).
