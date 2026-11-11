# AUTOTRADE V3 — PR DELIVERY & EVIDENCE REPORT: V3-10
**Tên nhiệm vụ:** Independent Validation Gates (12 Cổng Kiểm Định Độc Lập)  
**Tài liệu tham chiếu:** "AutoTrade V3 Research Lab — Báo cáo kiểm toán độc lập & thiết kế lại" (10/11/2026), mục 10  
**Người thực hiện:** Antigravity | **Người review:** Claude | **Người nghiệm thu & merge:** Owner  
**Ngày báo cáo:** 2026-11-11  

---

## A. THÔNG TIN TRIỂN KHAI
- **PR:** V3-10: Independent validation gates
- **Branch:** `feat/v3-10-validation-gates`
- **Base SHA (v3-08 golden baseline):** `726fd52b0c86be8bceae6fd9c64dbef789d3140a`
- **Target Repository:** `https://github.com/trungtn87/autoTrade`
- **Phạm vi thay đổi:**
  - `autotrade_v3/validation/` (Gói mới hoàn toàn: types, hashing, context, replay, gates_a, gates_b, gates_c, gates_d, runner)
  - `autotrade_v3/lab/validation_gates.py` (Chuyển thành lớp vỏ adapter gọi gói mới, loại bỏ toàn bộ `REAL_DATA_PASS`)
  - `autotrade_v3/lab/experiment.py` (Thực thi kiểm định sau khi lưu artifact và gán validation status)
  - `autotrade_v3/cli.py` (Thêm lệnh `autotrade_v3 verify <run_dir>`)
  - `autotrade_v3/tests/` (`test_validation_gates.py`, `test_validation_import_boundary.py`, `test_validation_mutations.py`)
  - `evidence/v3-10/` (Bằng chứng golden run validation, mutation test logs, import boundary log, junit XML, c3_mismatch.csv)
- **Ràng buộc bất biến đã kiểm chứng:**
  - `autotrade_v2/`: `git diff --stat feat/v3-08-golden-baseline..HEAD -- autotrade_v2` rỗng (0 dòng thay đổi).
  - Không gọi BingX, Supabase, Gemini hay network API nào.
  - Dữ liệu M1 gốc (`data/m1_raw`, `catalog/research.duckdb`) chỉ đọc. SHA256 các partition Parquet trước và sau quá trình kiểm định trùng khớp 100%.
  - Chuỗi `REAL_DATA_PASS` không còn tồn tại trong mã nguồn `autotrade_v3/` hay scripts đang sử dụng.

---

## B. CÁC HẠNG MỤC ĐÃ HOÀN THÀNH
| Task ID | Công việc | Trạng thái | File/Artifact chứng minh |
|:---|:---|:---:|:---|
| **B1** | Tạo nhánh `feat/v3-10-validation-gates` từ `feat/v3-08-golden-baseline` | **VERIFIED** | Git branch history |
| **B2** | Khung kiến trúc `validation/`, kiểu kết quả `GateResult`, lệnh `verify`, kiểm tra ranh giới import độc lập | **VERIFIED** | `autotrade_v3/validation/types.py`, `autotrade_v3/validation/context.py`, `autotrade_v3/tests/test_validation_import_boundary.py` |
| **B3** | Cài đặt Cổng A1 (Hash dữ liệu), A2 (Toàn vẹn M1), D1 (Toàn vẹn Artifact) | **VERIFIED** | `autotrade_v3/validation/gates_a.py`, `autotrade_v3/validation/gates_d.py` |
| **B4** | Cài đặt Cổng B1 (Frozen signal parity), C5 (Bảo toàn tín hiệu) | **VERIFIED** | `autotrade_v3/validation/gates_b.py`, `autotrade_v3/validation/gates_c.py` |
| **B5** | Cài đặt Cổng C1 (Entry causality), C2 (ExitPolicy propagation), C4 (Kế toán toán học) | **VERIFIED** | `autotrade_v3/validation/gates_c.py` |
| **B6** | Cài đặt bộ phát lại C3 (Exit Replay độc lập không import simulator) | **VERIFIED** | `autotrade_v3/validation/replay.py`, `autotrade_v3/validation/gates_c.py` |
| **B7** | Cài đặt Cổng B2 (No-lookahead cutoff sampling), B3 (INCONCLUSIVE), D2 (Recomputed reported metrics) | **VERIFIED** | `autotrade_v3/validation/gates_b.py`, `autotrade_v3/validation/gates_d.py` |
| **B8** | Loại bỏ hoàn toàn `REAL_DATA_PASS`, tích hợp vào `experiment.py` và `cli.py verify` | **VERIFIED** | `autotrade_v3/lab/validation_gates.py`, `autotrade_v3/cli.py` |
| **B9** | Chạy `verify` trên cả 3 run golden V3-08 (`ALL_SIGNALS`, `BINGX_PORTFOLIO`, `ONE_TRADE_PER_COMBO`) | **VERIFIED** | `evidence/v3-10/golden_verify/*/validation.json` |
| **B10** | Chạy 12 nhóm mutation test độc lập trên bản sao tạm | **VERIFIED** | `autotrade_v3/tests/test_validation_mutations.py`, `evidence/v3-10/junit/all.xml` |

---

## C. KẾT QUẢ KIỂM THỬ TRÊN 3 RUN GOLDEN V3-08

### 1. Bảng 12 Cổng Kiểm Định Trên Golden Runs

| Cổng | Tên cổng | ALL_SIGNALS | BINGX_PORTFOLIO | ONE_TRADE_PER_COMBO | Số dòng kiểm tra / Tổng số | Vi phạm | Thời gian |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **A1** | Dataset hash | **PASS** | **PASS** | **PASS** | 74 / 74 partitions | 0 | ~0.9s |
| **A2** | M1 integrity | **PASS** | **PASS** | **PASS** | 3,156,480 / 3,156,480 nến | 0 | ~4.2s |
| **B1** | Frozen signal parity | **PASS** | **PASS** | **PASS** | 5,984 / 5,984 tín hiệu | 0 | ~0.8s |
| **B2** | No-lookahead validation | **PASS** | **PASS** | **PASS** | 3 / 3 cutoffs | 0 | ~128s |
| **B3** | Batch-Live equivalence | **INCONCLUSIVE** | **INCONCLUSIVE** | **INCONCLUSIVE** | 0 / 5,984 | 0 | 0.0s |
| **C1** | Entry causality | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~0.2s |
| **C2** | ExitPolicy propagation | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~2.5s |
| **C3** | Exit replay độc lập | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~0.4s |
| **C4** | Kế toán độc lập | **PASS** | **PASS** | **PASS** | 8,258 / 8,258 (A), 6,265 / 6,265 (C), 6,273 / 6,273 (B) | 0 | ~0.2s |
| **C5** | Bảo toàn tín hiệu | **PASS** | **PASS** | **PASS** | 5,984 / 5,984 tín hiệu | 0 | ~0.03s |
| **D1** | Toàn vẹn Artifact | **PASS** | **PASS** | **PASS** | 7/7 (A), 8/8 (C), 7/7 (B) | 0 | ~1.2s - 14s |
| **D2** | Số liệu báo cáo | **PASS** | **PASS** | **PASS** | 650/650 (A), 652/652 (C), 650/650 (B) | 0 | ~0.2s |
| **Tổng thể** | **Trạng thái Run** | **PARTIALLY_VERIFIED** | **PARTIALLY_VERIFIED** | **PARTIALLY_VERIFIED** | **Toàn bộ 11/11 cổng khả thi PASS** | **0** | **~140s** |

> **Ghi chú về Gate B3 và Trạng thái Tổng thể:**
> Đúng theo Thiết kế kỹ thuật mục 4.3 & 4.4 và AC2: Cổng B3 trả về `INCONCLUSIVE` với lý do *"on_bar_close chưa tồn tại"*. Do đó, không có run nào đạt trạng thái `VERIFIED` trong PR này; trạng thái cao nhất đạt được theo thiết kế là `PARTIALLY_VERIFIED`.

### 2. Bộ Phát Lại C3 và Đối Chiếu Khớp Ledger
- File `evidence/v3-10/c3_mismatch.csv` ghi nhận: **0 dòng lệch** (Khớp 100% trên toàn bộ 8,258 legs của Mode A, 6,265 legs của Mode C, và 6,273 legs của Mode B).
- Mã bộ phát lại C3 trong `autotrade_v3/validation/replay.py` được viết độc lập từ đặc tả tài liệu thiết kế (không sao chép mã từ simulator).

---

## D. KẾT QUẢ MUTATION / ADVERSARIAL TESTS (12 NHÓM)

| Mutation ID | Lỗi cấy vào bản sao tạm | Cổng mục tiêu | Kết quả mong đợi | Kết quả thực tế | Trạng thái |
|:---:|:---|:---:|:---:|:---:|:---:|
| **M-A1** | Sửa giá close của 1 nến trong partition Parquet | A1 | FAIL | A1 FAIL (`SHA256_MISMATCH`) | **VERIFIED** |
| **M-A2** | Xóa 1 nến M1 trong partition Parquet | A2 | FAIL | A2 FAIL (`COUNT_VS_MANIFEST`) | **VERIFIED** |
| **M-B1** | Đổi symbol của 1 tín hiệu trong `signals.parquet` | B1 | FAIL | B1 FAIL (`COMBO_CONTENT_MISMATCH`) | **VERIFIED** |
| **M-B2** | Nhận diện lookahead khi tín hiệu xuất hiện sớm | B2 | FAIL | B2 FAIL (`LOOKAHEAD_DETECTED`) | **VERIFIED** |
| **M-C1** | Lùi `entry_time_ms` của 1 leg lùi 1 phút | C1 | FAIL | C1 FAIL (`ENTRY_TIME`) | **VERIFIED** |
| **M-C2** | Đổi `tp_pct` của 1 combo trong ledger leg | C2 | FAIL | C2 FAIL (`POLICY_FIELD`) | **VERIFIED** |
| **M-C3** | Đổi `exit_reason` của 1 leg | C3 | FAIL | C3 FAIL (`EXIT_REASON`) | **VERIFIED** |
| **M-C4** | Sửa `net_pnl` của 1 leg trong ledger | C4 | FAIL | C4 FAIL (`LEG_NET_PNL`) | **VERIFIED** |
| **M-C5** | Xóa mọi leg của một tín hiệu ACCEPTED | C5 | FAIL | C5 FAIL (`STATE_COUNT`) | **VERIFIED** |
| **M-D1** | Làm sai lệch 1 byte & xóa `trades.parquet` | D1 | FAIL | D1 FAIL (`CONTENT_HASH_MISMATCH` / `MISSING_FILE`) | **VERIFIED** |
| **M-D2** | Sửa một con số net PnL trong `report.md` | D2 | FAIL | D2 FAIL (`REPORT_NUMBER_MISMATCH`) | **VERIFIED** |
| **M-DIRTY** | Run tạo trên cây làm việc dirty (`code_dirty=True`) | Overall | UNVERIFIED_DIRTY | `overall_status = UNVERIFIED_DIRTY` (không thể là VERIFIED) | **VERIFIED** |

- **Toàn vẹn dữ liệu gốc:** Kiểm tra SHA256 trước và sau mỗi phép đột biến trên partition dữ liệu thật khẳng định: Dữ liệu M1 gốc không bị thay đổi (`original_unmodified: True`).

---

## E. KIỂM TRA RANH GIỚI IMPORT (IMPORT BOUNDARY)
- Lệnh chạy: `pytest autotrade_v3/tests/test_validation_import_boundary.py` (Exit code: 0).
- Kết quả: Không có file nào trong `autotrade_v3/validation/` import `autotrade_v3.engine.execution_simulator`, `autotrade_v3.analysis`, `autotrade_v3.lab`, hay `autotrade_v2`.
- Chi tiết log lưu tại: `evidence/v3-10/import_boundary.log`.

---

## F. REPRODUCIBILITY & ARTIFACTS
- **Dataset Hash:** `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`
- **Total M1 Candles:** `3,156,480`
- **Total Parquet Partitions:** `74`
- **Lệnh tái lập xác minh độc lập:**
  ```powershell
  $env:PYTHONPATH="."
  python -m autotrade_v3.cli verify runs/golden/v3-08/ALL_SIGNALS
  python -m autotrade_v3.cli verify runs/golden/v3-08/BINGX_PORTFOLIO
  python -m autotrade_v3.cli verify runs/golden/v3-08/ONE_TRADE_PER_COMBO
  pytest autotrade_v3/tests/test_validation_*.py -v
  ```

---

## G. TỰ ĐÁNH GIÁ NGHIỆM THU
- **Tự đánh giá:** **PARTIALLY_VERIFIED** (Trạng thái chuẩn mực cao nhất của V3-10 do B3 được thiết kế trả về INCONCLUSIVE chờ V3-11).
- **Tiêu chí đạt:** AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8 đều thỏa mãn 100%.
- **Đủ điều kiện đề nghị Claude review và Owner nghiệm thu.**
