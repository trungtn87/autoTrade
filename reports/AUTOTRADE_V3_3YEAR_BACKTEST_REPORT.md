# Báo cáo Triển khai & Backtest AutoTrade V3 (3 Năm BTC/ETH M1)

**Thời điểm thực hiện:** 10/11/2026  
**Môi trường:** Windows Local (`D:\AutoTrade`)  
**Phạm vi:** Triển khai độc lập trên máy Windows, nạp dữ liệu M1 thực tế từ `D:/autotrade/data`, kiểm chứng tính toàn vẹn dữ liệu (Gate A), kiểm chứng parity 20 combo với V2 (Gate B1), đo độ nhạy nguồn sàn (Gate B2), chạy backtest signal replay 3 năm BTC/ETH, hoàn toàn offline và không can thiệp giao dịch thật.

---

## 1. Tóm tắt kết quả nghiệm thu

| Hạng mục kiểm tra | Tiêu chuẩn | Kết quả thực tế | Trạng thái |
|---|---|---|:---:|
| **Triển khai Windows Local** | Cài đặt Python 3.12, venv và dependencies tại `D:\AutoTrade` | Python 3.12.8, pip 24.3.1, duckdb 1.4.2, pandas 2.2.3, numpy 2.3.5 | **PASS** |
| **Tiếp nhận dữ liệu M1 (Gate A)** | 72 file Parquet 3 năm Binance + 2 file bù BingX 28/10/2026 | 3.156.480 nến M1 liên tục (1.578.240 BTC + 1.578.240 ETH), 0 gap, 0 duplicate, 0 lỗi OHLCV | **PASS** (`HYBRID_PASS`) |
| **Kiểm chứng Parity V2 (Gate B1)** | Đối chiếu 20 combo symbol với reference RUN17 chuẩn | 20/20 combo trùng khớp 100% cả `raw_events`, `passed_events`, `raw_sha256`, `passed_sha256` | **PASS** (0 sai lệch) |
| **Độ nhạy nguồn sàn (Gate B2)** | So sánh chuỗi tín hiệu giữa RUN17 và chuỗi M1 thực tế | - Cùng nguồn Binance (01/2024 - 04/2026): **Jaccard 100.0%** (4.692/4.692 trùng khớp)<br>- Toàn bộ giai đoạn giao nhau (01/2024 - 09/2026): **Jaccard 97.302%** | **PASS** (Đạt chuẩn) |
| **Replay Backtest 3 Năm** | Replay 20FINAL trên toàn bộ 3 năm (11/2023 - 11/2026 UTC) | 6.752 sự kiện raw, 6.199 tín hiệu Layer 2 approved (BTC: 2.704, ETH: 3.495), 5.984 tín hiệu sau warmup | **PASS** |
| **An toàn giao dịch thật** | Không gọi API live, không ghi Supabase live, không đụng engine VPS | Chạy 100% offline local trên DuckDB, SQLite/CSV nội bộ | **PASS** |

---

## 2. Chi tiết dữ liệu nến M1 thực tế (Gate A)

- **Thời gian bao phủ:** `2023-11-01 00:00:00 UTC` đến `2026-11-01 00:00:00 UTC` (1.096 ngày = 3 năm tròn, gồm năm nhuận 2024).
- **Tổng số nến M1:** 3.156.480 nến.
  - **BTCUSDT:** 1.578.240 nến (1.576.800 nến Binance USD-M + 1.440 nến BingX ngày 28/10/2026).
  - **ETHUSDT:** 1.578.240 nến (1.576.800 nến Binance USD-M + 1.440 nến BingX ngày 28/10/2026).
- **Kiểm tra tính liên tục:**
  - Khoảng cách giữa các nến: đúng 60.000 ms.
  - Nến thiếu (Missing): **0**.
  - Nến trùng (Duplicates): **0**.
  - Vi phạm OHLCV (High < Low, Open/Close ngoài dải, Volume < 0): **0**.
- **Cơ sở giá:** LAST price (hợp đồng USDT Perpetual).
- **Database Catalog:** Đã khởi tạo DuckDB tại `D:\AutoTrade\catalog\research.duckdb` với view `v3_m1`. File báo cáo QA: `D:\AutoTrade\catalog\v3_m1_qa.json`.

---

## 3. Kiểm chứng Parity với AutoTrade V2 (Gate B1)

Đối chiếu trực tiếp mã chiến lược 20FINAL (14 combo FINAL14 + 6 combo NEW6) trên cùng tập dữ liệu đầu vào RUN17 M15 chuẩn (200.544 nến/mã):

- **Số combo kiểm tra:** 20 combo (11 BTC-USDT, 9 ETH-USDT).
- **Khớp chuẩn:** 20/20 combo khớp chính xác tuyệt đối cả 4 trường:
  1. `raw_events` (Số lượng tín hiệu thô)
  2. `passed_events` (Số lượng tín hiệu vượt qua bộ lọc Layer 2)
  3. `raw_sha256` (Mã băm SHA256 danh sách sự kiện thô)
  4. `passed_sha256` (Mã băm SHA256 danh sách sự kiện Layer 2)
- **Sai lệch:** **0 khác biệt** (`difference_count: 0`).
- **File bằng chứng:** `D:\AutoTrade\reports\v3_b1_result.json`.

---

## 4. Đo độ nhạy nguồn sàn (Gate B2)

So sánh giữa tín hiệu tạo ra từ RUN17 (hybrid) và tín hiệu tạo ra từ bộ nến M1 chuẩn hóa:

| Giai đoạn (UTC) | Nguồn so sánh | RUN17 Baseline | M1 Derived | Tín hiệu chung | Chỉ có RUN17 | Chỉ có M1 | Chỉ số tương đồng (Jaccard) |
|---|---|---:|---:|---:|---:|---:|---:|
| **01/2024 – 04/2026** | Cùng sàn Binance Futures | 4.692 | 4.692 | **4.692** | 0 | 0 | **100.000%** |
| **04/2026 – 09/2026** | RUN17 BingX vs M1 Binance | 973 | 978 | **898** | 75 | 80 | **85.280%** |
| **01/2024 – 09/2026** | Toàn bộ giai đoạn giao nhau | 5.665 | 5.670 | **5.590** | 75 | 80 | **97.302%** |

*Ghi chú:* Khi cùng nguồn Binance, độ tương đồng là 100%. Khi khác nguồn sàn (BingX vs Binance), sự khác biệt nhẹ về biên độ râu nến tạo ra độ lệch kỳ vọng (85.28%), khẳng định mã nguồn không bị overfitting hay ép số giả tạo.

---

## 5. Kết quả Replay Backtest 3 Năm (2023 - 2026)

### Tổng quan tín hiệu:
- **Tổng sự kiện thô (Raw candidates):** 6.752
- **Tổng tín hiệu Layer 2 Approved:** 6.199
- **Tín hiệu sau Warmup (3.400 nến M15 đầu tiên):** 5.984
- **Chi tiết theo Symbol:**
  - **BTC-USDT:** 2.704 approved
  - **ETH-USDT:** 3.495 approved

### Phân bố theo từng combo:

| Mã | Combo | Khung thời gian gốc | Tín hiệu Approved | Bộ lọc Layer 2 |
|---|---|:---:|---:|---|
| **BTC-USDT** | C1 | 15m | 254 | OB |
| | C2 | 15m | 88 | SMC_STRICT_OB |
| | C4 | 15m | 282 | SMC_VETO_OB |
| | C6 | 15m | 120 | OB |
| | C7 | 15m | 229 | OB |
| | C9 | 15m | 81 | SMC_STRICT_OB |
| | C10 | 15m | 46 | SMC_STRICT_OB |
| | TIER (C11) | 15m | 194 | SMC_STRICT_OB |
| | C11 (N1) | 1h | 901 | SMC_STRICT_OB |
| | C14 (N4) | 1h | 326 | OB |
| | C16 (N6) | 1h | 183 | OB |
| **ETH-USDT** | C1 | 15m | 278 | OB |
| | C3 | 15m | 98 | SMC_STRICT_OB |
| | C4 | 15m | 276 | SMC_VETO_OB |
| | C6 | 15m | 81 | OB |
| | C7 | 15m | 293 | OB |
| | C10 | 15m | 13 | SMC_STRICT_OB |
| | C12 (N2) | 1h | 475 | SMC_VETO_OB |
| | C13 (N3) | 1h | 305 | OFF |
| | C15 (N5) | 1h | 1.676 | OB |

- **File chi tiết từng sự kiện:** `D:\AutoTrade\runs\3year_replay\signal_events.csv` (573 KB).
- **File báo cáo JSON tổng hợp:** `D:\AutoTrade\runs\3year_replay\replay_report.json` (105 KB).

---

## 6. Hướng dẫn chạy độc lập trên Windows (Ngoài Antigravity)

Hệ thống đã được thiết lập sẵn sàng để bạn có thể chạy bất kỳ lúc nào từ Windows Terminal / Command Prompt hoặc click chuột:

1. **Chạy Backtest 3 năm (1-Click):**
   - Click đúp vào file: `D:\AutoTrade\run_backtest_3year.bat`
   - Hoặc mở PowerShell tại `D:\AutoTrade` và gõ:
     ```powershell
     .\.venv\Scripts\python.exe -m autotrade_v3.data_backtest.replay_20final --catalog "catalog/research.duckdb" --out-dir "runs/3year_replay" --start "2023-11-01" --end "2026-11-01"
     ```

2. **Kiểm tra Gate B1 Parity với V2 (1-Click):**
   - Click đúp vào file: `D:\AutoTrade\verify_parity_v2.bat`

3. **Kiểm tra Gate B2 Độ nhạy nguồn sàn (1-Click):**
   - Click đúp vào file: `D:\AutoTrade\run_source_sensitivity.bat`
