# BẢNG ĐỐI CHIẾU BEFORE / AFTER — KHẮC PHỤC FINDINGS SAU GEMINI REVIEW

**HEAD trước khắc phục:** `57d131ab8af475cc063d0d9a20ec3264dde3d909`  
**Phạm vi:** Chỉ cập nhật `autotrade_v3/lab/filter_aware_validation.py`, `autotrade_v3/tests/test_filter_aware_validation.py`, `.github/workflows/v3-lab-tests.yml`, và hồ sơ báo cáo; **0 thay đổi** tại simulator, chiến lược, dữ liệu, `live`, `20FINAL`, `Golden`.

---

## 1. Bảng Before / After Từng Nhiệm vụ & Finding

| Hạng mục / Finding | Trước (`57d131a`) | Sau (HEAD mới) | Bằng chứng Kiểm thử |
|---|---|---|---|
| **Nhiệm vụ 1 — `FA1` 3-way `signal_id` alignment** | Chỉ đối chiếu `baseline` vs `signal_decisions` và kiểm tra trùng lặp; chưa kiểm tra tập `signal_id` trong `signals.parquet` khớp `baseline` (`run_set == base_set`). | Kiểm tra đầy đủ `base_set == run_set == dec_set`, phát hiện `MISSING_SIGNAL_IN_RUN_SIGNALS` và `SPURIOUS_SIGNAL_IN_RUN_SIGNALS`. | `test_negative_replaced_signal_id_in_run_signals` **PASSED**; `27/27` clean-tree runs `FA1=PASS`. |
| **Nhiệm vụ 2 — `FA4` đối soát cấp combo & trade** | Kiểm tra tổng portfolio, tổng theo symbol/combo (dung sai `1e-3`) và tổng `Own == VWAP`; chưa đối soát từng combo/symbol giữa `trades.parquet` và `analysis.json`, chưa kiểm tra bất biến từng trade. | Đối chiếu từng trade (`fee_entry+fee_exit==fee_total`, `gross_pnl-fee_total==net_pnl`), từng combo (`net_pnl`, `fees`, `pnl_own_entry` giữa `trades.parquet` và `analysis["combos"]`), từng symbol (`trades.parquet` vs `analysis["symbols"]`). | `test_negative_offsetting_combo_pnl_error` (`+$1/−$1`) **PASSED**; `27/27` clean-tree runs `FA4=PASS`. |
| **Nhiệm vụ 3 — Siết whitelist `FA5`** | Miễn trừ `D2` chỉ dựa vào `path in FILTER_RECON_PATHS` mà chưa kiểm tra độ lớn `expected`/`actual`. | Bắt buộc mọi violation `D2` miễn trừ phải khớp đúng `n_dropped` (`max_absolute_diff == n_dropped`, `sum_by_*` lệch đúng `n_dropped`, `expected - actual == n_dropped`, `n_dropped > 0`). | `test_negative_d2_valid_path_abnormal_magnitude` **PASSED**; `27/27` clean-tree runs `FA5=PASS`. |
| **Nhiệm vụ 4 / `F04` — CI `lab-quality-and-tests`** | **`FAIL`** (run `44245822729`: `ModuleNotFoundError: No module named 'streamlit'` ở 4 test dashboard có sẵn). | Sửa `.github/workflows/v3-lab-tests.yml` cài `autotrade_v3/requirements-dashboard.txt` (đồng bộ với `origin/v3_lab`). | Chạy lại CI trên HEAD mới (xác nhận kết quả thực tế). |
| **`F01` — Standard `B1`** | `INCONCLUSIVE` (27/27) | **`INCONCLUSIVE` (27/27)** — Giữ nguyên (không sửa validator, chưa có frozen ref cho `20FINAL_FIXED_V1@1`). | `FINAL_GATE_MATRIX.csv` |
| **`F02` / `F03` — Standard `D2`** | `FAIL` (18 run lọc), `INCONCLUSIVE` (3 `H0 STRESS`) | **`FAIL` (18 run lọc), `INCONCLUSIVE` (3 `H0 STRESS`)** — Giữ nguyên trung thực, không ép `PASS`. | `FINAL_GATE_MATRIX.csv` |
| **PnL / TP / SL / Số lệnh (27 runs)** | `H0`: `+$338.01`, `LOO-19`: `+$331.96`, `H2b`: `+$348.22` (`BASE`) | **Không đổi (`0.0000` sai lệch)**. | `RUN_MANIFESTS_AND_HASHES.csv` |
