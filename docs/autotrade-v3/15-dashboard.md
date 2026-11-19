# AutoTrade V3 Research Lab — Local Dashboard (V3-15)

## 1. Mục tiêu & Kiến trúc
Local Research Lab Dashboard chạy offline trên Windows (`127.0.0.1`), chỉ đọc `runs/lab/store/` (Run Store V3-14), `catalog/research.duckdb` (chỉ đọc), và `strategy_core/` (chỉ đọc trừ lệnh clone). Dashboard không kết nối `autotrade_v2/`, BingX, Supabase hay Gemini.

Ba lớp tách biệt trong `autotrade_v3/dashboard/`:
- `queries.py`: hàm thuần chỉ đọc, mở DuckDB với `read_only=True`, tính mọi số liệu trực tiếp từ ledger (`legs.parquet`, `signals.parquet`, `signal_decisions.parquet`, `events.parquet`, `equity.parquet`, `validation.json`).
- `actions.py` & `_cli_ops.py`: danh sách trắng 8 hành động CLI (`dataset_verify`, `strategy_clone`, `edit_clone_params`, `backtest`, `uncertainty`, `verify`, `diff`, `export_strategy_core`) chạy qua `subprocess.run(..., shell=False)`, chặn chèn lệnh, chặn sửa baseline `20FINAL@1`, chặn cost profile `UNCONFIRMED`/`TEST_ONLY` trên dữ liệu thật, và ghi log tại `runs/lab/dashboard_actions.log`.
- `notes.py`: lưu nhãn, ghi chú và ghim baseline tại `runs/lab/notes.duckdb`, tách biệt hoàn toàn khỏi `runs/lab/store/`.
- `pages/`: 7 màn hình Streamlit (`p01_datasets.py` .. `p07_research_log.py`) chỉ gọi `queries`, `actions` và `notes`.

## 2. Cách cài đặt & Chạy
```bat
.venv\Scripts\pip.exe install -r autotrade_v3/requirements-dashboard.txt
run_dashboard.bat
```
Mặc định lắng nghe tại `http://127.0.0.1:8501` với cấu hình offline trong `.streamlit/config.toml` (`gatherUsageStats = false`, `server.address = "127.0.0.1"`).

## 3. Bảng ánh xạ Phần tử Giao diện -> `queries.py` -> Nguồn Store (B2)
Xem chi tiết đầy đủ tại [ui_map.md](../../evidence/v3-15/ui_map.md).

## 4. Giới hạn
- Không tính toán lại chỉ số ngoài `queries.py`.
- ROI của Mode A (`ALL_SIGNALS`) và Mode B (`ONE_TRADE_PER_COMBO`) chỉ là số tham chiếu trên vốn danh nghĩa, không so sánh trực tiếp với Portfolio ROI của Mode C (`BINGX_PORTFOLIO`).
- Hồ sơ chi phí `UNCONFIRMED` (`BASE`, `STRESS`) và `TEST_ONLY` bị khóa trên dữ liệu M1 thật.
- Baseline `20FINAL@1` (`20final_v1.json` + `20final_v1.lock`) bị khóa cứng, chỉ có thể clone sang version mới.


## 5. Kho backtest V3 (bổ sung từ PR #133)

- Trang **Kho backtest · Tất cả run** đọc trực tiếp `run_manifest.json` trong cây `runs/`, không chỉ `runs/lab/store`, và luôn quét lại khi bấm **Tải lại**. Không thay thế các màn hình Run Detail/Walk-Forward hiện có.
- Các mode `ALL_SIGNALS`, `ONE_TRADE_PER_COMBO`, `BINGX_PORTFOLIO`, `ALL_SIGNALS_PORTFOLIO` hiển thị riêng theo manifest. Chiến lược và combo-symbol giữ tách biệt, không gộp BTC-C1 với ETH-C1.
- Với run nằm ngoài `runs/` (ví dụ một thư mục worktree nghiên cứu riêng), đặt biến `AUTOTRADE_BACKTEST_RUN_ROOTS` trỏ tới thư mục gốc. Trên Windows, nhiều đường dẫn ngăn cách bằng dấu chấm phẩy.
- Run không có `run_manifest.json` **chưa được nhập tự động**; các script nghiên cứu xuất file CSV tùy ý cần chuẩn hóa artifact có thông tin run/provenance.
- **Bảo toàn nguồn sự thật:** `validation.json` là nguồn validation của trang này. Khi thiếu hoặc không VERIFIED/PARTIALLY_VERIFIED, chỉ hiện metadata và report.md gốc, không hiển thị KPI từ analysis.json. Nếu `ARTIFACTS.json` có hash cho analysis.json thì hash phải khớp. PARTIALLY_VERIFIED không được xem là VERIFIED.
- **Không phải kiểm chứng MT5:** Các KPI được đọc trực tiếp từ `analysis.json` của run đủ điều kiện; màn hình này không thay thế verifier độc lập, và không tính lại PnL/ROI.
- Dashboard chỉ chạy local, không đụng V2/live, baseline/golden, Supabase hoặc BingX.
