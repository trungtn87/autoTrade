# AutoTrade — Agent instructions

Trước khi thực hiện nhiệm vụ, đọc và tuân thủ **[docs/governance/AI_ROLES_WORKFLOW.md](docs/governance/AI_ROLES_WORKFLOW.md)**. Quy tắc này xác định quyền hạn của ChatGPT, Antigravity, Gemini, Codex và Claude Code. Không viện dẫn vai trò AI để tự nâng quyền của mình.

## Ranh giới mặc định
- Local research: thử nghiệm offline; không gọi trade API hay production DB; không thay đổi V2/live, 20FINAL/Golden trừ khi task nêu rõ phạm vi và Owner phê duyệt.
- **Codex được giao audit**: chỉ đọc/chạy test, không sửa file, commit, push, merge hoặc deploy.
- Thay đổi live/main: cần Owner duyệt phương án; kiểm thử có bằng chứng; **merge/deploy phải được Owner cho phép rõ ràng** vì main có thể tự triển khai.
- Không chạy lại backtest lớn khi không có lý do; ưu tiên kiểm thử phần bị ảnh hưởng.
- Luôn tuân thủ [docs/BASELINE.md](docs/BASELINE.md) và [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Mọi điều kiện an toàn hiện có được giữ nguyên.

Nếu file này không trùng với quy tắc cục bộ trên máy người dùng, không tự ghi đè/chọn bản yếu hơn; báo Owner đối chiếu.
