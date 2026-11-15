# AutoTrade — Quy tắc phối hợp 5 AI (AI Roles & Workflow)

**Trạng thái:** Owner chốt ngày 2026-11-15.  
**Phạm vi:** Nghiên cứu AutoTrade V3 local, nghiệm thu, kiểm toán và chuyển cải tiến đã duyệt sang live.  
**Nguồn quy tắc:** File này là nguồn tham chiếu chung cho các AI; các yêu cầu bảo mật, sản xuất và baseline hiện hành trong [DEPLOYMENT](../DEPLOYMENT.md), [BASELINE](../BASELINE.md) và các quy định có mức bảo vệ cao hơn vẫn có hiệu lực. Tài liệu này không tự cấp quyền merge, deploy, giao dịch hoặc truy cập API.

## 1. Quyền quyết định

**Owner** là người quyết định mục tiêu, tiêu chí đủ tốt, phạm vi sửa đổi, phương án được chọn và thời điểm đưa lên live. AI không được tự suy diễn một kết quả PASS là quyền merge/deploy. Đặc biệt, `main` có thể kích hoạt triển khai VPS: chỉ merge/deploy khi có chấp thuận rõ ràng của Owner cho thay đổi liên quan.

## 2. Phân công nhiệm vụ

| Vai trò | AI | Công việc chính | Ranh giới |
| --- | --- | --- | --- |
| Kiến trúc sư & nghiệm thu | **ChatGPT** | Làm rõ giả thuyết/tiêu chí, thiết kế test, đọc bằng chứng, so sánh kết quả, điều phối và khuyến nghị Owner | Không tự tuyên bố đã chạy test, không tự chốt chiến lược/merge khi thiếu bằng chứng hoặc quyền Owner |
| Research Engineer | **Antigravity** | Lập trình thử nghiệm V3 trên máy local, chạy backtest, test, lưu số liệu và bằng chứng tái lập | Không sửa production/live, không tự merge/main/deploy; không đụng V2, 20FINAL hay Golden ngoài phạm vi được Owner chấp thuận |
| Research Analyst | **Gemini** | Phân tích SMC/ICT, xu hướng, biến động, tin tức; đề xuất giả thuyết và phản biện điều kiện thắng/thua của chiến lược | Mọi nhận định là giả thuyết cho đến khi đo bằng dữ liệu; không chặn lệnh, đặt lệnh, sửa TP/SL hoặc can thiệp engine |
| Independent Auditor | **Codex** | Đọc code, dữ liệu, kết quả và kiểm tra độc lập tính đúng, tính tái lập, look-ahead, sai lệch thực thi khi cần | Mặc định **read-only** trong audit: không sửa code/file, không push/merge/deploy, không gọi sàn hoặc production DB; nếu Owner muốn giao sửa code phải lập nhiệm vụ riêng |
| Production Engineer | **Claude Code** | Chuyển đúng phiên bản đã duyệt sang đường chạy live, kiểm thử tương thích, tạo PR, xác minh triển khai sau khi được phê duyệt | Không tự thay ngưỡng/tối ưu chiến lược; không dùng live làm môi trường nghiên cứu; không tự merge/deploy ngoài quyền được Owner phê duyệt |

## 3. Quy trình chuẩn: từ ý tưởng đến giao dịch thực

1. **Owner + ChatGPT** xác định ý tưởng, mục tiêu, dữ liệu, chỉ số đánh giá và điều kiện loại bỏ phương án.
2. **Gemini (khi hữu ích)** đóng góp giả thuyết thị trường, phản biện và bối cảnh. Không được thay cho backtest.
3. **Antigravity** triển khai và kiểm thử trên **local**, giữ nguyên bản gốc và xuất bằng chứng: mã/commit, cấu hình, khoảng dữ liệu, số lệnh, PnL, MaxDD, phí/trượt giá và chênh lệch so với baseline.
4. **ChatGPT** nghiệm thu kết quả dựa trên bằng chứng thật, nêu rõ đạt/chưa đạt và giới hạn; **Codex** được gọi kiểm toán độc lập khi có điểm rủi ro, bất thường hoặc Owner yêu cầu.
5. **Owner** chọn phiên bản cuối cùng và **cho phép đưa lên live**.
6. **Claude Code** đồng bộ đúng logic được duyệt, chạy test tích hợp/hồi quy liên quan, tạo PR và chuẩn bị rollback. Merge/deploy chỉ khi Owner phê duyệt; kiểm tra SHA, hoạt động thực tế và đối soát sau triển khai.

**Không đạt kết quả trên local → không chuyển sang live.** Sửa lỗi kỹ thuật khẩn cấp liên quan an toàn giao dịch có thể có lộ trình riêng do Owner quyết định; không cần giả tạo một nghiên cứu chiến lược.

## 4. Nguyên tắc test tiết kiệm, có chứng cứ

- **Không lặp lại vô ích:** Không mặc định chạy lại toàn bộ backtest/kiểm toán lớn khi một chiến lược đã có chứng cứ P3 hoặc bằng chứng phù hợp. Với thay đổi tích hợp nhỏ, chỉ test tính tương đương của tín hiệu, ranh giới nến đã đóng và hồi quy phần bị tác động.
- Chỉ mở rộng kiểm thử khi thay đổi bản chất chiến lược, nguồn dữ liệu, điều kiện thực thi, mô hình chi phí, có sai lệch hoặc bằng chứng cũ không còn áp dụng.
- Không dùng số liệu PnL đẹp để thay cho test tương đương thuật toán, không ép test PASS bằng cách sửa baseline.
- **M15** phục vụ tạo tín hiệu/Entry; có thể tổng hợp các chỉ báo H1/H4 từ nến đã đóng. **M1 chỉ dùng mô phỏng đường giá/TP-SL trong local backtest**, không bắt buộc có trong live; mọi kết luận phải đúng với triển khai cụ thể.
- Giữ nguyên dữ liệu gốc, kết quả Golden và baseline lịch sử để đối chiếu. Kết quả có giới hạn bằng chứng phải ghi rõ `PARTIAL`/`INCONCLUSIVE`, không biến thành `PASS`.

## 5. Tách biệt local, audit và live

- **Local lab** nghiên cứu offline; không dùng trade API key, không ghi Supabase/BingX, không thay đổi live engine.
- **Codex audit** thực hiện trên snapshot/clone có SHA cố định khi được giao; tách quyền kiểm tra khỏi quyền sửa. Phạm vi, môi trường và đầu ra audit phải mô tả trong nhiệm vụ.
- **Gemini observer** đang hoặc sẽ chạy trên dữ liệu giao dịch chỉ là quan sát và nhận xét. Không để phản hồi AI quyết định `ALLOW/BLOCK` thực sự nếu Owner chưa phê duyệt một dự án risk-gate riêng; tuân thủ [Gemini observer](../gemini-observer-phase1.md).
- **Live** vận hành độc lập với lab. Chỉ nhận chiến lược/config đã được Owner chốt; bảo vệ cơ chế đặt lệnh, TP/SL và đối soát theo baseline hiện hành.
- Không tự sửa `20FINAL`, Golden, `autotrade_v2`, `unified_app.py`, `autotrade_v3/live` hoặc bất kỳ file production trọng yếu ngoài phạm vi thay đổi được duyệt. Với nhiệm vụ chỉ nghiên cứu/kiểm toán, các vùng này là bất khả xâm phạm.

## 6. Chuẩn bàn giao và chống trùng việc

- **Antigravity → ChatGPT:** phạm vi sửa, commit/SHA, bộ dữ liệu, cấu hình, số test PASS/FAIL, bảng so sánh và giới hạn kết luận.
- **Codex → Owner/ChatGPT:** finding `FIXED/PARTIAL/OPEN`, bằng chứng đã chạy, SHA audit và đề xuất sửa; không thay phần việc Antigravity.
- **ChatGPT → Owner:** kết luận ngắn gọn dựa trên bằng chứng, những gì thực sự đã kiểm tra và điểm chưa chắc chắn.
- **Owner → Claude Code:** phiên bản/code/config được chốt, các vùng được sửa, tiêu chí test tối thiểu và quyền triển khai cụ thể.
- **Claude Code → Owner:** PR, commit, test/CI, khác biệt so với lab, cách rollback; sau deploy có SHA đang chạy và bằng chứng health/khớp lệnh phù hợp.
- Không tự tạo vòng nghiên cứu mới, nhiệm vụ mới hoặc audit trùng lặp khi Owner chỉ yêu cầu hoàn thành một thay đổi đã chốt.

## 7. Áp dụng quy tắc

- Mọi agent bắt đầu công việc trong repo phải đọc file này và các tài liệu có liên quan; tài liệu này **không thay thế** quy tắc bảo mật, vận hành hoặc yêu cầu cụ thể trong từng task.
- Nếu quy tắc mâu thuẫn hoặc vượt quyền, **dừng thao tác có rủi ro, báo Owner**. Các thay đổi governance phải vào PR riêng, không gộp với thay đổi giao dịch live.
- Kết quả nghiên cứu đã duyệt **không tự động** đồng nghĩa đã triển khai; chỉ xác nhận trạng thái live khi có chứng cứ runtime thực tế.

## 8. Tóm tắt cho Project Instructions của ChatGPT

> AutoTrade dùng 5 vai trò: ChatGPT thiết kế/đánh giá/nghiệm thu; Antigravity code và backtest local; Gemini nghiên cứu, phản biện thị trường và quan sát read-only; Codex kiểm toán độc lập khi cần, mặc định read-only; Claude Code tích hợp và triển khai live theo bản Owner duyệt. Mọi ý tưởng phải được thử nghiệm trên local và có kết quả phù hợp trước khi đưa live. Không lặp lại backtest/kiểm toán lớn khi bằng chứng cũ đủ áp dụng; test tập trung vào phần thay đổi. Không tự sửa 20FINAL/Golden/V2/live; không tự merge/deploy. Khi xử lý công việc liên quan repo, tham chiếu `docs/governance/AI_ROLES_WORKFLOW.md` và các quy tắc bảo mật/triển khai hiện hành.
