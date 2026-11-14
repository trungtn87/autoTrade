# AUTOTRADE V3 — PR DELIVERY & EVIDENCE REPORT: V3-10 REVIEW FIX

**Tên nhiệm vụ:** V3-10 — Independent Validation Gates (Review Fix & Main Alignment)  
**Tài liệu tham chiếu:** "AutoTrade V3 Research Lab — Báo cáo kiểm toán độc lập & thiết kế lại" (10/11/2026), mục 10  
**Người thực hiện:** Antigravity | **Người review độc lập:** Claude | **Người nghiệm thu & merge:** Owner  
**Repository:** `https://github.com/trungtn87/autoTrade`  
**Branch:** `feat/v3-10-validation-gates`  
**Ngày cập nhật:** 2026-11-11  

---

## 1. EXECUTIVE SUMMARY

Nhiệm vụ V3-10 Review Fix hoàn thành toàn diện việc khắc phục triệt để toàn bộ các phát hiện review P1/P2 và blockers từ Claude & Owner:

1. **Khắc phục Post-Save Status Persistence & Checksum Integrity:** Triển khai phương thức `update_post_validation()` trong `RunManager` và tích hợp vào `ExperimentRunner`. Đảm bảo `run_manifest.json`, `analysis.json`, `report.md`, `validation.json` và `ARTIFACTS.json` đồng bộ 100% về trạng thái và checksum trên đĩa. Đã bổ sung bộ integration test [`test_post_save_persistence.py`](../../autotrade_v3/tests/test_post_save_persistence.py) kiểm chứng hậu kiểm cả trường hợp PASS lẫn trường hợp FAIL.
2. **Khôi phục hoàn toàn Golden Evidence ALL_SIGNALS:** Đã chạy xác minh đầy đủ 12 gate KHÔNG skip B2 trên `ALL_SIGNALS`. Kết quả đạt `PARTIALLY_VERIFIED` (11/11 cổng khả thi PASS 100%, B3 `INCONCLUSIVE` đúng đặc tả). File [`evidence/v3-10/golden_validation/ALL_SIGNALS/validation.json`](golden_validation/ALL_SIGNALS/validation.json) đã được cập nhật chính xác với B2 PASS.
3. **Chạy Gate B2 đủ 20/20 Cutoffs trên cả 3 Golden Modes:** Đã kiểm thử 20 cutoffs ngẫu nhiên theo seed `20261110` trên cả 3 chế độ (`ALL_SIGNALS`, `BINGX_PORTFOLIO`, `ONE_TRADE_PER_COMBO`). Toàn bộ 20/20 điểm cutoff đều PASS 100%, bảo đảm không có tín hiệu lookahead, so sánh nội dung tín hiệu khớp chính xác, và nến được cắt strictly closed (`index < cutoff_dt`). Dữ liệu chi tiết 20 cutoffs được lưu tại [`evidence/v3-10/b2_20_cutoffs_audit.json`](b2_20_cutoffs_audit.json).
4. **Bảo vệ tuyệt đối Immutability Golden Baseline:** Đã xóa bỏ các file `validation.json` tạm thời khỏi `runs/golden/v3-08/`. Khẳng định lệnh `git diff --stat origin/main..HEAD -- runs/golden/v3-08` hoàn toàn rỗng (0 file, 0 dòng thay đổi so với `main`). Toàn bộ kết quả hậu kiểm được lưu biệt lập tại `evidence/v3-10/`.
5. **Full Regression Test Suite:** Chạy thành công toàn bộ 236/236 tests unit & integration test (bao gồm các test mới về CLI fail-closed subprocess, B2 replay cache isolation, post-save persistence và B2 properties), 100% PASS trong 491.53s (0 skipped, 0 failed). Kết quả JUnit XML đầy đủ được ghi tại [`evidence/v3-10/junit/all.xml`](junit/all.xml).
6. **Code Quality & Invariants:** `ruff check autotrade_v3` sạch 100% không cảnh báo; `compileall` biên dịch thành công; ranh giới `autotrade_v2` rỗng 100% (`git diff origin/main..HEAD -- autotrade_v2` rỗng).

---

## 2. FIX AUDIT LOG

| Issue ID | Phân loại | Mô tả vấn đề | Giải pháp & File thực hiện | Trạng thái |
|:---:|:---:|:---|:---|:---:|
| **FIX-01** | **P1 (Runner)** | `evaluate_overall_status()` bỏ qua gate bị skip trong dict kết quả; nguy cơ trả về `VERIFIED` khi thiếu gate. | Cập nhật `runner.py`: kiểm tra bắt buộc đủ 12 gate trong `GATE_IDS`. Gate thiếu hoặc skip bị đánh dấu lỗi và hạ trạng thái khỏi `VERIFIED`. Bổ sung test suite `test_validation_runner.py` (8 bài test). | **VERIFIED** |
| **FIX-02** | **P1 (Gate D2)** | Gate D2 chưa tương thích với cấu trúc báo cáo V3-09 (thiếu 4 canonical PF, combo key dạng cũ, không kiểm unrounded metrics và bảng reconciliation). | Viết lại `gates_d.py` độc lập 100%: tính lại 4 PF, đối soát unrounded metrics (tol=1e-6), kiểm tra header bảng Sections 5-8. Bổ sung 8 mutation tests M-D2-1 đến M-D2-8. | **VERIFIED** |
| **FIX-03** | **P1 (Post-Save & Checksum)** | Status hậu kiểm không đồng bộ xuống `run_manifest.json`, `analysis.json`, `report.md`, và `ARTIFACTS.json` trên đĩa. | Thêm `RunManager.update_post_validation()`: đồng bộ hóa toàn bộ file trên đĩa sau khi audit xong. Bổ sung integration test `test_post_save_persistence.py` kiểm chứng cả kịch bản PASS lẫn FAIL. | **VERIFIED** |
| **FIX-04** | **P1 (Golden Immutability)** | File `validation.json` bị tạo trong `runs/golden/v3-08/`. | Xóa bỏ hoàn toàn 3 file `validation.json` khỏi `runs/golden/v3-08/` bằng `git rm`. Định tuyến kết quả kiểm định sang `evidence/v3-10/golden_validation/{mode}/`. `git diff origin/main..HEAD -- runs/golden/v3-08` rỗng. | **VERIFIED** |
| **FIX-05** | **P2 (M-B2 Mutation)** | Thiếu mutation test chứng minh Gate B2 phát hiện lookahead thật sự. | Thêm test `test_m_b2_lookahead_detection` vào `test_validation_mutations.py`: cấy tín hiệu lookahead, Gate B2 trả về `FAIL` với vi phạm `LOOKAHEAD_DETECTED`. | **VERIFIED** |
| **FIX-06** | **P2 (Gate B2 20 Cutoffs)** | B2 chạy 20 cutoffs bị skip trên ALL_SIGNALS hoặc seed không ảnh hưởng đến mẫu; thời gian chạy lâu. | Sửa B2: dùng `rng.choice()` theo từng bucket thời gian; so sánh nội dung tín hiệu; tối ưu hóa slice in-memory và cache replayed signals; chạy đủ 20/20 cutoffs trên cả 3 golden modes. | **VERIFIED** |
| **FIX-07** | **P1 (Evidence Contamination)** | `test_golden_run_immutability_under_validation` chạy với `skip_gates=['B2']` và ghi đè vào `evidence/.../ALL_SIGNALS/validation.json`. | Cách ly test bằng `tempfile.TemporaryDirectory()`. Sửa `runner.py`: không tự động ghi kết quả golden nếu thiếu `output_dir`. Tái chạy full 12 gates 20 cutoffs trên `ALL_SIGNALS` đạt `PARTIALLY_VERIFIED`, xác nhận hash không đổi sau pytest. | **VERIFIED** |
| **FIX-08** | **P1 (CLI Fail-Closed)** | Lệnh `cli verify` luôn trả về exit code 0 kể cả khi overall status là `FAILED`. | Sửa `autotrade_v3/cli.py`: thoát với exit code 1 khi trạng thái là `FAILED` hoặc `UNVERIFIED_DIRTY`; thoát 0 với `PARTIALLY_VERIFIED` (B3 INCONCLUSIVE đúng thiết kế). Thêm subprocess test chứng minh CI phát hiện được FAIL. | **VERIFIED** |
| **FIX-09** | **P2 (B2 Cache Isolation)** | Cache B2 thiếu cách ly theo `dataset_hash`, `config_hash`, `symbols`. | Cập nhật `gates_b.py`: khóa cache gồm `(strat_id, config_hash, dataset_hash, symbols, start, end, cutoff)`. Thêm `clear_replay_cache()` và run-scoped cache trên context. Thêm test chứng minh không tái sử dụng cache khi cấu hình hoặc dữ liệu khác biệt trong cùng process. | **VERIFIED** |

---

## 3. 12 GATES GOLDEN BASELINE RESULTS TABLE

Kết quả kiểm định độc lập trên 3 golden run V3-08 với 20/20 cutoffs đầy đủ tại [`evidence/v3-10/golden_validation/{mode}/validation.json`](golden_validation/):

| Gate ID | Tên cổng | ALL_SIGNALS | BINGX_PORTFOLIO | ONE_TRADE_PER_COMBO | Phạm vi kiểm tra (Checked / Total) | Vi phạm | Thời gian |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **A1** | Dataset hash | **PASS** | **PASS** | **PASS** | 74 / 74 partitions | 0 | ~0.9s |
| **A2** | M1 integrity | **PASS** | **PASS** | **PASS** | 3,156,480 / 3,156,480 nến | 0 | ~4.3s |
| **B1** | Frozen signal parity | **PASS** | **PASS** | **PASS** | 5,984 / 5,984 tín hiệu | 0 | ~0.8s |
| **B2** | No-lookahead validation | **PASS** | **PASS** | **PASS** | **20 / 20 cutoffs** (toàn bộ 3 năm) | 0 | 626s (A), 0.05s (B, C cached) |
| **B3** | Batch-Live equivalence | **INCONCLUSIVE** | **INCONCLUSIVE** | **INCONCLUSIVE** | 0 / 5,984 (Blocked until V3-11: on_bar_close chưa tồn tại) | 0 | 0.0s |
| **C1** | Entry causality | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~0.2s |
| **C2** | ExitPolicy propagation | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~3.0s |
| **C3** | Independent Exit Replay | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~0.4s |
| **C4** | Mathematical Accounting | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~0.1s |
| **C5** | Signal Conservation | **PASS** | **PASS** | **PASS** | 5,984 / 5,984 tín hiệu | 0 | ~0.03s |
| **D1** | Artifact Integrity | **PASS** | **PASS** | **PASS** | 7/7 (A), 8/8 (C), 7/7 (B) artifacts | 0 | ~1.1s - 14s |
| **D2** | Reported Numbers & Schema | **PASS** | **PASS** | **PASS** | 650/650 (A), 652/652 (C), 650/650 (B) số liệu | 0 | ~0.3s |
| **TỔNG HỢP** | **Overall Status** | **PARTIALLY_VERIFIED** | **PARTIALLY_VERIFIED** | **PARTIALLY_VERIFIED** | **11/11 Cổng khả thi PASS 100%** | **0** | **~638s (A)** |

---

## 4. GATE B2 — 20 CUTOFF AUDIT & SEED SENSITIVITY

Chi tiết kiểm định 20 cutoff points trên dữ liệu 3 năm với `seed=20261110`:
- **Chính sách phân bổ mẫu:** Chia 5,984 mốc tín hiệu thành 20 buckets thời gian đều nhau. Với mỗi bucket, dùng `rng.choice()` sinh ngẫu nhiên 1 mốc cutoff. Đảm bảo trải đều từ tháng 01/2024 đến tháng 10/2026.
- **Chứng minh độ nhạy Seed:** Bài test `test_b2_seed_sensitivity_on_cutoff_sampling` chứng minh rằng với `seed=20261110`, kết quả sinh ra mảng cutoff hoàn toàn trùng khớp có tính tất định, trong khi với `seed=42`, các mốc thời gian sinh ra hoàn toàn khác biệt.
- **Bảo đảm Closed Bar Boundary:** Bài test `test_b2_closed_bar_boundary_enforcement` xác nhận dữ liệu M15 chỉ lấy các nến có `open_time < cutoff_dt`. Nến mở tại thời điểm `cutoff_dt` hoặc sau đó bị loại bỏ hoàn toàn.
- **So sánh nội dung tín hiệu:** Không chỉ kiểm tra `signal_id`, Gate B2 đối chiếu từng trường: `combo_id`, `side`, `signal_close_ms`, `entry_price`, `tp_price`, `sl_price`. Toàn bộ 20 cutoffs ghi nhận `content_matched: True`.

---

## 5. MUTATION MATRIX (ADVERSARIAL TESTS)

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

## 6. GOLDEN BASELINE INTEGRITY

- **Thư mục Golden Baseline:** `runs/golden/v3-08/`
- **Kết quả `git diff --stat origin/main..HEAD -- runs/golden/v3-08`:** RỖNG HOÀN TOÀN (0 file, 0 dòng thay đổi).
- **Kiểm tra Immutability tự động:** Test suite `test_golden_run_immutability_under_validation` trong `test_validation_gates.py` quét toàn bộ file trong `runs/golden/v3-08/`, tính SHA256 từng file trước khi chạy `verify`, chạy toàn bộ validation gates, và đối chiếu lại SHA256 sau khi chạy:
  - Khẳng định 100% hash trùng khớp tuyệt đối.
  - Không có file nào bị tạo mới, xóa đi, hoặc thay đổi nội dung trong `runs/golden/v3-08/`.

---

## 7. BOUNDARY & INDEPENDENCE AUDIT

Quy tắc ranh giới độc lập của Gói `autotrade_v3/validation/`:
- **Cấm nhập (Forbidden Imports):** Không được import `autotrade_v3.engine.execution_simulator`, `autotrade_v3.analysis`, `autotrade_v3.lab`, hay bất kỳ module nào thuộc `autotrade_v2`.
- **Cho phép nhập:** Thư viện chuẩn Python (`hashlib`, `json`, `re`, `pathlib`), thư viện tính toán (`pandas`, `numpy`, `duckdb`, `pyarrow`), và các kiểu dữ liệu / context độc lập.
- **Kết quả kiểm toán AST tự động (`test_validation_import_boundary.py`):**
  - Số file kiểm tra trong `autotrade_v3/validation/`: 10 files.
  - Số vi phạm phát hiện: **0**.
  - Khẳng định: Tính độc lập của bộ kiểm tra được bảo đảm tuyệt đối theo thiết kế.

---

## 8. V3-09 PARITY VERIFICATION

Sau khi tích hợp PR #100 (`0e9ee21ec249820b403021caa8e446ae6b01000c`), toàn bộ các tính năng và sửa lỗi của V3-09 đã được kiểm tra chéo:
- **Combo Keys:** Định dạng `symbol/combo_name` (`BTC-USDT/C1`, `ETH-USDT/C105`, ...) được bảo toàn đầy đủ 20 combo trong cả simulator, analyzer và validation gates.
- **Bốn Canonical Profit Factors:** Cả 4 chỉ số (`net_pf_trade`, `net_pf_leg`, `gross_pf_trade`, `gross_pf_leg`) đều được analyzer sinh ra và validation D2 tính toán lại độc lập từ `trades.parquet` với kết quả khớp 100%.
- **Unrounded Metrics:** Các trường unrounded (`gross_pnl_unrounded`, `total_fees_unrounded`, `net_pnl_unrounded`) được kiểm tra với dung sai máy tính chặt chẽ `1e-6`.
- **Three-Level Reconciliation:** Cả 3 cấp độ (Run Total vs Sum By Symbol vs Sum By Combo) đều được kiểm tra đối soát toán học tự động trong Section 8 của `report.md` và `analysis.json`.

---

## 9. KNOWN LIMITATIONS

1. **Gate B3 (Batch-Live Equivalence):**
   - Trạng thái hiện tại: `INCONCLUSIVE`.
   - Lý do: Phương thức `on_bar_close` chưa tồn tại trong kiến trúc chiến lược hiện tại. Cổng B3 được thiết kế chuẩn mực chờ triển khai tại nhiệm vụ V3-11 theo tài liệu thiết kế mục 4.3 & 10.
   - Do B3 trả về `INCONCLUSIVE`, trạng thái cao nhất của các run thật hợp lệ trong giai đoạn này là `PARTIALLY_VERIFIED`. Đây là hành vi đúng đặc tả kỹ thuật, không phải lỗi.

---

## 10. INVARIANTS COMPLIANCE (N1–N12)

| Nguyên tắc | Mô tả nguyên tắc | Bằng chứng tuân thủ | Kết luận |
|:---:|:---|:---|:---:|
| **N1** | Không sửa bất kỳ file nào trong `autotrade_v2/` | Lệnh `git diff --stat origin/main..HEAD -- autotrade_v2` trả về rỗng | **VERIFIED** |
| **N2** | Không sửa thông số engine 20FINAL để làm đẹp số liệu | Chiến lược `20FINAL_BASELINE` giữ nguyên 100% logic và tham số | **VERIFIED** |
| **N3** | Không gọi BingX, Supabase, Gemini hay network API | Kiểm tra offline boundary socket blocking PASS | **VERIFIED** |
| **N4** | Dữ liệu M1 gốc chỉ đọc | `catalog/` và `data/` không bị biến đổi | **VERIFIED** |
| **N5** | Không thay đổi file golden `runs/golden/v3-08/` | `git diff origin/main..HEAD -- runs/golden/v3-08` rỗng | **VERIFIED** |
| **N6** | Không hardcode số lượng tín hiệu, PnL, Win Rate, PF | Mọi số liệu trong D2 được tính lại từ `trades.parquet` và `signals.parquet` | **VERIFIED** |
| **N7** | Dữ liệu tổng hợp mang nhãn `SYNTHETIC` | Runner gán nhãn `SYNTHETIC` cho toàn bộ synthetic runs | **VERIFIED** |
| **N8** | Thiếu dữ liệu M1 thật thì dừng và báo BLOCKED | Đã xác nhận trong logic DataLayer và Gate A1/A2 | **VERIFIED** |
| **N9** | Mọi khẳng định kỹ thuật đều có nhãn xác minh | Báo cáo tuân thủ nhãn VERIFIED / PARTIALLY_VERIFIED | **VERIFIED** |
| **N10** | Một PR, một nhánh, không sửa main trực tiếp | Thực hiện trên nhánh `feat/v3-10-validation-gates` | **VERIFIED** |
| **N11** | Giữ nguyên lịch sử commit, không force push | Giữ nguyên lịch sử V3-10 và merge commit từ origin/main | **VERIFIED** |
| **N12** | Không tự merge hoặc deploy production | Dừng lại ở pull request, chờ Claude review và Owner nghiệm thu | **VERIFIED** |

---

## 11. SIGN-OFF RECOMMENDATION

- **Tự đánh giá:** **READY_FOR_REVIEW (PARTIALLY_VERIFIED)**
- **Độ bao phủ kiểm thử:** 236/236 bài test unit & integration test PASS 100% (0 skipped, 0 failed). JUnit XML tại `evidence/v3-10/junit/all.xml`. Linting và compile check sạch sẽ.
- **Đề nghị:** Toàn bộ điểm chặn đã được giải quyết triệt để có bằng chứng kiểm chứng. Trân trọng kính chuyển Claude thực hiện Review độc lập và kính chuyển Owner xem xét nghiệm thu, merge PR vào nhánh `main`.
