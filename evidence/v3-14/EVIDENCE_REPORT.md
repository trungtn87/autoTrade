# V3-14 EVIDENCE REPORT: Run Store, Signal Cache & Comparison

## A. Tổng quan & Trạng thái Tuân thủ Nguyên tắc (N1–N10)

- **Nhánh làm việc:** `feat/v3-14-run-store` (từ base `e551de5`)
- **Commit code:** `961c266`, `7c5acee`, `866bcbe`, `06d4201`, `9d05046`, `bebd017`
- **Kiểm tra vùng cấm (`N1`, `N4`):** `git diff --stat e551de5..HEAD -- autotrade_v2 runs/golden autotrade_v3/strategy_core autotrade_v3/sim/leg.py autotrade_v3/sim/costs.py autotrade_v3/sim/ambiguity.py` trả về rỗng.
- **Mạng & Dữ liệu gốc (`N2`, `N3`):** Không gọi BingX/Supabase/Gemini; mọi đột biến thực hiện trên bản sao trong thư mục tạm.
- **Ký hiệu nguồn dữ liệu (`N7`, `N9`):** Mọi kết quả tổng hợp được gắn nhãn `[SYNTHETIC]`.

## B. Kiến trúc & Các Gói Mới

- **`autotrade_v3/runstore/`**:
  - `hashing.py`: `compute_run_id` (`R_` + 16 hex SHA-256 trên 10 tham số đầu vào), `df_content_hash`, `artifact_hashes`.
  - `store.py`: `RunStore` lưu bất biến tại `runs/lab/store/<run_id>/` (hoặc `runs/lab/scratch/` khi dirty tree), kiểm tra `NonReproducibleError` / `reproducible_hit=True`.
  - `index.py`: `StoreIndex` (`index.duckdb`) có thể xóa và dựng lại hoàn toàn qua `reindex()`.
  - `equity.py`: xây dựng `equity.parquet`.
  - `signal_cache.py`: `SignalCache` khóa theo `(dataset_hash, signal_spec_hash, symbols, start, end, include_warmup)`, kiểm tra `file_sha256` + `content_hash` (`CACHE_CORRUPT` khi sai lệch), và `rebind_exit_policies` tái gắn chính sách thoát lệnh không cần gọi `strategy.generate_signals()`.
  - `diff.py`: `diff_runs` tạo `diff.parquet` + `diff_summary.json` với thứ tự ưu tiên `DATA > SIGNAL > ENTRY > AMBIGUITY > EXIT_LOGIC > COST > IDENTICAL > UNEXPLAINED` và bất biến `|sum(delta_net) - (net_b - net_a)| <= 1e-9` (`DiffInvariantError`).
- **`autotrade_v3/external/` & `autotrade_v3/filters/`**:
  - `annotations.py`: `AnnotationStore` (DuckDB), bắt buộc `as_of_ms`, gắn cờ `AS_OF_UNVERIFIED` khi chưa xác minh nguồn, và `query_as_of` ép `as_of_ms <= signal_close_ms`.
  - `annotation_filter.py` & `base.py`: `SignalFilter`, `FilterContext`, `AnnotationGuardFilter`, trạng thái `FILTERED` trong `signal_decisions.parquet`.
  - `ledger_import.py`: `import_external_ledger` ánh xạ CSV/Parquet ngoài vào `RunStore` (`source="EXTERNAL"`), ghi `missing_columns`.
- **`validation/`, `lab/`, `cli.py`, `sim/engine.py`**:
  - Hỗ trợ `FILTERED` trong `C5`, bố cục `RunStore` trong `D1`, chặn `VERIFIED` khi `as_of_unverified=True`, cùng các lệnh CLI `store list/show/reindex`, `diff`, `cache stats`, `annotations import`, `import-ledger`.

## C. Đối chiếu RunStore với Golden V3-13 (B3)

Log gốc: `evidence/v3-14/store_vs_golden.log` — **VERIFIED**

| Mode | RunStore `run_id` | `legs.parquet` vs `trades.parquet` | `signals.parquet` | `signal_decisions.parquet` | Re-run `reproducible_hit` |
|---|---|---|---|---|---|
| `ALL_SIGNALS` | `R_cbeda03593afcbb5` | `569bceccfe188d9d..` (`match=True`) | `52ed9b559dfba752..` (`match=True`) | `2b3455653e043ac4..` (`match=True`) | `True` |
| `ONE_TRADE_PER_COMBO` | `R_49d448154f17036b` | `ad8a69ea7922c3c6..` (`match=True`) | `52ed9b559dfba752..` (`match=True`) | `5b621544e832661c..` (`match=True`) | `True` |
| `BINGX_PORTFOLIO` | `R_f4ae5e858e8533ad` | `64812518e6012fed..` (`match=True`) | `52ed9b559dfba752..` (`match=True`) | `5b621544e832661c..` (`match=True`) | `True` |

- `StoreIndex.reindex()` sau khi xóa `index.duckdb`: `before=3 rebuilt=3 after=3`.

## D. Bằng chứng SignalCache (B4)

Log & manifest gốc: `evidence/v3-14/cache_proof/cache_proof.log`, `run{1,2,3}_manifest.json` — **VERIFIED**

- **Run 1 (baseline `ETH-USDT`, `ALL_SIGNALS`):** `run_id=R_9ba448bdd59dd5f6`, `signal_source=COMPUTED`, `signal_gen_calls=1`, `signal_compute_time_s=19.964992`, `total_s=50.437`.
- **Run 2 (đổi `tp_pct=0.035`, giữ `signal_spec_hash`):** `run_id=R_01180d0ce30eb5e7`, `signal_source=CACHE`, `signal_gen_calls=0`, `signal_compute_time_s=0.499004`, `total_s=30.816`.
- **Run 3 (đổi entry spec):** `run_id=R_eb9a9a5350372bc5`, `signal_source=COMPUTED`, `signal_gen_calls=1`, `signal_compute_time_s=19.325642`, `total_s=44.636`.

## E. Bằng chứng Bộ lọc & AnnotationGuard (B5)

Log gốc: `evidence/v3-14/annotations_guard.log` — **VERIFIED**

- Annotation đã xác minh (`as_of_verified=True`): `run_id=R_c56fb449b8e43a00`, `filtered=3409`, `C5=PASS`, `overall=PARTIALLY_VERIFIED`.
- Annotation chưa xác minh (`as_of_verified=False`): `run_id=R_c3c832454c863401`, `overall=PARTIALLY_VERIFIED` (bị chặn khỏi `VERIFIED`).

## F. Bằng chứng So sánh Per-Signal (B6)

Log & artifact gốc: `evidence/v3-14/diff/diff_runs.log` và các thư mục con `evidence/v3-14/diff/<pair>/` — **VERIFIED**

1. **`ideal_legacy_vs_conservative`** (dữ liệu thật, `IDEAL`, `LEGACY` vs `CONSERVATIVE`):
   - `IDENTICAL=5984`, `SIGNAL=0`, `COST=0`, `UNEXPLAINED=0`, `invariant_diff=0.0`.
2. **`all_signals_vs_one_trade_per_combo`** (dữ liệu thật, `IDEAL+CONSERVATIVE`, `ALL_SIGNALS` vs `ONE_TRADE_PER_COMBO`):
   - `IDENTICAL=4149`, `ENTRY=1835`, `UNEXPLAINED=0`, `invariant_diff=0.0`.
3. **`synthetic_ideal_vs_test_only`** (`[SYNTHETIC]`, `CONSERVATIVE`, `IDEAL` vs `TEST_ONLY`):
   - `AMBIGUITY=10`, `SIGNAL=0`, `UNEXPLAINED=0`, `invariant_diff=2.886579864025407e-15` (`<= 1e-9`).

## G. Nạp Ledger Ngoài & Đối chiếu Owner (B7)

Artifact gốc: `evidence/v3-14/external_ledger.md`

- **Đối chiếu backtest độc lập của Owner:** **BLOCKED_ON_OWNER** (`OWNER_EXTERNAL_LEDGER` trống; chưa có file ledger của Owner trong repo).
- **Minh họa `[SYNTHETIC]`:** `import_external_ledger` nạp `R_EXT_968295ba0a66` (`source=EXTERNAL`, `synthetic=True`), ghi đầy đủ `missing_columns`, đối chiếu 5 tầng với `R_SYN_INTERNAL` cho `SIGNAL=4`, `actual_delta_net=-0.340000`, `expected_delta_net=-0.340000`, `invariant_diff=1.11e-16` — **VERIFIED (SYNTHETIC)**.

## H. Kiểm thử Đột biến (M1–M6) & Chất lượng Mã

- **Mutation Tests (`evidence/v3-14/mutation_tests.log`) — VERIFIED:**
  - `M1`: annotation tương lai bị bỏ qua (`visible_rows=0`, `dropped_future=1`).
  - `M2`: sửa 1 byte `signals.parquet` trong cache -> `hit=False`, `warning=CACHE_CORRUPT`.
  - `M3`: sửa `net_pnl` của leg -> `diff_rows=1`, `D1=FAIL`.
  - `M4`: sửa `tp_pct` giữ nguyên `spec_hash` -> `C2=FAIL`.
  - `M5`: đột biến `diff.py` phá bất biến tổng -> `exit_code=1`.
  - `M6`: đổi `symbol` tín hiệu -> `SIGNAL` cho cả ID cũ và mới (`{'S1': 'SIGNAL', 'S1_RENAMED': 'SIGNAL'}`).
- **Static & Unit/Integration Suite (`evidence/v3-14/pytest_all.log`, `junit/all.xml`):**
  - `ruff check autotrade_v3`: `All checks passed!` — **VERIFIED**.
  - `python -m compileall -q autotrade_v3`: exit code `0` — **VERIFIED**.
  - `pytest autotrade_v3/tests`: **342 passed** (`1073.01s`) — **VERIFIED**.
  - `mypy`: **UNVERIFIED** (`mypy` chưa được cài trong `.venv`).

## I. Danh sách Artifact Đính kèm

- `evidence/v3-14/EVIDENCE_REPORT.md`
- `evidence/v3-14/git_state.txt`
- `evidence/v3-14/pytest_all.log`
- `evidence/v3-14/junit/all.xml`
- `evidence/v3-14/store_vs_golden.log`
- `evidence/v3-14/cache_proof/{cache_proof.log,run1_manifest.json,run2_manifest.json,run3_manifest.json}`
- `evidence/v3-14/annotations_guard.log`
- `evidence/v3-14/diff/{diff_runs.log,ideal_legacy_vs_conservative/,all_signals_vs_one_trade_per_combo/,synthetic_ideal_vs_test_only/}`
- `evidence/v3-14/external_ledger.md`
- `evidence/v3-14/mutation_tests.log`
