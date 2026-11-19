# Kế hoạch dashboard V3-16

V3-15 là dashboard Streamlit của phòng lab gồm 7 trang, đọc `runs/lab/store` qua lớp truy vấn chỉ đọc; lệnh cũ là `run_dashboard.bat` (Streamlit tại `127.0.0.1:8501`). V3-16 tái sử dụng đúng ứng dụng và giao diện chung đó, nhưng entrypoint chỉ mở hai trang ALL_SIGNALS: danh sách run và chi tiết báo cáo MT5. Nguồn mặc định là `runs/v3_16_all_signals`; báo cáo được tìm trong run trước, rồi `evidence/v3-16/reports`. Chi tiết fail-closed: verify lại ledger/hash trước khi hiện số; status chỉ lấy từ `validation.json`.
