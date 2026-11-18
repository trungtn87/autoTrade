# CHANGE SCOPE AUDIT — NHÁNH `feat/v3-c14-loo-h2`

**So sánh:** `feat/v3-16-fixed-tpsl-backtest` (`c99e452`) → `HEAD` trên `feat/v3-c14-loo-h2`.

---

## 1. Kiểm tra Phạm vi Bảo vệ (Protected Paths)

| Thư mục / File được Bảo vệ | Diff so với `feat/v3-16-fixed-tpsl-backtest` | Trạng thái |
|---|---|---|
| `autotrade_v3/live/**` | `0` files, `0` lines | **AN TOÀN (0 diff)** |
| `autotrade_v2/**` | `0` files, `0` lines | **AN TOÀN (0 diff)** |
| `unified_app.py` | `0` files, `0` lines | **AN TOÀN (0 diff)** |
| `autotrade_v3/strategy_core/**` (`20FINAL`, `20FINAL_FIXED_V1`) | `0` files, `0` lines | **AN TOÀN (0 diff)** |
| `autotrade_v3/validation/**` (Shared Validator) | `0` files, `0` lines | **AN TOÀN (0 diff)** |
| `runs/golden/**` (Golden Baseline) | `0` files, `0` lines | **AN TOÀN (0 diff)** |
| `reports/fixed_tpsl_v1/**` (Baseline Reports) | `0` files, `0` lines | **AN TOÀN (0 diff)** |

---

## 2. Danh sách File Mới Thêm trên Nhánh Nghiên cứu

1. **Lab Validation & Tests:**
   - `autotrade_v3/lab/filter_aware_validation.py` — Filter-Aware Validator (`FA1`–`FA5`) độc lập cho thí nghiệm Lab.
   - `autotrade_v3/tests/test_filter_aware_validation.py` — 10 tests dương/âm tính.
   - `autotrade_v3/tests/test_c14_ichimoku_atr_causality.py` — 3 tests zero look-ahead Ichimoku/ATR.
2. **Research & Audit Scripts:**
   - `scripts/c14_filters.py`
   - `scripts/run_c14_loo_19.py`
   - `scripts/analyze_c14_features.py`
   - `scripts/run_c14_h2_ab.py`
   - `scripts/audit_c14_research.py`
   - `scripts/audit_c14_complete.py`
   - `scripts/run_clean_tree_repro.py`
3. **Reports:**
   - `reports/v3-c14-loo-h2/*` và `reports/v3-c14-loo-h2/acceptance-final/*`.
