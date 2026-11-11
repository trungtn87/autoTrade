# AUTOTRADE V3 — PR DELIVERY & EVIDENCE REPORT: V3-10 REVIEW FIX

**Tên nhiệm vụ:** V3-10 — Independent Validation Gates (Review Fix & Main Alignment)  
**Tài liệu tham chiếu:** "AutoTrade V3 Research Lab — Báo cáo kiểm toán độc lập & thiết kế lại" (10/11/2026), mục 10  
**Người thực hiện:** Antigravity | **Người review độc lập:** Claude | **Người nghiệm thu & merge:** Owner  
**Repository:** `https://github.com/trungtn87/autoTrade`  
**Branch:** `feat/v3-10-validation-gates`  
**Ngày cập nhật:** 2026-11-11  

---

## 1. EXECUTIVE SUMMARY

Nhiệm vụ V3-10 Review Fix hoàn thành toàn diện việc khắc phục các phát hiện review P1/P2 từ Claude, đồng bộ hóa trơn tru với `origin/main` sau PR #100 (V3-09), và đưa hệ thống 12 Independent Validation Gates vào trạng thái sẵn sàng nghiệm thu:

1. **Đồng bộ Main (PR #100 / V3-09):** Tích hợp commit `0e9ee21ec249820b403021caa8e446ae6b01000c` vào nhánh `feat/v3-10-validation-gates` qua merge commit `2c5b76c`. Toàn bộ nâng cấp V3-09 (combo keys `symbol/combo_name`, 4 canonical Profit Factors, unrounded metrics, three-level reconciliation) được bảo toàn 100%.
2. **Sửa lỗi P1 Runner:** Khắc phục triệt để lỗ hổng thiếu gate bằng cách kiểm tra bắt buộc đủ 12 gate trong `GATE_IDS`. Bất kỳ gate nào bị thiếu, bị skip, hoặc không đạt tiêu chuẩn đều khiến run không thể đạt trạng thái `VERIFIED`.
3. **Nâng cấp P1 Gate D2:** Viết lại độc lập hoàn toàn bộ tính toán D2 mà không phụ thuộc vào `autotrade_v3.analysis` hay `autotrade_v3.engine`. D2 kiểm tra độc lập 650+ leaves của `analysis.json` và toàn bộ số liệu trong `report.md`, bao gồm 4 canonical Profit Factors, unrounded metrics và đối soát 3 cấp Section 8.
4. **Bảo vệ P1 Golden Runs & Post-Save:** Định tuyến kết quả kiểm định các run golden (`runs/golden/v3-08`) sang `evidence/v3-10/golden_validation/{mode}/validation.json`. Thư mục `runs/golden/v3-08/` được bảo vệ bất biến tuyệt đối (`git status` rỗng, 0 file thay đổi).
5. **Mutation Test M-B2:** Bổ sung mutation test cấy tín hiệu lookahead vào `signals.parquet` và chứng minh Gate B2 phát hiện chính xác lỗi `LOOKAHEAD_DETECTED`.
6. **Tối ưu P2 Gate B2:** Cắt giảm truy vấn DuckDB lặp lại bằng cách query M15 một lần theo symbol và slice in-memory theo từng cutoff point, tối ưu hóa thời gian chạy B2 với 20 cutoffs.
7. **Bảo toàn Invariants N1–N12:** `git diff --stat origin/main..HEAD -- autotrade_v2` hoàn toàn rỗng; 229/229 bài test pytest PASS 100%; ruff check và compileall PASS không có cảnh báo.

---

## 2. FIX AUDIT LOG

| Issue ID | Phân loại | Mô tả vấn đề | Giải pháp & File thực hiện | Trạng thái |
|:---:|:---:|:---|:---|:---:|
| **FIX-01** | **P1 (Runner)** | `evaluate_overall_status()` chỉ duyệt qua các gate có trong dict kết quả; nếu dùng `skip_gates` hoặc thiếu gate vẫn có thể trả về `VERIFIED`. | Cập nhật `autotrade_v3/validation/runner.py`: lấy `GATE_IDS` làm danh sách 12 gate bắt buộc. Gate thiếu hoặc skip lập tức bị đánh dấu lỗi và hạ trạng thái khỏi `VERIFIED`. Thêm bộ test `test_validation_runner.py` (8 bài test). | **VERIFIED** |
| **FIX-02** | **P1 (Gate D2)** | Gate D2 chưa tương thích với cấu trúc báo cáo V3-09 (thiếu 4 canonical PF, combo key dạng cũ, không kiểm unrounded metrics và bảng reconciliation). | Viết lại `autotrade_v3/validation/gates_d.py` độc lập 100%: tính lại 4 PF, đối soát unrounded metrics (tol=1e-6), kiểm tra header bảng Sections 5-8. Bổ sung 8 mutation tests M-D2-1 đến M-D2-8. | **VERIFIED** |
| **FIX-03** | **P1 (Post-Save)** | `audit_run` trong `lab/experiment.py` ghi đè `validation.json` vào run dir, có nguy cơ làm bẩn `runs/golden/v3-08`. | Thêm tham số `output_dir` và `write_result` vào `runner.py` và `cli.py`. Khi kiểm tra golden run, output tự động ghi vào `evidence/v3-10/golden_validation/{mode}/`. Bổ sung test tự động kiểm tra SHA256 immutability. | **VERIFIED** |
| **FIX-04** | **P2 (M-B2)** | Thiếu mutation test chứng minh Gate B2 phát hiện lookahead thật sự. | Thêm test `test_m_b2_lookahead_detection` vào `test_validation_mutations.py`: cấy tín hiệu đóng trước thời điểm tạo nến, Gate B2 trả về `FAIL` với vi phạm `LOOKAHEAD_DETECTED`. | **VERIFIED** |
| **FIX-05** | **P2 (Gate B2)** | B2 chạy 20 cutoffs tốn thời gian do query DuckDB 40 lần (20 cutoffs x 2 symbols). | Tối ưu hóa `gate_b2` trong `autotrade_v3/validation/gates_b.py`: query M15 full một lần duy nhất cho mỗi symbol, slice in-memory `df[df.index < cutoff_dt]`. Đã chạy kiểm chứng 20/20 cutoffs PASS trên ALL_SIGNALS. | **VERIFIED** |

---

## 3. 12 GATES GOLDEN BASELINE RESULTS TABLE

Kết quả kiểm định độc lập trên 3 golden run V3-08 được lưu trữ tại `evidence/v3-10/golden_validation/{mode}/validation.json`:

| Gate ID | Tên cổng | ALL_SIGNALS | BINGX_PORTFOLIO | ONE_TRADE_PER_COMBO | Phạm vi kiểm tra (Checked / Total) | Vi phạm | Thời gian |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **A1** | Dataset hash | **PASS** | **PASS** | **PASS** | 74 / 74 partitions | 0 | ~0.9s |
| **A2** | M1 integrity | **PASS** | **PASS** | **PASS** | 3,156,480 / 3,156,480 nến | 0 | ~4.3s |
| **B1** | Frozen signal parity | **PASS** | **PASS** | **PASS** | 5,984 / 5,984 tín hiệu | 0 | ~0.8s |
| **B2** | No-lookahead validation | **PASS** | **PASS** | **PASS** | 3 / 3 cutoffs (smoke/verify) & 20/20 cutoffs audit | 0 | ~110s (3 cutoffs) |
| **B3** | Batch-Live equivalence | **INCONCLUSIVE** | **INCONCLUSIVE** | **INCONCLUSIVE** | 0 / 5,984 (Blocked until V3-11: on_bar_close chưa tồn tại) | 0 | 0.0s |
| **C1** | Entry causality | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~0.2s |
| **C2** | ExitPolicy propagation | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~3.0s |
| **C3** | Independent Exit Replay | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~0.4s |
| **C4** | Mathematical Accounting | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~0.1s |
| **C5** | Signal Conservation | **PASS** | **PASS** | **PASS** | 5,984 / 5,984 tín hiệu | 0 | ~0.03s |
| **D1** | Artifact Integrity | **PASS** | **PASS** | **PASS** | 7/7 (A), 8/8 (C), 7/7 (B) artifacts | 0 | ~1.1s - 14s |
| **D2** | Reported Numbers & Schema | **PASS** | **PASS** | **PASS** | 650/650 (A), 652/652 (C), 650/650 (B) số liệu | 0 | ~0.3s |
| **TỔNG HỢP** | **Overall Status** | **PARTIALLY_VERIFIED** | **PARTIALLY_VERIFIED** | **PARTIALLY_VERIFIED** | **11/11 Cổng khả thi PASS 100%** | **0** | **~125s** |

---

## 4. MUTATION MATRIX (ADVERSARIAL TESTS)

Toàn bộ các phép đột biến được kiểm thử trên bản sao tạm trong thư mục tạm, bảo đảm dữ liệu gốc không bị biến đổi:

| Mutation ID | Lỗi cấy vào bản sao tạm | Cổng mục tiêu | Kết quả mong đợi | Kết quả kiểm thử thực tế | Trạng thái |
|:---:|:---|:---:|:---:|:---|:---:|
| **M-A1** | Sửa giá close của 1 nến trong partition Parquet | A1 | FAIL | A1 trả về FAIL (`SHA256_MISMATCH`) | **VERIFIED** |
| **M-A2** | Xóa 1 nến M1 trong partition Parquet | A2 | FAIL | A2 trả về FAIL (`COUNT_VS_MANIFEST`) | **VERIFIED** |
| **M-B1** | Đổi symbol của 1 tín hiệu trong `signals.parquet` | B1 | FAIL | B1 trả về FAIL (`COMBO_CONTENT_MISMATCH`) | **VERIFIED** |
| **M-B2** | Cấy tín hiệu lookahead có `signal_close_ms` sớm hơn nến | B2 | FAIL | B2 trả về FAIL (`LOOKAHEAD_DETECTED`) | **VERIFIED** |
| **M-C1** | Lùi `entry_time_ms` của 1 leg lùi 1 phút trước tín hiệu | C1 | FAIL | C1 trả về FAIL (`ENTRY_TIME`) | **VERIFIED** |
| **M-C2** | Đổi `tp_pct` của 1 combo trong ledger leg | C2 | FAIL | C2 trả về FAIL (`POLICY_FIELD`) | **VERIFIED** |
| **M-C3** | Đổi `exit_reason` của 1 leg | C3 | FAIL | C3 trả về FAIL (`EXIT_REASON`) | **VERIFIED** |
| **M-C4** | Sửa `net_pnl` của 1 leg trong ledger | C4 | FAIL | C4 trả về FAIL (`LEG_NET_PNL`) | **VERIFIED** |
| **M-C5** | Xóa mọi leg của một tín hiệu mang trạng thái ACCEPTED | C5 | FAIL | C5 trả về FAIL (`STATE_COUNT`) | **VERIFIED** |
| **M-D1** | Làm sai lệch hash và xóa `trades.parquet` | D1 | FAIL | D1 trả về FAIL (`CONTENT_HASH_MISMATCH`) | **VERIFIED** |
| **M-D2** | Sửa một con số net PnL trong `report.md` | D2 | FAIL | D2 trả về FAIL (`REPORT_NUMBER_MISMATCH`) | **VERIFIED** |
| **M-D2-1** | Sửa `net_pf_trade` trong Section 5 của `report.md` | D2 | FAIL | D2 trả về FAIL (`REPORT_TOKEN_MISMATCH`) | **VERIFIED** |
| **M-D2-2** | Sửa `gross_pf_leg` trong Section 5 của `report.md` | D2 | FAIL | D2 trả về FAIL (`REPORT_TOKEN_MISMATCH`) | **VERIFIED** |
| **M-D2-3** | Đổi khóa combo từ `BTC-USDT/C1` sang `BTC-USDT_C1` | D2 | FAIL | D2 trả về FAIL (`UNEXPECTED_KEY`) | **VERIFIED** |
| **M-D2-4** | Làm sai lệch `sum_by_combo_unrounded` trong PnL reconciliation | D2 | FAIL | D2 trả về FAIL (`VALUE_MISMATCH`) | **VERIFIED** |
| **M-D2-5** | Làm sai lệch `sum_by_symbol_unrounded` trong PnL reconciliation | D2 | FAIL | D2 trả về FAIL (`VALUE_MISMATCH`) | **VERIFIED** |
| **M-D2-6** | Làm sai lệch trường `gross_pnl_unrounded` trong `pnl` | D2 | FAIL | D2 trả về FAIL (`VALUE_MISMATCH`) | **VERIFIED** |
| **M-D2-7** | Sửa tiêu đề Section 8 Reconciliation Audit trong `report.md` | D2 | FAIL | D2 trả về FAIL (`RECONCILIATION_SECTION_MISSING`) | **VERIFIED** |
| **M-D2-8** | Sửa header bảng 4 Profit Factors trong `report.md` | D2 | FAIL | D2 trả về FAIL (`TABLE_HEADER_MISMATCH`) | **VERIFIED** |
| **M-DIRTY** | Run tạo trên working tree có diff chưa commit (`code_dirty=True`) | Overall | UNVERIFIED_DIRTY | `overall_status = UNVERIFIED_DIRTY` | **VERIFIED** |

---

## 5. GOLDEN BASELINE INTEGRITY

- **Thư mục Golden Baseline:** `runs/golden/v3-08/`
- **Kết quả `git status --porcelain runs/golden/v3-08`:** Rỗng (0 dòng, 0 file modified, 0 file untracked).
- **Kiểm tra Immutability tự động:** Test suite `test_golden_run_immutability_under_validation` trong `autotrade_v3/tests/test_validation_gates.py` quét toàn bộ file trong `runs/golden/v3-08/`, tính SHA256 từng file trước khi chạy `verify`, chạy toàn bộ validation gates, và đối chiếu lại SHA256 sau khi chạy:
  - Khẳng định 100% hash trùng khớp tuyệt đối.
  - Không có file nào bị tạo mới, xóa đi, hoặc thay đổi nội dung.

---

## 6. BOUNDARY & INDEPENDENCE AUDIT

Quy tắc ranh giới độc lập của Gói `autotrade_v3/validation/`:
- **Cấm nhập (Forbidden Imports):** Không được import `autotrade_v3.engine.execution_simulator`, `autotrade_v3.analysis`, `autotrade_v3.lab`, hay bất kỳ module nào thuộc `autotrade_v2`.
- **Cho phép nhập:** Thư viện chuẩn Python (`hashlib`, `json`, `re`, `pathlib`), thư viện tính toán (`pandas`, `numpy`, `duckdb`, `pyarrow`), và các kiểu dữ liệu / context độc lập.
- **Kết quả kiểm toán AST tự động (`test_validation_import_boundary.py`):**
  - Số file kiểm tra trong `autotrade_v3/validation/`: 10 files.
  - Số vi phạm phát hiện: **0**.
  - Khẳng định: Tính độc lập của bộ kiểm tra được bảo đảm tuyệt đối theo thiết kế.

---

## 7. V3-09 PARITY VERIFICATION

Sau khi tích hợp PR #100 (`0e9ee21ec249820b403021caa8e446ae6b01000c`), toàn bộ các tính năng và sửa lỗi của V3-09 đã được kiểm tra chéo:
- **Combo Keys:** Định dạng `symbol/combo_name` (`BTC-USDT/C1`, `ETH-USDT/C105`, ...) được bảo toàn đầy đủ 20 combo trong cả simulator, analyzer và validation gates.
- **Bốn Canonical Profit Factors:** Cả 4 chỉ số (`net_pf_trade`, `net_pf_leg`, `gross_pf_trade`, `gross_pf_leg`) đều được analyzer sinh ra và validation D2 tính toán lại độc lập từ `trades.parquet` với kết quả khớp 100%.
- **Unrounded Metrics:** Các trường unrounded (`gross_pnl_unrounded`, `total_fees_unrounded`, `net_pnl_unrounded`) được kiểm tra với dung sai máy tính chặt chẽ `1e-6`.
- **Three-Level Reconciliation:** Cả 3 cấp độ (Run Total vs Sum By Symbol vs Sum By Combo) đều được kiểm tra đối soát toán học tự động trong Section 8 của `report.md` và `analysis.json`.

---

## 8. KNOWN LIMITATIONS

1. **Gate B3 (Batch-Live Equivalence):**
   - Trạng thái hiện tại: `INCONCLUSIVE`.
   - Lý do: Phương thức `on_bar_close` chưa tồn tại trong kiến trúc chiến lược hiện tại. Cổng B3 được thiết kế chuẩn mực chờ triển khai tại nhiệm vụ V3-11 theo tài liệu thiết kế mục 4.3 & 10.
   - Do B3 trả về `INCONCLUSIVE`, trạng thái cao nhất của các run thật hợp lệ trong giai đoạn này là `PARTIALLY_VERIFIED`. Đây là hành vi đúng đặc tả kỹ thuật, không phải lỗi.

---

## 9. INVARIANTS COMPLIANCE (N1–N12)

| Nguyên tắc | Mô tả nguyên tắc | Bằng chứng tuân thủ | Kết luận |
|:---:|:---|:---|:---:|
| **N1** | Không sửa bất kỳ file nào trong `autotrade_v2/` | Lệnh `git diff --stat origin/main..HEAD -- autotrade_v2` trả về rỗng | **VERIFIED** |
| **N2** | Không sửa thông số engine 20FINAL để làm đẹp số liệu | Chiến lược `20FINAL_BASELINE` giữ nguyên 100% logic và tham số | **VERIFIED** |
| **N3** | Không gọi BingX, Supabase, Gemini hay network API | Kiểm tra offline boundary socket blocking PASS | **VERIFIED** |
| **N4** | Dữ liệu M1 gốc chỉ đọc | `catalog/` và `data/` không bị biến đổi | **VERIFIED** |
| **N5** | Không thay đổi file golden `runs/golden/v3-08/` | `git status --porcelain runs/golden/v3-08` rỗng | **VERIFIED** |
| **N6** | Không hardcode số lượng tín hiệu, PnL, Win Rate, PF | Mọi số liệu trong D2 được tính lại từ `trades.parquet` và `signals.parquet` | **VERIFIED** |
| **N7** | Dữ liệu tổng hợp mang nhãn `SYNTHETIC` | Runner gán nhãn `SYNTHETIC` cho toàn bộ synthetic runs | **VERIFIED** |
| **N8** | Thiếu dữ liệu M1 thật thì dừng và báo BLOCKED | Đã xác nhận trong logic DataLayer và Gate A1/A2 | **VERIFIED** |
| **N9** | Mọi khẳng định kỹ thuật đều có nhãn xác minh | Báo cáo tuân thủ nhãn VERIFIED / PARTIALLY_VERIFIED | **VERIFIED** |
| **N10** | Một PR, một nhánh, không sửa main trực tiếp | Thực hiện trên nhánh `feat/v3-10-validation-gates` | **VERIFIED** |
| **N11** | Giữ nguyên lịch sử commit, không force push | Merge commit sạch sẽ, giữ nguyên toàn bộ lịch sử V3-10 | **VERIFIED** |
| **N12** | Không tự merge hoặc deploy production | Dừng lại ở pull request, chờ Claude review và Owner nghiệm thu | **VERIFIED** |

---

## 10. SIGN-OFF RECOMMENDATION

- **Tự đánh giá:** **HOÀN THÀNH TOÀN DIỆN (PARTIALLY_VERIFIED)**
- **Độ bao phủ kiểm thử:** 229/229 bài test unit & mutation test PASS 100%. Linting và compile check sạch sẽ.
- **Đề nghị:** Trân trọng kính chuyển Claude thực hiện Review độc lập và kính chuyển Owner xem xét nghiệm thu, merge PR #101 vào nhánh `main`.
