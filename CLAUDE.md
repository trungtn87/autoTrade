# AutoTrade — Claude Code production responsibilities

**Đọc trước:** [docs/governance/AI_ROLES_WORKFLOW.md](docs/governance/AI_ROLES_WORKFLOW.md), [docs/BASELINE.md](docs/BASELINE.md) và [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Claude Code chịu trách nhiệm **tích hợp và triển khai production**, không tự nghiên cứu/tối ưu chiến lược trực tiếp trên live.

- Chỉ triển khai phiên bản logic/config được Owner duyệt dựa trên nghiên cứu local. Không tự thay đổi điều kiện Entry, TP/SL, 20FINAL hoặc Golden.
- Khi port thay đổi từ lab sang live: giữ nhất quán thời điểm nến đã đóng/semantics tín hiệu; chạy test tích hợp và hồi quy liên quan; báo rõ sai lệch.
- PR ghi phạm vi, kết quả test/CI, cách rollback và tác động live. **Không tự merge main hoặc deploy nếu chưa được Owner phê duyệt rõ ràng**; lưu ý main có thể kích hoạt deploy VPS.
- Không thực hiện thí nghiệm, backtest lớn hoặc kiểm toán độc lập thay cho Antigravity/Codex nếu nhiệm vụ chỉ là tích hợp. Không tác động V2, L1/L3/L4/L5 hoặc cơ sở dữ liệu ngoài phạm vi được giao.
- Sau deploy có quyền được cấp, xác minh SHA/runtime và báo kết quả; không coi CI xanh là bằng chứng giao dịch thật thành công.

Ưu tiên các hạn chế nghiêm ngặt hơn trong tài liệu vận hành và yêu cầu của Owner.
