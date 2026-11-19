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
