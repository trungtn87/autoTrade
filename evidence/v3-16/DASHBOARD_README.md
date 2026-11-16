# Dashboard báo cáo phòng lab V3-16
1. Mở PowerShell tại thư mục gốc của repository.
2. Chạy: `python -m autotrade_v3.dashboard serve`.
3. Mở `http://127.0.0.1:8501` trong trình duyệt.
4. Dashboard chỉ lắng nghe trên máy cục bộ `127.0.0.1`.
5. Nguồn run mặc định: `runs/v3_16_all_signals`.
6. Trang đầu mặc định chọn run BASE mới nhất.
7. Bấm “Tải lại” để đọc lại kho run, không dùng cache dài hạn.
8. Mở báo cáo để kiểm tra lại artifact trước khi hiện số.
9. Dashboard không gọi mạng, API key, Supabase hoặc BingX.


## Kho backtest nhiều chế độ

- Trang **Kho backtest** quét lại các file run_manifest.json trong cây runs/ mỗi lần mở hoặc bấm **Tải lại**.
- Hỗ trợ danh mục cho ALL_SIGNALS_PORTFOLIO, ALL_SIGNALS, ONE_TRADE_PER_COMBO, BINGX_PORTFOLIO và các mode khác có manifest hợp lệ.
- Nếu run nằm ngoài cây runs/ (ví dụ worktree nghiên cứu khác), thêm đường dẫn gốc qua biến môi trường AUTOTRADE_BACKTEST_RUN_ROOTS. Trên Windows, ngăn cách nhiều đường dẫn bằng dấu chấm phẩy.
- Run phải có run_manifest.json để được nhận diện. Không suy đoán kết quả từ tên thư mục hay CSV tự phát. Kết quả script nghiên cứu riêng cần được chuẩn hóa thành artifact có provenance.
- Thông tin run hiện ngay cả khi chưa có validation; KPI từ analysis.json chỉ hiện khi validation.json có trạng thái VERIFIED/PARTIALLY_VERIFIED và checksum được kiểm tra nếu có. Các run này không được coi là đã qua verifier MT5; PARTIALLY_VERIFIED không phải VERIFIED.
- Bảng combo-symbol giữ nguyên từng dòng symbol/combo của artifact, không gộp BTC-C1 với ETH-C1. PnL theo combo trong mode danh mục cần đọc theo semantics của report gốc.
- Trang **Báo cáo MT5** và quy trình verify ledger/hash hiện có giữ nguyên và vẫn chỉ áp dụng cho ALL_SIGNALS_PORTFOLIO.
- Không gọi Supabase/BingX, không dùng API key, không sửa run/dataset/engine/live.

Nếu đang chạy dashboard từ main hoặc worktree cũ, cần cập nhật code dashboard đúng worktree trước; bấm refresh trình duyệt không cập nhật được code nhánh mới.
