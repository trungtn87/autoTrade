# AutoTrade V3 Research Lab — Báo Cáo Minh Chứng Nghiệm Thu V3-09
## Reporting & Accounting Corrections Audit Report

- **Dự án**: AutoTrade V3 Research Lab
- **Mã triển khai**: V3-09 (Reporting & Accounting Corrections)
- **Người thực hiện**: Antigravity (Advanced Agentic AI)
- **Người review**: Claude
- **Người nghiệm thu & merge**: Owner
- **Thời gian lập báo cáo**: 2026-11-11T11:35:00+07:00

---

## A. Tóm Tắt Thực Hiện

| Hạng mục | Chi tiết |
|:---|:---|
| **Repository** | https://github.com/trungtn87/autoTrade |
| **Nhánh làm việc** | `feat/v3-09-reporting` |
| **Base Commit** | `726fd52b0c86be8bceae6fd9c64dbef789d3140a` (Merge commit nghiệm thu V3-08 vào main) |
| **Commit 1 (Analyzer & PF)** | `9d7a3cf` — `feat(analysis): implement 20-combo breakdown, 4 canonical profit factors, unrounded precision and 3-level reconciliation` |
| **Commit 2 (Verify Tool & Legacy)** | `10f4e81` — `feat(reporting): add independent verify_report tool and clean legacy reporting scripts` |
| **Commit 3 (Engine Bugfixes)** | `ba404fc` — `fix(engine): resolve trailing_ambiguous unbound state on max_bars_held zero and harden to_utc_ms` |
| **Commit 4 (Evidence & Docs)** | *(Commit hiện tại)* — `docs(v3-09): deliver evidence report, verification logs, and updated lab artifacts` |

---

## B. Bảng Mốc Đối Chiếu Lịch Sử (Run-Level Totals vs Golden Baseline V3-08)

Các chỉ số cấp run được đối chiếu trực tiếp giữa đầu ra tái lập trên HEAD và `runs/golden/v3-08/` bằng công cụ `run_head_verification.py`:

| Chỉ số / Chế độ | Golden Baseline V3-08 | Tái lập V3-09 | Sai lệch | Trạng thái xác minh |
|:---|:---:|:---:|:---:|:---:|
| **Tín hiệu Tổng (Signals)** | 5.984 | 5.984 | 0 | **VERIFIED** |
| - BTC-USDT | 2.575 | 2.575 | 0 | **VERIFIED** |
| - ETH-USDT | 3.409 | 3.409 | 0 | **VERIFIED** |
| **Mode A (ALL_SIGNALS)** | | | | |
| - Accepted Entries | 5.984 | 5.984 | 0 | **VERIFIED** |
| - Executed Legs | 8.258 | 8.258 | 0 | **VERIFIED** |
| - Gross PnL ($) | 1.009,0873 | 1.009,0873 | 0.0000 | **VERIFIED** |
| - Total Fees ($) | 598,3866 | 598,3866 | 0.0000 | **VERIFIED** |
| - Net PnL ($) | 410,7007 | 410,7007 | 0.0000 | **VERIFIED** |
| - Win Rate (Trade / Leg) | 38,72% / 42,75% | 38,72% / 42,75% | 0.00% | **VERIFIED** |
| - Trades Parquet Content Hash | `9ca9215cd1c9f32e...` | `9ca9215cd1c9f32e...` | Trùng 100% | **VERIFIED** |
| **Mode B (ONE_TRADE_PER_COMBO)** | | | | |
| - Accepted Entries | 4.156 | 4.156 | 0 | **VERIFIED** |
| - Executed Legs | 6.273 | 6.273 | 0 | **VERIFIED** |
| - Skipped by Concurrency | 1.828 | 1.828 | 0 | **VERIFIED** |
| - Gross PnL ($) | 699,8388 | 699,8388 | 0.0000 | **VERIFIED** |
| - Total Fees ($) | 415,6443 | 415,6443 | 0.0000 | **VERIFIED** |
| - Net PnL ($) | 284,1945 | 284,1945 | 0.0000 | **VERIFIED** |
| - Win Rate (Trade / Leg) | 39,61% / 44,30% | 39,61% / 44,30% | 0.00% | **VERIFIED** |
| - Trades Parquet Content Hash | `6fb3c2f2491a9e57...` | `6fb3c2f2491a9e57...` | Trùng 100% | **VERIFIED** |
| **Mode C (BINGX_PORTFOLIO)** | | | | |
| - Accepted Entries | 4.149 | 4.149 | 0 | **VERIFIED** |
| - Executed Legs | 6.265 | 6.265 | 0 | **VERIFIED** |
| - Skipped by Concurrency | 1.835 | 1.835 | 0 | **VERIFIED** |
| - Gross PnL ($) | 697,1388 | 697,1388 | 0.0000 | **VERIFIED** |
| - Total Fees ($) | 414,9453 | 414,9453 | 0.0000 | **VERIFIED** |
| - Net PnL ($) | 282,1935 | 282,1935 | 0.0000 | **VERIFIED** |
| - Win Rate (Trade / Leg) | 40,85% / 45,06% | 40,85% / 45,06% | 0.00% | **VERIFIED** |
| - Continuous M2M Max Drawdown ($) | $49,01 (3,70%) | $49,01 (3,70%) | 0.00 | **VERIFIED** |
| - Trades Parquet Content Hash | `779fac41367787ec...` | `779fac41367787ec...` | Trùng 100% | **VERIFIED** |
| - Snapshots SHA256 Match | `a02644b48fe0cec7...` | `a02644b48fe0cec7...` | Trùng 100% | **VERIFIED** |

---

## C. Tuân Thủ 10 Nguyên Tắc Bất Biến (Invariants Verification)

1. **N1 (autotrade_v2 Freeze):**
   - Lệnh kiểm tra: `git diff --stat 726fd52b0c86be8bceae6fd9c64dbef789d3140a..HEAD -- autotrade_v2`
   - **Kết quả:** RỖNG (0 files changed, 0 insertions, 0 deletions).
2. **N2 (Hoàn toàn Offline, không gọi mạng/sàn):**
   - 100% logic chạy qua DuckDB catalog và local parquet. Không có API call BingX, Supabase, Gemini hay mạng ngoài.
3. **N3 (Dữ liệu M1 gốc chỉ đọc):**
   - DuckDB connection mở ở chế độ `read_only=True`. Mọi phép thử chạy trên thư mục tạm.
4. **N4 (Không sửa file tham chiếu / golden):**
   - Thư mục `runs/golden/v3-08/` được bảo tồn nguyên vẹn 100%. `git diff --stat 726fd52..HEAD -- runs/golden` là RỖNG.
5. **N5 (Không gõ tay số liệu hoặc chữ PASS):**
   - Mọi số liệu trong báo cáo được trích xuất động trực tiếp từ ledger.
   - `test_no_hardcoded_metrics_in_reporting_code` quét toàn bộ mã dựng báo cáo và xác nhận 0 vi phạm (xem `evidence/v3-09/hardcode_scan.log`).
6. **N6 (Tái lập và giải trình sai lệch):**
   - Kết quả chạy lại trùng khớp hoàn toàn với mốc golden V3-08.
7. **N7 (Nhãn SYNTHETIC):**
   - Dữ liệu mock phục vụ unit test được gắn nhãn `provider="SYNTHETIC"`.
8. **N8 (Không thiếu phụ thuộc):**
   - Phụ thuộc V3-08 đã được nghiệm thu và merge vào Base commit `726fd52`.
9. **N9 (Gắn nhãn kỹ thuật):**
   - Mọi khẳng định kỹ thuật đều mang nhãn rõ ràng: VERIFIED, PARTIALLY_VERIFIED, UNVERIFIED, FAILED.
10. **N10 (Một PR, một nhánh):**
    - Nhánh duy nhất `feat/v3-09-reporting`, không commit trực tiếp main, không rebase, không force push.

---

## D. Bảng Đối Chiếu Trước và Sau Sửa Đổi (Before vs After)

| Hạng mục / Chỉ số | Trạng thái Trước Sửa Đổi (Before) | Trạng thái Sau Sửa Đổi (After) | Nguồn Dữ Liệu Minh Chứng |
|:---|:---|:---|:---|
| **Khóa dòng combo (`combo_key`)** | `combo_id` (dạng số nguyên 1, 2, 4...) -> Va chạm giữa BTC và ETH | `combo_key = f"{symbol}/{combo_name}"` (ví dụ `BTC-USDT/C1`, `ETH-USDT/C1`) | [metrics.py](file:///d:/AutoTrade/autotrade_v3/analysis/metrics.py#L350), [research_analyzer.py](file:///d:/AutoTrade/autotrade_v3/analysis/research_analyzer.py#L200) |
| **Số lượng dòng combo trong `analysis.json`** | **15 dòng** (P1-4: Combo 1, 4, 6, 7, 10 bị gộp giữa BTC và ETH) | **20 dòng** (11 dòng BTC-USDT, 9 dòng ETH-USDT, tách biệt hoàn toàn) | Trước: [`evidence/v3-09/before/combo_count_before.txt`](file:///d:/AutoTrade/evidence/v3-09/before/combo_count_before.txt)<br>Sau: [`evidence/v3-09/reports/ALL_SIGNALS/analysis.json`](file:///d:/AutoTrade/evidence/v3-09/reports/ALL_SIGNALS/analysis.json) |
| **Phân định Profit Factor** | Chỉ có 1 trường `profit_factor` tính theo leg, nhưng trình bày như PF chiến lược | Tách thành **4 Profit Factors**: `net_pf_trade`, `net_pf_leg`, `gross_pf_trade`, `gross_pf_leg`. Trường cũ giữ nguyên giá trị kèm `profit_factor_basis = "leg"` | [metrics.py](file:///d:/AutoTrade/autotrade_v3/analysis/metrics.py#L257-L320), [`analysis.json`](file:///d:/AutoTrade/evidence/v3-09/reports/ALL_SIGNALS/analysis.json) |
| - Mode A Net PF (Trade vs Leg) | 1,13 (không phân định) | `net_pf_trade: 1.14`, `net_pf_leg: 1.13` | [`reports/ALL_SIGNALS/analysis.json`](file:///d:/AutoTrade/evidence/v3-09/reports/ALL_SIGNALS/analysis.json) |
| - Mode B Net PF (Trade vs Leg) | 1,14 (không phân định) | `net_pf_trade: 1.14`, `net_pf_leg: 1.14` | [`reports/ONE_TRADE_PER_COMBO/analysis.json`](file:///d:/AutoTrade/evidence/v3-09/reports/ONE_TRADE_PER_COMBO/analysis.json) |
| - Mode C Net PF (Trade vs Leg) | 1,14 (không phân định) | `net_pf_trade: 1.15`, `net_pf_leg: 1.14` | [`reports/BINGX_PORTFOLIO/analysis.json`](file:///d:/AutoTrade/evidence/v3-09/reports/BINGX_PORTFOLIO/analysis.json) |
| **Độ chính xác số học (Accounting Precision)** | Làm tròn 4 chữ số thập phân (`round(..., 4)`), gây sai số tích lũy khi cộng dồn | Bổ sung giá trị unrounded (`gross_pnl_unrounded`, `fees_unrounded`, `net_pnl_unrounded`) chuẩn IEEE-754 ở mọi cấp | [research_analyzer.py](file:///d:/AutoTrade/autotrade_v3/analysis/research_analyzer.py#L100-L130), [`analysis.json`](file:///d:/AutoTrade/evidence/v3-09/reports/ALL_SIGNALS/analysis.json) |
| **Kiểm toán đối chiếu ba cấp (`reconciliation`)** | Không có khối kiểm toán tự động đối chiếu Run == Symbol Sum == Combo Sum | Tự động kiểm toán đối chiếu 6 chỉ tiêu (signals, entries, legs, gross, fees, net) với sai số < `1e-9` (thực tế `1.71e-13`) | Khối `reconciliation` trong [`analysis.json`](file:///d:/AutoTrade/evidence/v3-09/reports/ALL_SIGNALS/analysis.json) |
| **Drawdown trong `compare_runs`** | Trả về `None` cho `closed_trade_max_drawdown` của Mode A và B (P3-3) | Tách biệt hoàn toàn `closed_trade_max_drawdown` và `m2m_max_drawdown`. Mode A & B báo Closed DD đầy đủ, Mode C báo cả Closed DD và Continuous M2M DD | [research_analyzer.py](file:///d:/AutoTrade/autotrade_v3/analysis/research_analyzer.py#L480-L550) |
| **Tính toán độc lập báo cáo (`verify_report.py`)** | Chưa có công cụ độc lập; báo cáo phụ thuộc vào bộ nhớ simulator | Viết mới [`tools/verify_report.py`](file:///d:/AutoTrade/autotrade_v3/tools/verify_report.py), đọc trực tiếp `report.md` và `trades.parquet`, tính lại 110 chỉ số độc lập | [`evidence/v3-09/verify_report_*.log`](file:///d:/AutoTrade/evidence/v3-09/) |
| **Mã lịch sử gõ cứng số liệu** | Gõ cứng `TP 2.827, SL 1.488, TRAIL 1.947`, `"status": "PASS"`, default `5984` | Thay bằng biến động từ artifact; gắn banner cảnh báo lịch sử V3-03 đến V3-06 | [reconcile_runs.py](file:///d:/AutoTrade/scripts/reconcile_runs.py), [reporter.py](file:///d:/AutoTrade/autotrade_v3/reports/reporter.py), [run_v3_backtest.py](file:///d:/AutoTrade/scripts/run_v3_backtest.py) |
| **Lỗi `to_utc_ms` (P3-1)** | Nhánh datetime không múi giờ dùng biến `dt` chưa gán (lỗi lịch sử từ V3-04) | **Đã được sửa từ trước** tại commit `34c299f` (trong V3-08), V3-09 chỉ xác minh lại và bổ sung unit test `test_to_utc_ms_all_timestamp_types` bảo vệ hồi quy (0 dòng diff so với Base) | [candle_loader.py](file:///d:/AutoTrade/autotrade_v3/engine/candle_loader.py#L16-L36) |
| **Lỗi `_simulate_trailing_leg` (P3-2)** | Biến `trailing_ambiguous` bị UnboundLocalError khi `max_bars_held=0` | Khởi tạo `trailing_ambiguous = False` trước vòng lặp nến | [execution_simulator.py](file:///d:/AutoTrade/autotrade_v3/engine/execution_simulator.py#L640) |

---

## E. Checksum & Content Hash Ledger

Kết quả đối chiếu ledger giữa bản chạy tái lập V3-09 và Golden Baseline V3-08 (lưu nguyên văn tại [`evidence/v3-09/ledger_hash_compare.log`](file:///d:/AutoTrade/evidence/v3-09/ledger_hash_compare.log)):

| Chế độ | Artifact | Kích thước (Bytes) | Content Hash (V3-08 Golden) | Content Hash (V3-09 Head) | Khớp 100% |
|:---|:---|---:|:---|:---|:---:|
| **ALL_SIGNALS** | `signals.parquet` | 364.669 | `52ed9b559dfba752...` | `52ed9b559dfba752...` | **TRUE** |
| | `trades.parquet` | 683.969 | `9ca9215cd1c9f32e...` | `9ca9215cd1c9f32e...` | **TRUE** |
| | `skipped_signals.parquet` | 4.411 | `206e84787e2cc2d0...` | `206e84787e2cc2d0...` | **TRUE** |
| | `rejected_signals.parquet` | 5.692 | `245de4d472e8c3ee...` | `245de4d472e8c3ee...` | **TRUE** |
| **ONE_TRADE_PER_COMBO** | `signals.parquet` | 364.669 | `52ed9b559dfba752...` | `52ed9b559dfba752...` | **TRUE** |
| | `trades.parquet` | 525.751 | `6fb3c2f2491a9e57...` | `6fb3c2f2491a9e57...` | **TRUE** |
| | `skipped_signals.parquet` | 46.730 | `6f49c7d657cfa137...` | `6f49c7d657cfa137...` | **TRUE** |
| | `rejected_signals.parquet` | 5.692 | `245de4d472e8c3ee...` | `245de4d472e8c3ee...` | **TRUE** |
| **BINGX_PORTFOLIO** | `signals.parquet` | 364.669 | `52ed9b559dfba752...` | `52ed9b559dfba752...` | **TRUE** |
| | `trades.parquet` | 560.163 | `779fac41367787ec...` | `779fac41367787ec...` | **TRUE** |
| | `skipped_signals.parquet` | 47.489 | `b585ce8eb2c9ecd2...` | `b585ce8eb2c9ecd2...` | **TRUE** |
| | `rejected_signals.parquet` | 5.692 | `245de4d472e8c3ee...` | `245de4d472e8c3ee...` | **TRUE** |
| | `snapshots.parquet` (SHA256) | 23.619.083 | `a02644b48fe0cec7...` | `a02644b48fe0cec7...` | **TRUE** |

---

## F. Phân Tích 20 Combos & Khối Reconciliation 3 Cấp (AC1, AC2)

### 1. Danh sách 20 Combos Chuẩn Hóa Theo `combo_key`
Cấu hình baseline 20FINAL được cấu thành từ 11 combo BTC-USDT và 9 combo ETH-USDT:

| STT | Combo Key | Symbol | Combo ID | Combo Name | Timeframe | Type |
|:---:|:---|:---|:---:|:---|:---:|:---:|
| 1 | `BTC-USDT/C1` | BTC-USDT | 1 | C1 | 1h | FINAL14 |
| 2 | `BTC-USDT/C2` | BTC-USDT | 2 | C2 | 1h | FINAL14 |
| 3 | `BTC-USDT/C4` | BTC-USDT | 4 | C4 | 1h | FINAL14 |
| 4 | `BTC-USDT/C6` | BTC-USDT | 6 | C6 | 1h | FINAL14 |
| 5 | `BTC-USDT/C7` | BTC-USDT | 7 | C7 | 15m | FINAL14 |
| 6 | `BTC-USDT/C9` | BTC-USDT | 9 | C9 | 15m | FINAL14 |
| 7 | `BTC-USDT/C10` | BTC-USDT | 10 | C10 | 15m | FINAL14 |
| 8 | `BTC-USDT/C11` | BTC-USDT | 101 | C11 | 1h | NEW6 |
| 9 | `BTC-USDT/C14` | BTC-USDT | 104 | C14 | 1h | NEW6 |
| 10 | `BTC-USDT/C16` | BTC-USDT | 106 | C16 | 1h | NEW6 |
| 11 | `BTC-USDT/TIER` | BTC-USDT | 11 | TIER | 1h | FINAL14 |
| 12 | `ETH-USDT/C1` | ETH-USDT | 1 | C1 | 1h | FINAL14 |
| 13 | `ETH-USDT/C3` | ETH-USDT | 3 | C3 | 1h | FINAL14 |
| 14 | `ETH-USDT/C4` | ETH-USDT | 4 | C4 | 1h | FINAL14 |
| 15 | `ETH-USDT/C6` | ETH-USDT | 6 | C6 | 1h | FINAL14 |
| 16 | `ETH-USDT/C7` | ETH-USDT | 7 | C7 | 15m | FINAL14 |
| 17 | `ETH-USDT/C10` | ETH-USDT | 10 | C10 | 15m | FINAL14 |
| 18 | `ETH-USDT/C12` | ETH-USDT | 102 | C12 | 1h | NEW6 |
| 19 | `ETH-USDT/C13` | ETH-USDT | 103 | C13 | 1h | NEW6 |
| 20 | `ETH-USDT/C15` | ETH-USDT | 105 | C15 | 1h | NEW6 |

### 2. Kiểm Toán Đối Chiếu Ba Cấp (Reconciliation Audit)
Khối reconciliation trong `analysis.json` của cả 3 chế độ chạy đều ghi nhận:
- `reconciliation_pass: true` (dung sai quy định: `1e-9`, sai số thực tế lớn nhất ghi nhận: `1.71e-13`).
- `symbol_combo_alignment_pass: true` (100% leg thuộc đúng cấu hình symbol-combo đã định nghĩa).

---

## G. 4 Chuẩn Mực Profit Factor (AC3)

Tất cả 5 cấp phân tích (`run`, `symbol`, `combo`, `month`, `subwindow`) đều cung cấp đầy đủ 4 Profit Factors:
1. `net_pf_trade`: Tổng Net PnL các trade thắng chia cho giá trị tuyệt đối tổng Net PnL các trade thua (trong đó trade PnL = tổng net các legs cùng `trade_id`).
2. `net_pf_leg`: Tổng Net PnL các leg thắng chia cho giá trị tuyệt đối tổng Net PnL các leg thua.
3. `gross_pf_trade`: Tổng Gross PnL các trade thắng chia cho giá trị tuyệt đối tổng Gross PnL các trade thua.
4. `gross_pf_leg`: Tổng Gross PnL các leg thắng chia cho giá trị tuyệt đối tổng Gross PnL các leg thua.

Bảng tổng hợp cấp Run và Symbol trên Mode BINGX_PORTFOLIO:

| Phân Vùng | Accepted Entries | Executed Legs | Net PnL ($) | Win Rate (Trade) | Win Rate (Leg) | Net PF (Trade) | Net PF (Leg) | Gross PF (Trade) | Gross PF (Leg) |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Toàn Run** | 4.149 | 6.265 | $282.19 | 40.85% | 45.06% | **1.15** | **1.14** | **1.42** | **1.41** |
| **BTC-USDT** | 1.861 | 3.021 | $138.53 | 41.59% | 47.90% | **1.15** | **1.15** | **1.43** | **1.42** |
| **ETH-USDT** | 2.288 | 3.244 | $143.66 | 40.25% | 42.42% | **1.14** | **1.13** | **1.41** | **1.40** |

---

## H. Báo Cáo Thẩm Định Độc Lập verify_report.py (AC4) & Quét Mã (AC5)

1. **Thẩm định Độc Lập Báo Cáo (`tools/verify_report.py`):**
   - Đọc trực tiếp `report.md` và `trades.parquet` không qua bộ nhớ trung gian.
   - Tính lại độc lập 110 chỉ số trong báo cáo: Entries, Legs, Gross, Fees, Net PnL, Win Rates, 4 Profit Factors, Max Drawdowns, bảng 20 Combos, bảng Exit Reasons.
   - **Kết quả:** Thoát mã 0 (SUCCESS) trên cả 3 chế độ chạy.
   - Logs nguyên văn:
     - [`evidence/v3-09/verify_report_ALL_SIGNALS.log`](file:///d:/AutoTrade/evidence/v3-09/verify_report_ALL_SIGNALS.log)
     - [`evidence/v3-09/verify_report_ONE_TRADE_PER_COMBO.log`](file:///d:/AutoTrade/evidence/v3-09/verify_report_ONE_TRADE_PER_COMBO.log)
     - [`evidence/v3-09/verify_report_BINGX_PORTFOLIO.log`](file:///d:/AutoTrade/evidence/v3-09/verify_report_BINGX_PORTFOLIO.log)
2. **Kiểm tra Quét Mã Loại Bỏ Hằng Số Chỉ Số (AC5):**
   - Script kiểm tra độc lập và test `test_no_hardcoded_metrics_in_reporting_code` đã quét toàn bộ:
     - `autotrade_v3/lab/run_manager.py`
     - `autotrade_v3/reports/reporter.py`
     - `scripts/reconcile_runs.py`
     - `scripts/run_v3_backtest.py`
   - Xác nhận: 0 hằng số 5984, 2827, 1488, 1947, 343.52, 6271 hay chuỗi `'status': 'PASS'` gõ cứng.
   - Log nguyên văn: [`evidence/v3-09/hardcode_scan.log`](file:///d:/AutoTrade/evidence/v3-09/hardcode_scan.log).

---

## I. Kết Quả Kiểm Thử (Unit, Integration, Regression, Adversarial)

### 1. Bộ Kiểm Thử Đầy Đủ
```
======================= 180 passed in 75.38s (0:01:15) ========================
```
- **Tổng số tests:** 180
- **Passed:** 180 (100%)
- **Failed:** 0
- **Skipped:** 0

### 2. Kiểm Thử Đột Biến Đối Kháng (Adversarial Mutations M1-M4)
- **M1 (Sửa số trong report.md):** `verify_report.py` phát hiện sai lệch và thoát mã lỗi khác 0 (`test_adversarial_mutation_m1_report_number_changed: PASSED`).
- **M2 (Sửa net_pnl trong trades.parquet):** `verify_report.py` phát hiện bất nhất giữa ledger và báo cáo cũ (`test_adversarial_mutation_m2_trade_net_pnl_changed: PASSED`).
- **M3 (Đổi symbol của leg):** Khối reconciliation phát hiện `symbol_combo_alignment_pass == False` (`test_adversarial_mutation_m3_symbol_changed: PASSED`).
- **M4 (Quét hằng số chỉ số):** Đột biến chèn 5984 vào mã giả lập kích hoạt scanner bắt lỗi thành công (`test_adversarial_mutation_m4_hardcoded_metric_scan: PASSED`).

### 3. Linter & Compiler
- `ruff check autotrade_v3 scripts`: **ALL CHECKS PASSED** (0 errors).
- `python -m compileall autotrade_v3 scripts`: **ALL COMPILED** (0 syntax/compilation errors).
