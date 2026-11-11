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
| **Commit 4 (Evidence & Docs)** | `b737204` — `docs(v3-09): deliver evidence report, verification logs, and updated lab artifacts` |
| **Commit 5 (Harden Verifier & Mutations)** | `ab65225` — `fix(v3-09): harden report verifier with strict tolerances, mandatory tables, and comprehensive mutation tests` |
| **Commit 6 (Fix Audit Regression & Fail-Closed)** | `fix(v3-09): restore audit validation data, enforce fail-closed verification, and add regression tests` |

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
   - Đọc trực tiếp `report.md` và `trades.parquet` (cùng companion ledgers `signals.parquet`, `skipped_signals.parquet`, `rejected_signals.parquet`) không qua bộ nhớ trung gian.
   - Kiểm tra độc lập **179 chỉ số** trong mỗi báo cáo:
     - **Validation Gates Audit (FAIL-CLOSED)**: Bảng kiểm toán Gate A, B, C, D là bắt buộc, trạng thái phải là PASS. Thiếu Gate hoặc bảng kiểm toán -> lập tức FAIL.
     - **Tính nhất quán dữ liệu thật**: Cấm tuyệt đối trạng thái `REAL_DATA_PASS` đi kèm `Real Market Data: False`.
     - **Kiểm tra Ambiguity**: Số lượng TP/SL ambiguity và trailing ambiguity được tính độc lập trực tiếp từ các trường boolean `intrabar_ambiguous` và `intrabar_trailing_ambiguous` trong `trades.parquet` và đối chiếu với báo cáo.
     - **Companion Ledgers (Bắt buộc)**: Thiếu bất kỳ file nào trong số `signals.parquet`, `skipped_signals.parquet`, `rejected_signals.parquet` -> lập tức FAIL (`MISSING_COMPANION_LEDGER`).
     - **Đối chiếu 3 cấp độc lập**: Tự tính tổng net PnL run, tổng net PnL theo symbol, tổng net PnL theo combo trực tiếp trên `trades.parquet` với dung sai sai số $< 10^{-9}$.
     - **Toàn bộ 4 Profit Factors**: `Net Profit Factor (Trade)`, `Net Profit Factor (Leg)`, `Gross Profit Factor (Trade)`, `Gross Profit Factor (Leg)`.
     - **Độ chính xác tiền tệ và phần trăm**: `Gross PnL`, `Total Fees`, `Net PnL`, `ROI (%)` với dung sai số học nghiêm ngặt `tol = 0.009` (bác bỏ tuyệt đối sai lệch $0.04 USD).
     - **Bắt buộc có đầy đủ các bảng**: `## Validation Gates Audit`, `## 5. Performance by Symbol`, `## 6. Exit Reason Breakdown`, `## 7. Performance by Combo` (20 combos độc lập), `## 8. Reconciliation Audit`.
   - **Kết quả:** Thoát mã 0 (SUCCESS) trên cả 3 chế độ chạy, xác minh 179/179 metrics mỗi mode.
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
======================= 192 passed in 135.91s (0:02:15) =======================
```
- **Tổng số tests:** 192 (toàn bộ test suite autotrade_v3)
- **Passed:** 192 (100%)
- **Failed:** 0
- **Skipped:** 0
- Riêng bộ test V3-09 (`test_reporting_corrections_v309.py`): **28/28 tests PASSED**.

### 2. Kiểm Thử Đột Biến Đối Kháng Toàn Diện (Adversarial Mutations)
Bộ kiểm thử đột biến được bổ sung đầy đủ theo yêu cầu lệnh sửa đổi:
- **Thay đổi Gross PF Trade:** `test_adversarial_mutation_gross_pf_trade` -> `verify_report` phát hiện sai lệch và FAIL (**PASSED**).
- **Thay đổi Gross PF Leg:** `test_adversarial_mutation_gross_pf_leg` -> `verify_report` phát hiện sai lệch và FAIL (**PASSED**).
- **Thay đổi ROI:** `test_adversarial_mutation_roi` -> `verify_report` phát hiện sai lệch và FAIL (**PASSED**).
- **Xóa bảng Combo (`## 7. Performance by Combo`):** `test_adversarial_mutation_delete_combo_table` -> `verify_report` bắt buộc kiểm tra và FAIL (**PASSED**).
- **Xóa bảng Exit Reasons (`## 6. Exit Reason Breakdown`):** `test_adversarial_mutation_delete_exit_reason_table` -> `verify_report` bắt buộc kiểm tra và FAIL (**PASSED**).
- **Thay đổi Net PnL $0.04 USD:** `test_adversarial_mutation_pnl_004_usd` ($410.70 -> $410.74) -> `verify_report` với `tol = 0.009` phát hiện chênh lệch 0.04 và FAIL (**PASSED**).
- **Xóa bảng Gates (`## Validation Gates Audit`):** `test_adversarial_mutation_delete_gates_table` -> `verify_report` bắt buộc kiểm tra và FAIL (**PASSED**).
- **Mâu thuẫn Real Market Data (REAL_DATA_PASS + False):** `test_adversarial_mutation_real_data_false_with_real_pass` -> `verify_report` phát hiện mâu thuẫn và FAIL (**PASSED**).
- **Làm sai lệch TP/SL Ambiguity (3 -> 0):** `test_adversarial_mutation_tp_sl_ambiguity_zero` -> `verify_report` phát hiện lệch và FAIL (**PASSED**).
- **Làm sai lệch Trailing Ambiguity (486/459 -> 0):** `test_adversarial_mutation_trailing_ambiguity_zero` -> `verify_report` phát hiện lệch và FAIL (**PASSED**).
- **Thiếu Companion Ledger (`signals.parquet`):** `test_adversarial_mutation_missing_companion_ledger` -> `verify_report` bắt buộc kiểm tra và FAIL (**PASSED**).
- **Kiểm tra hồi quy bảo toàn Audit Validation & Ambiguity:** `test_regression_validation_and_ambiguity_preservation` -> tái tạo kết quả simulation bảo toàn 100% Gate A/B/C/D, `is_real_data=True` và bộ đếm ambiguity (**PASSED**).

### 3. Linter & Compiler
- `ruff check autotrade_v3 scripts`: **ALL CHECKS PASSED** (0 errors).
- `python -m compileall autotrade_v3 scripts`: **ALL COMPILED** (0 syntax/compilation errors).

---

## J. Giải Trình Lịch Sử Commit & Trạng Thái Cuối Cùng

Nhằm giải đáp thắc mắc về các commit SHA xuất hiện trong quá trình phát triển trên nhánh `feat/v3-09-reporting`:
1. `ba404fc` là Commit 3 hoàn chỉnh (`fix(engine): resolve trailing_ambiguous unbound state...`).
2. Trong quá trình đóng gói Commit 4 trên local:
   - Bản nháp đầu tiên mang commit SHA `91b6b77` / `ff50fcc`.
   - Sau đó commit được amend để cập nhật ghi chú về P3-1 (commit `b3543df`).
   - Tiếp tục amend lần cuối để đồng bộ `git_state.txt` tạo thành commit `b7372040`.
   - `b7372040` chính là HEAD SHA đầu tiên được push lên remote GitHub mở PR #100.
3. Commit 5 (`ab65225c`): Thắt chặt verifier với dung sai số học nghiêm ngặt 0.009, bắt buộc có các bảng thống kê và bổ sung kiểm thử đột biến.
4. Commit 6 (Hiện tại): Khắc phục toàn diện hồi quy dữ liệu kiểm toán, khôi phục Gate A/B/C/D, sửa dòng `Real Market Data`, đồng bộ bộ đếm TP/SL & Trailing Ambiguity từ ledger gốc `trades.parquet`, loại bỏ lỗi reset `discrepancies` tại dòng 298 của `verify_report.py`, và bổ sung 6 bài kiểm thử hồi quy & đối kháng mới (nâng tổng số bài kiểm thử V3-09 lên 28/28 và toàn repo lên 192/192).

> [!NOTE] Làm Rõ Về `git_state.txt`
> File `evidence/v3-09/git_state.txt` là bản chụp (snapshot) trạng thái working tree trước commit nhằm mục đích ghi lại các file đang được thay đổi trước khi đóng gói. Trạng thái HEAD sạch cuối cùng (clean working tree) được chứng minh và xác nhận hoàn toàn độc lập thông qua kết quả thực thi của GitHub Actions CI trên remote GitHub và nhật ký `git status` sau khi commit.

---

## K. Báo Cáo Khắc Phục Hồi Quy Kiểm Toán Báo Cáo (Review Claude)

### 1. Nguyên Nhân Gốc Rễ (Root Cause)
1. **Mất trường `validation` và bảng Gate A/B/C/D:**
   `ResearchAnalyzer.analyze_simulation()` chịu trách nhiệm phân tích hiệu năng sau mô phỏng, nhưng trước đó không tiếp nhận khối từ điển `validation` (thuộc khâu kiểm toán môi trường dữ liệu và chiến lược). Khi `analysis.json` được ghi đè bởi `analyze_simulation()`, thông tin `validation` bị khuyết. Hệ quả là khi `RunManager._render_markdown_report()` kết xuất Markdown, `val.get("is_real_data", False)` trả về `False` và khối `gates_dict` rỗng khiến bảng kiểm toán Gate A/B/C/D không được in ra.
2. **Bộ đếm Ambiguity về 0:**
   Khi tái dựng đối tượng `SimulationResult` từ file ledger trong quá trình tạo lại báo cáo, trường `ambiguity_stats` không được tự động tính toán từ các cột boolean có sẵn trong `trades.parquet` (`intrabar_ambiguous` và `intrabar_trailing_ambiguous`), dẫn đến số lượng trả về mặc định là 0.
3. **`verify_report.py` không bắt được lỗi thiếu Gate:**
   Tại dòng 298 của `verify_report.py` có một dòng lệnh khởi tạo lại danh sách `discrepancies: list[str] = []` ngay sau bước kiểm tra companion ledgers. Lệnh này đã vô tình xóa sạch mọi lỗi phát hiện được ở phần kiểm tra Section 0 (Validation Status và Gates Audit) trước khi tổng kết.

### 2. Các Biện Pháp Đã Triển Khai Khắc Phục Triệt Để
1. **Nâng cấp `ResearchAnalyzer.analyze_simulation`:**
   - Bổ sung tham số `validation: dict[str, Any] | None = None`. Nếu được cung cấp hoặc có sẵn trong `sim_result.summary_dict`, nó sẽ được đính kèm toàn vẹn vào `analysis["validation"]`.
   - Tự động quét `trades` để tính toán chính xác `intrabar_tp_sl_ambiguous_count` và `intrabar_trailing_ambiguous_count` nếu `sim_result.ambiguity_stats` bị thiếu hoặc bằng 0.
2. **Cơ chế Fail-Closed trong `verify_report.py`:**
   - Xóa bỏ triệt để dòng khởi tạo lại `discrepancies` tại dòng 298.
   - Bắt buộc kiểm tra 4 Gate A, B, C, D: Nếu thiếu bảng kiểm toán hoặc bất kỳ Gate nào không đạt PASS -> lập tức FAIL.
   - Kiểm tra tính nhất quán: Nếu status là `REAL_DATA_PASS` nhưng `Real Market Data` ghi `False` -> lập tức FAIL.
   - Kiểm tra Ambiguity độc lập: Đọc trực tiếp các cột boolean `intrabar_ambiguous` và `intrabar_trailing_ambiguous` từ `trades.parquet` để đối chiếu với các số liệu ghi trong Section 4 của `report.md`.
   - Kiểm tra Companion Ledgers: Bắt buộc đủ `signals.parquet`, `skipped_signals.parquet`, `rejected_signals.parquet`.
   - Đối chiếu 3 cấp độc lập: Tự tính toán trực tiếp từ `trades.parquet` để kiểm tra độ cân bằng (Run net PnL == Tổng Symbol net PnL == Tổng Combo net PnL) với sai số $< 10^{-9}$.
3. **Bảng Đối Chiếu Trước và Sau Khắc Phục:**

| Mục kiểm toán / Chỉ số | Trạng thái tại HEAD ab65225c | Trạng thái tại HEAD V3-09 hiện tại | Kết luận |
|:---|:---:|:---:|:---:|
| **Trường `validation` trong `analysis.json`** | Bị thiếu hoàn toàn | Đầy đủ (`status: REAL_DATA_PASS`, Gate A-D) | **RESTORED** |
| **Bảng `Validation Gates Audit` (`report.md`)** | Biến mất (0 Gate) | Hiện diện đầy đủ 4 Gate A, B, C, D (PASS) | **RESTORED** |
| **Dòng `Real Market Data`** | `False` (sai logic) | `True` (nhất quán tuyệt đối) | **RESTORED** |
| **TP/SL Ambiguity Count** | 0 | 3 (tính trực tiếp từ `trades.parquet`) | **RESTORED** |
| **Trailing Ambiguity Count** | 0 | 486 (Mode A), 459 (Mode B & C) | **RESTORED** |
| **Bắt lỗi thiếu Gate (Fail-Closed)** | Bị xóa lỗi do bug dòng 298 | Bắt buộc Gate A-D, lập tức FAIL nếu thiếu | **FAIL-CLOSED** |
| **Bắt mâu thuẫn Real Market Data** | Không có check | Lập tức FAIL nếu REAL_DATA_PASS + False | **FAIL-CLOSED** |
| **Bắt sai lệch Ambiguity** | Không có check | Lập tức FAIL nếu khác counts từ parquet | **FAIL-CLOSED** |
| **Bắt thiếu Companion Ledger** | Không có check | Lập tức FAIL nếu thiếu companion ledger | **FAIL-CLOSED** |
| **Số lượng bài test V3-09** | 22 tests | 28 tests (+6 mutation & regression) | **EXPANDED** |
| **Toàn bộ test suite autotrade_v3** | 186 passed | 192 passed (0 failed) | **ALL PASSED** |

---

## L. Đề Xuất Nghiệm Thu V3-09

Toàn bộ các yêu cầu trong Lệnh Sửa Bổ Sung V3-09 và Yêu Cầu Khắc Phục Hồi Quy Báo Cáo đã được thực thi hoàn tất:
- 10 Nguyên tắc bất biến (N1-N10) được tuân thủ nghiêm ngặt: không sửa `autotrade_v2`, không sửa 20FINAL, không sửa Golden Baseline, không can thiệp ledger giao dịch, 100% offline, không có số liệu hay trạng thái gõ cứng.
- Cả 12 content hashes của 3 chế độ chạy đều trùng khớp tuyệt đối 100% với Golden Baseline V3-08.
- Công cụ thẩm định độc lập `verify_report.py` hoạt động ở chế độ fail-closed khắt khe, xác minh 179/179 metrics mỗi mode và đã được chứng minh hiệu quả qua 11 bài kiểm thử đột biến đối kháng.
- Toàn bộ 192 bài kiểm thử tự động, linter và compiler đều đạt PASS 100%.

Kính chuyển Claude review độc lập và đề xuất Owner nghiệm thu, hợp nhất Pull Request #100 vào nhánh `main`.
