# NEGATIVE VALIDATION TESTS — FILTER-AWARE VALIDATOR

**Module kiểm chứng:** [filter_aware_validation.py](file:///d:/AutoTrade_c14/autotrade_v3/lab/filter_aware_validation.py)  
**Bộ test:** [test_filter_aware_validation.py](file:///d:/AutoTrade_c14/autotrade_v3/tests/test_filter_aware_validation.py)

---

## 1. Kết quả Chạy Unit & Regression Tests

Lệnh chạy:
```
pytest autotrade_v3/tests/test_gate_c1_cost_profiles.py autotrade_v3/tests/test_validation_mutations.py autotrade_v3/tests/test_c14_ichimoku_atr_causality.py autotrade_v3/tests/test_fixed_tpsl_v1.py autotrade_v3/tests/test_filter_aware_validation.py -v
```
- **Tổng số test:** **`55 passed in 348.89s`** (`0 failed`, `0 skipped`).
  - `17/17` Gate C1 cost profile tests (`test_gate_c1_cost_profiles.py`).
  - `20/20` Baseline mutation negative tests (`test_validation_mutations.py` — xác nhận validator baseline không bị ảnh hưởng).
  - `3/3` Ichimoku/ATR zero look-ahead tests (`test_c14_ichimoku_atr_causality.py`).
  - `5/5` `20FINAL_FIXED_V1` core tests (`test_fixed_tpsl_v1.py`).
  - `10/10` Filter-Aware positive & negative tests (`test_filter_aware_validation.py`).

---

## 2. Danh sách 10 Ca Kiểm thử Filter-Aware (`test_filter_aware_validation.py`)

| Test Case | Kịch bản Lỗi Tiêm vào (Fault Injection) | Gate Kỳ vọng Bắt lỗi | Kết quả |
|---|---|---|---|
| `test_filter_aware_validation_positive` | Run hợp lệ có lọc (`S1` DROP có lý do, `D2` có mismatch giả do filter) | Toàn bộ `FA1..FA5 = PASS` | **PASSED** |
| `test_negative_missing_signal_without_drop` | Xóa `S1` khỏi `signal_decisions` không có lý do `FILTERED` | `FA1 = FAIL` (`MISSING_SIGNAL_IN_DECISIONS`) | **PASSED** |
| `test_negative_duplicate_signal` | Nhân đôi 1 tín hiệu trong `signals.parquet` | `FA1 = FAIL` (`DUPLICATE_RUN_SIGNAL`) | **PASSED** |
| `test_negative_trade_mismatch_admission` | Tạo trade cho `S1` dù `S1` có trạng thái `FILTERED` | `FA3 = FAIL` (`ACCEPTED_WITHOUT_TRADE` / `TRADE_FOR_NON_ACCEPTED_SIGNAL`) | **PASSED** |
| `test_negative_pnl_mismatch` | Sửa `analysis.pnl.net_pnl` lệch khỏi tổng `trades.net_pnl` | `FA4 = FAIL` (`NET_PNL_MISMATCH`) | **PASSED** |
| `test_negative_fee_mismatch` | Sửa `analysis.pnl.total_fees` lệch khỏi tổng `trades.fee_total` | `FA4 = FAIL` (`FEES_MISMATCH`) | **PASSED** |
| `test_negative_wrong_entry_exit_timestamp` | Đặt `entry_time_ms = 1999 < signal_close_ms (2000)` (look-ahead) | `FA3 = FAIL` (`LOOKAHEAD_OR_EARLY_ENTRY`) | **PASSED** |
| `test_negative_vwap_not_conserved` | Sửa `pnl_vwap` khiến `sum(pnl_own_entry) != sum(pnl_vwap)` | `FA4 = FAIL` (`VWAP_OWN_CONSERVATION_MISMATCH`) | **PASSED** |
| `test_negative_non_target_combo_filtered` | Lọc nhầm tín hiệu của `ETH-USDT` (`S3`) ngoài phạm vi C14 | `FA2 = FAIL` (`NON_TARGET_COMBO_FILTERED`) | **PASSED** |
| `test_negative_unexplained_d2_violation` | Tiêm 1 violation `analysis.pnl.net_pnl` vào `validation.json` | `FA5 = FAIL` (`UNEXPLAINED_D2_VIOLATION`) | **PASSED** |
