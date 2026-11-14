# AUTOTRADE V3 — PR DELIVERY & EVIDENCE REPORT: V3-11

**Tên nhiệm vụ:** V3-11 — Independent Strategy Core & Plugin API  
**Tài liệu thiết kế:** "AutoTrade V3 Research Lab — Báo cáo kiểm toán độc lập & thiết kế lại" (10/11/2026), mục 7 và 8  
**Người thực hiện:** Antigravity | **Người review độc lập:** Claude | **Người nghiệm thu & merge:** Owner  
**Repository:** `https://github.com/trungtn87/autoTrade`  

---

## A. THÔNG TIN TRIỂN KHAI

- **PR Title:** `V3-11: Independent strategy core and plugin API`
- **Branch:** `feat/v3-11-strategy-core`
- **Base Branch / Base SHA:** `feat/v3-10-validation-gates` (`011d081ac84668211cd48ed497781904ce8eefd2`)
- **Code Head SHA (pre-evidence):** `6ef6c1eba1abd79b8e0eb6be9e3595d035c7697e` (xem `evidence/v3-11/git_state.txt` để lấy Head SHA cuối cùng sau khi commit `evidence/v3-11/`)
- **Lịch sử Commit tách theo mục 12:**
  1. `6987ac5` — `feat(v3-11): copy verbatim 20FINAL indicator and signal functions with PROVENANCE`
  2. `9b62abc` — `feat(v3-11): add 20FINAL v1 parameters extraction and strategy_core hashing`
  3. `7d81999` — `feat(v3-11): implement Strategy Core API, batch generate(), and versioned registry`
  4. `cdaa9e7` — `feat(v3-11): implement incremental O(1) on_bar_close and enable Validation Gate B3`
  5. `0690210` — `feat(v3-11): migrate lab and backtest runtime to strategy_core and remove V2 imports`
  6. `6ef6c1e` — `feat(v3-11): add 20final_v1.lock, strategy clone CLI, and V3-11 test suite`
  7. Commit cuối cùng — `docs(v3-11): add V3-11 evidence package and delivery report`
- **Phạm vi thay đổi:**
  - Gói mới: `autotrade_v3/strategy_core/` (`api.py`, `hashing.py`, `registry.py`, `indicators/*`, `strategies/final20/logic.py`, `params/20final_v1.json`, `params/20final_v1.lock`, `PROVENANCE.json`).
  - Tool trích tham số AST: `autotrade_v3/tools/extract_20final_params.py` (chỉ đọc văn bản AST `autotrade_v2/app/strategy/final14_config.py`, không import V2, không nằm trên đường chạy backtest).
  - Chuyển đổi Lab & Engine sang `strategy_core`: `autotrade_v3/strategy/`, `autotrade_v3/engine/signal_engine.py`, `autotrade_v3/data_backtest/replay_20final.py`, `autotrade_v3/data_backtest/simulate_execution.py`, `autotrade_v3/analysis/metrics.py`, `autotrade_v3/lab/experiment.py`, `autotrade_v3/lab/run_manager.py`, `autotrade_v3/cli.py`, `autotrade_v3/legacy/reconcile_runs.py`, `autotrade_v3/tools/run_head_verification.py`.
  - Validation Gates: `autotrade_v3/validation/gates_b.py` (bật cổng B3, cập nhật B1 tra theo `(strategy_id, version)` và kiểm `signal_spec_hash` với `20final_v1.lock`).
  - Tests & Evidence: `autotrade_v3/tests/test_strategy_core_v311.py`, `autotrade_v3/tests/test_validation_gates.py`, `evidence/v3-11/`.
- **Ngoài phạm vi (bất biến):** `autotrade_v2/`, `catalog/`, `data/`, `runs/golden/v3-08/`, `autotrade_v3/engine/execution_simulator.py`, `autotrade_v3/analysis/research_analyzer.py` không bị sửa đổi.

---

## B. CÁC HẠNG MỤC ĐÃ HOÀN THÀNH

| Task ID | Công việc | Trạng thái | File / Commit chứng minh |
|:---:|:---|:---:|:---|
| **B1** | Tạo nhánh `feat/v3-11-strategy-core` từ `feat/v3-10-validation-gates` (`011d081`) | **VERIFIED** | `git_state.txt` |
| **B2–B3** | Lập danh sách phụ thuộc V2, sao chép nguyên văn vào `strategy_core/`, lập `PROVENANCE.json` + đọc từng dòng `indicators.py`, `layer3_long.py` | **VERIFIED** | Commit `6987ac5`, `PROVENANCE.json`, `evidence/v3-11/provenance_diff/` |
| **B4** | Viết script AST trích tham số từ V2 `FINAL14_CASES` & `NEW6_CASES`, sinh `20final_v1.json`, đối chiếu 20/20 combo | **VERIFIED** | Commit `9b62abc`, `autotrade_v3/tools/extract_20final_params.py`, `evidence/v3-11/params_extract.log` |
| **B5** | Cài `api.py`, `hashing.py` (3 hash chuẩn hóa LF), `registry.py` (`(strategy_id, version)`), `generate()` | **VERIFIED** | Commit `7d81999`, `strategy_core/api.py`, `hashing.py`, `registry.py` |
| **B6** | Parity lô 3 năm: so tập `signal_id` và mọi trường (`side`, `native_open_ms`, `signal_close_ms`, `smc_dir`, `ob_blocked`, `approved`, `warmup_ready`) với `runs/golden/v3-08/ALL_SIGNALS/signals.parquet` và `runs/3year_replay/signal_events.csv` | **VERIFIED** | `evidence/v3-11/signal_parity.csv` (0 dòng lệch), `evidence/v3-11/signal_parity.log` |
| **B7** | Cài `on_bar_close()` tăng dần $O(1)$ theo từng nến M15; chạy tương đương với `generate()` trên toàn bộ 3 năm cả 2 symbol; bật cổng B3 | **VERIFIED** | Commit `cdaa9e7`, `evidence/v3-11/batch_vs_live.log`, `autotrade_v3/validation/gates_b.py` |
| **B8** | Chuyển lab sang `strategy_core`; xóa `sys.path` và mọi import `autotrade_v2`/`app.*` khỏi đường chạy backtest; ghi 3 hash vào `RunManifest` | **VERIFIED** | Commit `0690210`, `evidence/v3-11/import_boundary.log` |
| **B9** | Khóa baseline `20final_v1.lock`; cài `cli strategy clone --from 20FINAL@1 --to <ID>@1`; cập nhật B1 tra theo version | **VERIFIED** | Commit `6ef6c1e`, `params/20final_v1.lock`, `autotrade_v3/cli.py` |
| **B10** | Chạy lại 3 mode `--golden` qua `strategy_core`; so content hash ledger với golden V3-08; chạy `verify` 12 cổng (kể cả B3) | **VERIFIED** | `evidence/v3-11/ledger_hash_compare.log`, `evidence/v3-11/verify/<MODE>/validation.json` |
| **B11** | Thực hiện phép đo cửa sổ con mục 4.8 (`2025-05-01` đến `2025-11-01` vs tập con 3 năm) trả lời giả thuyết P2-5 | **VERIFIED** | `evidence/v3-11/subwindow_experiment.md` |

---

## C. KẾT QUẢ KIỂM THỬ

### 1. Kiểm tra mã nguồn & biên giới
| Hạng mục | Lệnh chạy | Kết quả | Exit Code | Artifact |
|:---|:---|:---|:---:|:---|
| **Ruff Lint** | `.\.venv\Scripts\python.exe -m ruff check autotrade_v3` | `All checks passed!` | 0 | — |
| **Bytecode Compile** | `.\.venv\Scripts\python.exe -m compileall -q autotrade_v3` | Thành công, 0 lỗi | 0 | — |
| **Mypy Type Check** | `.\.venv\Scripts\python.exe -m mypy autotrade_v3/strategy_core` | `UNVERIFIED` (`mypy` chưa cài trong `.venv`) | 1 | Mục H |
| **AST Import Boundary** | `pytest autotrade_v3/tests/test_strategy_core_v311.py -k test_01` | `PASSED` (0 vi phạm AST) | 0 | `evidence/v3-11/import_boundary.log` |

### 2. Full Pytest Suite (Unit + Integration + Adversarial Mutations)
- **Lệnh chạy:** `$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest autotrade_v3/tests --junitxml=evidence/v3-11/junit/all.xml -v`
- **Tổng hợp:** **242 passed, 0 failed, 0 skipped** trong **700.05s** (Exit Code: `0`).
- **Artifact JUnit XML:** [`evidence/v3-11/junit/all.xml`](junit/all.xml).
- **CLI Clone & 3-Mode Smoke Backtest:** Chạy `cli strategy clone --from 20FINAL@1 --to EXP_SMOKE@1` và `cli backtest --strategy 20FINAL@1 --mode ALL --smoke-test` (synthetic fixture) — Exit Code `0`, log tại [`evidence/v3-11/cli_clone_smoke.log`](cli_clone_smoke.log).

### 3. Kiểm toán từng dòng `indicators.py` và `layer3_long.py` (Mục 2)
- **`indicators.py` (137 dòng):** Toàn bộ chỉ báo (`sma`, `ema`, `rma`, `true_range`, `atr`, `rsi`, `mfi`, `cci`, `stochastic`, `crossover`, `crossunder`, `dmi`, `parabolic_sar`, `obv_variant`, `session_vwap`, `edge_event`, `pine_custom_supertrend_dir`, `pine_combo15_supertrend_dir`) chỉ dùng dữ liệu quá khứ và hiện tại (`rolling`, `ewm`, `diff`, `shift(1)`, vòng lặp nhân quả `0..i`). Không có `shift(-k)` hay trung bình toàn cục.
- **`layer3_long.py` (232 dòng):** `_nadaraya` tính tổng trọng số với `close.shift(i)` cho `i = 0..length` (nhân quả); `precompute` và `entry_variants` dùng `align_confirmed(..., "4h" / "6h")` (chỉ gắn chỉ báo khung lớn sau khi nến khung lớn đóng) và `shift(1)`. Không phát hiện look-ahead.

---

## D. KẾT QUẢ ĐỐI CHIẾU DỮ LIỆU

### 1. Three Canonical Hashes của `20FINAL@1`
| Hash Name | Giá trị SHA256 (Canonical LF-normalized JSON) | Nguồn |
|:---|:---|:---|
| `signal_spec_hash` | `fe592c07fdeaee9a20e21c7a1443ae1ac94ab2ec9a8c30d92dcb1dcea997b26e` | `20final_v1.lock` |
| `exit_spec_hash` | `ce10e2b1c328201266965d2b1a4d991136941320ff9c2cdb338a471d7927254d` | `20final_v1.lock` |
| `spec_hash` | `e2dcfe7e10c2ff7c63b5a94b946721e1d671219d60b22a452c62f507af4cc058` | `20final_v1.lock` |

### 2. Batch Signal Parity 3 Năm (Step B6 — `signal_parity.csv` & `signal_parity.log`)
| Chỉ tiêu đối chiếu | Nguồn Tham Chiếu | Kết quả `strategy_core` | Sai lệch | Trạng thái |
|:---|:---:|---:|---:|:---:|
| Tổng tín hiệu thô (gồm chưa `approved` & chưa `warmup_ready`) | `runs/3year_replay/signal_events.csv` (`6,752`) | `6,752` | `0` | **VERIFIED** |
| Tổng tín hiệu hợp lệ (`approved=True`, `warmup_ready=True`) | `runs/golden/v3-08/ALL_SIGNALS/signals.parquet` (`5,984`) | `5,984` | `0` | **VERIFIED** |
| Đối chiếu từng `signal_id` và mọi trường (`side`, `native_open_ms`, `signal_close_ms`, `smc_dir`, `ob_blocked`, `approved`, `warmup_ready`) | 20/20 combos (`BTC-USDT` 11, `ETH-USDT` 9) | Khớp 100% | `0` dòng trong `signal_parity.csv` | **VERIFIED** |

### 3. Batch (`generate`) vs Live Incremental (`on_bar_close`) 3 Năm (Step B7 — `batch_vs_live.log`)
- **Phạm vi:** Toàn bộ 3 năm (`2023-11-01` đến `2026-11-01` kèm `3,400` nến warm-up = `105,216` nến M15 / symbol, tổng `210,432` nến M15).
- **Thời gian chạy `on_bar_close`:** BTC-USDT `13.842s`, ETH-USDT `14.877s` (tổng `28.719s` — hoàn toàn dưới ngưỡng 6 giờ).

| Symbol | Số nến M15 | Raw Events (Batch / Live) | Approved Eval Events (Batch / Live) | Sai lệch (`raw` / `approved`) | Thời gian Batch / Live | Trạng thái |
|:---|---:|---:|---:|---:|---:|:---:|
| `BTC-USDT` | `105,216` | `3,085` / `3,085` | `2,575` / `2,575` | `0` / `0` | `20.840s` / `13.842s` | **VERIFIED** |
| `ETH-USDT` | `105,216` | `3,667` / `3,667` | `3,409` / `3,409` | `0` / `0` | `19.467s` / `14.877s` | **VERIFIED** |

### 4. Thí nghiệm Cửa sổ con & Warm-up (Mục 4.8 — `subwindow_experiment.md`)
- **Cửa sổ đo:** `2025-05-01` đến `2025-11-01` (nạp kèm `3,400` nến M15 warm-up) so với tập con cùng kỳ của Run 3 năm (`2023-11-01` đến `2026-11-01`).
- **Kết quả:**
  - Tín hiệu `approved=True, warmup_ready=True` trong tập con 3 năm: `1,103`
  - Tín hiệu `approved=True, warmup_ready=True` trong Run cửa sổ con: `1,103`
  - Trùng khớp ở cả hai bên (`In Both`): `1,103`
  - Chỉ có ở Run 3 năm (`Only in 3-Year`): `0`
  - Chỉ có ở Run cửa sổ con (`Only in Subwindow`): `0`
  - Tín hiệu thô Layer 3 (`warmup_ready=True`): `0` tín hiệu chỉ có ở 1 bên (`raw_only_3yr = 0`, `raw_only_sub = 0`).
- **Kết luận cho giả thuyết P2-5:** Trên cửa sổ `2025-05-01` đến `2025-11-01`, `3,400` nến M15 warm-up đã đủ để mọi trạng thái chỉ báo (kể cả SMC/OB) hội tụ hoàn toàn giống hệt run 3 năm (sai lệch = `0`). Không thay đổi hành vi warm-up trong PR này.

---

## E. MUTATION TESTS (TRÊN BẢN SAO TẠM)

Toàn bộ mutation test M1–M5 nằm trong `autotrade_v3/tests/test_strategy_core_v311.py::test_06_adversarial_mutations_m1_m2_m3_m4_m5` và `test_03_baseline_lock_enforcement_m2`:

| Mutation ID | Lỗi được cấy | Kết quả mong đợi | Kết quả thực tế | Evidence |
|:---:|:---|:---|:---|:---|
| **M1** | Đổi `tp_pct` một combo giữ nguyên `20FINAL@1` -> bị lock từ chối; đổi trên bản clone -> `spec_hash` và `exit_spec_hash` đổi, `signal_spec_hash` & tín hiệu giữ nguyên | Reject by lock / hash đổi trên clone | `BaselineLockError` khi giữ `20FINAL@1`; hash & parity đúng trên clone (`PASSED`) | `test_strategy_core_v311.py` |
| **M2** | Đổi ngưỡng entry của `BTC-USDT/C7` trên bản clone | `signal_spec_hash` đổi; Gate B1 trả `INCONCLUSIVE` (nếu là clone) hoặc `FAIL` (nếu so với reference baseline), không `PASS` | `signal_spec_hash` đổi; B1 trả `INCONCLUSIVE` / `FAIL` (`PASSED`) | `test_strategy_core_v311.py` |
| **M3** | Sửa tham số độ dài pivot SMC (`swing_len=5` thay vì `50`) | Parity thất bại và liệt kê các `signal_id` lệch | `len(orig_ids ^ mut_ids) > 0` (`PASSED`) | `test_strategy_core_v311.py` |
| **M4** | Làm `on_bar_close` nhận nến chưa đóng (`is_closed=False`) và dịch `signal_close_ms` 1 nến | Cổng B3 trả về `FAIL` | B3 trả `FAIL` với `UNCLOSED_BAR_ACCEPTED` và `ONLY_IN_LIVE` (`PASSED`) | `test_strategy_core_v311.py` |
| **M5** | Chèn `from autotrade_v2.app.strategy import final14_config` vào file tạm | Quét AST phát hiện vi phạm ranh giới | AST scanner bắt được `autotrade_v2` (`PASSED`) | `test_strategy_core_v311.py` |

---

## F. REGRESSION (ĐỐI CHIẾU 3 CHẾ ĐỘ GOLDEN & 12 CỔNG VERIFY)

Chạy lại cả 3 chế độ `--golden` bằng `strategy_core` (`20FINAL@1`) vào thư mục tạm (`ledger_hash_compare.log`), kết quả content hash của toàn bộ 4 bảng ledger trùng khớp **100%** với `runs/golden/v3-08/<MODE>/` và vượt qua **12/12 cổng kiểm định** với trạng thái tổng thể **`VERIFIED`**:

| Chế độ (`Mode`) | `signals.parquet` | `trades.parquet` | `skipped_signals.parquet` | `rejected_signals.parquet` | Overall Validation Status |
|:---|:---|:---|:---|:---|:---:|
| **`ALL_SIGNALS`** | `5,984` rows (`52ed9b55...` = MATCH) | `8,258` rows (`9ca9215c...` = MATCH) | `0` rows (`206e8478...` = MATCH) | `0` rows (`245de4d4...` = MATCH) | **`VERIFIED`** (12/12 PASS, `59.4s`) |
| **`ONE_TRADE_PER_COMBO`** | `5,984` rows (`52ed9b55...` = MATCH) | `6,273` rows (`6fb3c2f2...` = MATCH) | `1,828` rows (`6f49c7d6...` = MATCH) | `0` rows (`245de4d4...` = MATCH) | **`VERIFIED`** (12/12 PASS, `52.02s`) |
| **`BINGX_PORTFOLIO`** | `5,984` rows (`52ed9b55...` = MATCH) | `6,265` rows (`779fac41...` = MATCH) | `1,835` rows (`b585ce8e...` = MATCH) | `0` rows (`245de4d4...` = MATCH) | **`VERIFIED`** (12/12 PASS, `66.68s`) |

- Xác nhận module ngoài phạm vi: `git diff --stat feat/v3-10-validation-gates..HEAD -- autotrade_v2` **rỗng**; `git diff --stat feat/v3-10-validation-gates..HEAD -- runs/golden/v3-08` **rỗng**.

---

## G. REPRODUCIBILITY

- **Dataset Hash:** `07c92a7f9eb6...` (74 partitions, `3,156,480` nến M1 thật trong `data/m1_raw/`, `catalog/research.duckdb`)
- **Strategy Hashes (`20FINAL@1`):**
  - `signal_spec_hash`: `fe592c07fdeaee9a20e21c7a1443ae1ac94ab2ec9a8c30d92dcb1dcea997b26e`
  - `exit_spec_hash`: `ce10e2b1c328201266965d2b1a4d991136941320ff9c2cdb338a471d7927254d`
  - `spec_hash`: `e2dcfe7e10c2ff7c63b5a94b946721e1d671219d60b22a452c62f507af4cc058`
- **Code SHA (pre-evidence):** `6ef6c1eba1abd79b8e0eb6be9e3595d035c7697e` (`code_dirty=false`)
- **Lệnh tái lập chính:**
  - Trích & đối chiếu tham số: `.\.venv\Scripts\python.exe -m autotrade_v3.tools.extract_20final_params`
  - Full test suite: `$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m pytest autotrade_v3/tests -v`
  - Backtest 3 mode golden: `.\.venv\Scripts\python.exe -m autotrade_v3.cli backtest --strategy 20FINAL@1 --mode ALL --out-dir <TEMP_DIR> --golden`
  - Verify 12 cổng: `.\.venv\Scripts\python.exe -m autotrade_v3.cli verify <RUN_DIR> --output-dir <OUT_DIR>`

---

## H. CÁC VẤN ĐỀ TỒN ĐỌNG / GIỚI HẠN

1. **`mypy` chưa được cài trong `.venv`:** Mục 12 yêu cầu chạy `mypy` cho `strategy_core/`, tuy nhiên `.venv` hiện tại không có gói `mypy` (`No module named mypy`), do đó kiểm tra `mypy` mang nhãn `UNVERIFIED`.
2. **Giá trị Trailing (`trailing_act_pct=0.005`, `trailing_cb_pct=0.005`):** Tuân thủ mục 4.3, sử dụng mặc định `0.005 / 0.005` như bản V3 hiện tại và giá trị mặc định trong `autotrade_v2/app/config.py` (dòng 53–54). Giá trị biến môi trường thực tế trên server V2 live chưa được đối chiếu trong phạm vi PR offline này (`UNVERIFIED`).

---

## I. TỰ ĐÁNH GIÁ NGHIỆM THU

- **Trạng thái tự đánh giá:** **VERIFIED** (đối với toàn bộ tiêu chí AC1–AC9 và 12/12 cổng kiểm định trên dữ liệu M1 thật; riêng `mypy` chưa chạy được do thiếu gói trong `.venv` và biến môi trường trailing V2 live chưa đối chiếu theo đúng giả định mục 4.3).
- **Đề nghị:** Toàn bộ 9 Acceptance Criteria (AC1–AC9) đều đạt có bằng chứng truy xuất được. Kính chuyển Claude thực hiện Review độc lập (R1–R16) và Owner xem xét nghiệm thu, merge PR vào nhánh đích.
