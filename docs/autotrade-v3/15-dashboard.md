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
