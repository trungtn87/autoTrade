# GEMINI REVIEW DIFF SCOPE (`feat/v3-c14-loo-h2` vs `v3_lab`)

**Branch:** `feat/v3-c14-loo-h2`  
**Base (`origin/v3_lab`):** `ae57ea079376175d6ab27ba8bafa336566633085`  
**Merge-base:** `c99e4529ac1761614ccfd1fdbed8a92315e7a5d7` (`feat/v3-16-fixed-tpsl-backtest`)  
**Trạng thái PR #141:** `OPEN` (Draft), `MERGEABLE` (không có xung đột merge với `v3_lab`).

---

## 1. Phân loại Thay đổi So với `merge-base` (`c99e452`)

Toàn bộ các thay đổi trên `feat/v3-c14-loo-h2` đều là **file mới** trong phạm vi Lab (`autotrade_v3/lab/`, `autotrade_v3/tests/`, `scripts/`, `reports/v3-c14-loo-h2/`):

| Nhóm file | Đường dẫn trong repo | Mục đích | Ảnh hưởng Runtime Live/Baseline |
|---|---|---|---|
| **Filter-Aware Validator** | `autotrade_v3/lab/filter_aware_validation.py` | Kiểm toán độc lập `FA1..FA5` cho thí nghiệm Lab có lọc tín hiệu | **Không** (chỉ dùng trong Lab audit) |
| **Unit & Negative Tests** | `autotrade_v3/tests/test_filter_aware_validation.py`, `autotrade_v3/tests/test_c14_ichimoku_atr_causality.py` | Kiểm thử dương/âm tính `FA1..FA5` (11 tests) và zero look-ahead Ichimoku/ATR (3 tests) | **Không** |
| **Research & Audit Scripts** | `scripts/c14_filters.py`, `scripts/run_c14_loo_19.py`, `scripts/analyze_c14_features.py`, `scripts/run_c14_h2_ab.py`, `scripts/audit_c14_research.py`, `scripts/audit_c14_complete.py`, `scripts/run_clean_tree_repro.py` | Chạy thí nghiệm LOO-19, H2a, H2b, phân tích feature và tái lập clean-tree | **Không** |
| **Báo cáo & Bằng chứng** | `reports/v3-c14-loo-h2/**` | Lưu bảng đối chiếu, ledger giao dịch, báo cáo nghiệm thu và hồ sơ Gemini review | **Không** |

## 2. Xác minh Đường dẫn Bảo vệ (Protected Paths)

So với `c99e452` (`feat/v3-16-fixed-tpsl-backtest`), **0 dòng thay đổi** tại:
- `autotrade_v3/live/**`
- `autotrade_v2/**`
- `unified_app.py`
- `autotrade_v3/strategy_core/**` (`20FINAL`, `20FINAL_FIXED_V1`)
- `autotrade_v3/validation/**` (Shared Validator)
- `runs/golden/**`
- `reports/fixed_tpsl_v1/**`
