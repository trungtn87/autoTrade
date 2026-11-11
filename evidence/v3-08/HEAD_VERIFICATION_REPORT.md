# AutoTrade V3 Research Lab — Báo Cáo Xác Minh Độc Lập Trên Commit HEAD `f1f7f5b`

- **Commit HEAD**: `f1f7f5ba951eef4c44d64fdaae874b5b6f9b9e51`
- **Thời gian chạy**: 2026-11-11T09:06:24 -> 09:12:05 (+07:00)
- **Thư mục đầu ra độc lập**: `verify_head_f1f7f5b` (Nằm ngoài `runs/golden/v3-08`, không ghi đè bất kỳ file tham chiếu nào)
- **Dữ liệu đầu vào**: 100% Dữ liệu M1 thật (3 năm từ 2023-11-01 đến 2026-11-01, 3.156.480 nến)
- **Trạng thái Git trước khi chạy**:
  - `code_sha`: `f1f7f5ba951eef4c44d64fdaae874b5b6f9b9e51`
  - `code_dirty`: `False` (Working tree hoàn toàn sạch)
  - `diff_sha256`: `""`
- **Xác thực Dataset Integrity thời gian thực**:
  - `is_valid`: `True`
  - `dataset_hash`: `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628` (Khớp 100% với `runs/golden/v3-08/dataset_manifest.json`)

---

## 1. Bảng Đối Chiếu New HEAD Run vs Locked Golden Baseline

| Mode | Thành phần kiểm toán | Locked Golden Baseline | New HEAD Run (`f1f7f5b`) | Sai lệch (Diff) | Trùng lặp (Duplicates) | Content Hash Match | Trạng thái xác minh |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **ALL_SIGNALS** | `signals.parquet` | 5.984 rows | 5.984 rows | 0 | 0 | **TRUE** | **VERIFIED** |
| | `trades.parquet` | 8.258 legs | 8.258 legs | 0 | 0 | **TRUE** | **VERIFIED** |
| | - Gross PnL | $1.009,0873 | $1.009,0873 | 0.0000 | - | - | **VERIFIED** |
| | - Fees (Taker 0.05%) | $598,3866 | $598,3866 | 0.0000 | - | - | **VERIFIED** |
| | - Net PnL | $410,7007 | $410,7007 | 0.0000 | - | - | **VERIFIED** |
| | - ROI | +41,07% | +41,07% | 0.00% | - | - | **VERIFIED** |
| | `skipped_signals` | 0 rows | 0 rows | 0 | 0 | **TRUE** | **VERIFIED** |
| | `rejected_signals` | 0 rows | 0 rows | 0 | 0 | **TRUE** | **VERIFIED** |
| | Ledger Differences | - | - | 0 cols diff | - | - | **VERIFIED** |
| **ONE_TRADE_PER_COMBO** | `signals.parquet` | 5.984 rows | 5.984 rows | 0 | 0 | **TRUE** | **VERIFIED** |
| | `trades.parquet` | 6.273 legs | 6.273 legs | 0 | 0 | **TRUE** | **VERIFIED** |
| | - Gross PnL | $699,8388 | $699,8388 | 0.0000 | - | - | **VERIFIED** |
| | - Fees (Taker 0.05%) | $415,6443 | $415,6443 | 0.0000 | - | - | **VERIFIED** |
| | - Net PnL | $284,1945 | $284,1945 | 0.0000 | - | - | **VERIFIED** |
| | - ROI | +28,42% | +28,42% | 0.00% | - | - | **VERIFIED** |
| | `skipped_signals` | 1.828 rows | 1.828 rows | 0 | 0 | **TRUE** | **VERIFIED** |
| | `rejected_signals` | 0 rows | 0 rows | 0 | 0 | **TRUE** | **VERIFIED** |
| | Ledger Differences | - | - | 0 cols diff | - | - | **VERIFIED** |
| **BINGX_PORTFOLIO** | `signals.parquet` | 5.984 rows | 5.984 rows | 0 | 0 | **TRUE** | **VERIFIED** |
| | `trades.parquet` | 6.265 legs | 6.265 legs | 0 | 0 | **TRUE** | **VERIFIED** |
| | - Gross PnL | $697,1388 | $697,1388 | 0.0000 | - | - | **VERIFIED** |
| | - Fees (Taker 0.05%) | $414,9453 | $414,9453 | 0.0000 | - | - | **VERIFIED** |
| | - Net PnL | $282,1935 | $282,1935 | 0.0000 | - | - | **VERIFIED** |
| | - ROI | +28,22% | +28,22% | 0.00% | - | - | **VERIFIED** |
| | `skipped_signals` | 1.835 rows | 1.835 rows | 0 | 0 | **TRUE** | **VERIFIED** |
| | `rejected_signals` | 0 rows | 0 rows | 0 | 0 | **TRUE** | **VERIFIED** |
| | `snapshots.parquet` | 781.611 rows | 781.611 rows | 0 | - | **MATCH** | **VERIFIED** |
| | Ledger Differences | - | - | 0 cols diff | - | - | **VERIFIED** |

---

## 2. Chi Tiết Xác Minh Mode C Snapshots
- **File sinh ra**: `verify_head_f1f7f5b/BINGX_PORTFOLIO/snapshots.parquet`
- **File tham chiếu**: Ghi nhận trong `runs/golden/v3-08/BINGX_PORTFOLIO/ARTIFACTS.json`
- **Kích thước**: `23.619.083 bytes (22.52 MB)`
- **Số dòng**: `781.611`
- **SHA256 File**: `a02644b48fe0cec76bd00a58afb8acb3e40c18b81832c66197dc34ea8c7dca6a`
- **Initial Equity**: `$1.000,00`
- **Final Equity**: `$1.282,19` (Khớp Net PnL +$282.1935)
- **Min Equity**: `$965,34`
- **Max Equity**: `$1.324,65`
- **Trạng thái kiểm toán**: **VERIFIED** (100% Khớp với mốc kiểm toán ban đầu).

---

## 3. Khẳng Định Về Tính Toàn Vẹn Của Artifacts Tham Chiếu
- Thư mục `runs/golden/v3-08/` được giữ nguyên trạng thái chỉ đọc trong suốt quá trình chạy.
- Lệnh so sánh `compare_ledgers` đã thực hiện đọc độc lập từ `verify_head_f1f7f5b` và `runs/golden/v3-08`.
- Kết quả kiểm tra checksum và hash nội dung chứng minh commit `f1f7f5b` sinh ra kết quả **hoàn toàn đồng nhất (100% bit-for-bit content hash)** với Golden Baseline ban đầu.
