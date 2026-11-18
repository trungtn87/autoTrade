# FILTER-AWARE VALIDATION REPORT (27 CLEAN-TREE RUNS)

**Module:** [filter_aware_validation.py](file:///d:/AutoTrade_c14/autotrade_v3/lab/filter_aware_validation.py)  
**Test Suite:** [test_filter_aware_validation.py](file:///d:/AutoTrade_c14/autotrade_v3/tests/test_filter_aware_validation.py) (`10/10 passed`)  
**Ma trận Gate đầy đủ:** [FINAL_GATE_MATRIX.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/acceptance-final/FINAL_GATE_MATRIX.csv)

---

## 1. Thiết kế Cơ chế Filter-Aware Độc lập (`FA1`–`FA5`)

Thay vì sửa đổi validator dùng chung (`autotrade_v3/validation/*`) có thể ảnh hưởng đến baseline/golden, cơ chế Filter-Aware được xây dựng độc lập cho thí nghiệm Lab và kiểm tra 5 cổng:

1. **`FA1` — Signal Conservation (`Original Signals == Kept + Dropped`):**
   - Kiểm tra toàn bộ `5,984` tín hiệu gốc xuất hiện đúng 1 lần trong `signal_decisions.parquet`, không thiếu tín hiệu, không nhân đôi, không phát sinh tín hiệu mới.
2. **`FA2` — Non-Target Invariance & Filter Decision Fidelity:**
   - Kiểm tra 19 combo ngoài `BTC-USDT/C14` có `FILTERED = 0`.
   - Kiểm tra mọi quyết định `FILTERED` của `BTC-USDT/C14` đều có lý do hợp lệ và khớp `100%` với oracle độc lập (`LOO_C14_DISABLED`, `H2b_THIN_KUMO`).
3. **`FA3` — Order Chain Integrity (`Signal → Decision → Admission → Trade → Exit`):**
   - Mọi `ACCEPTED` có đúng 1 trade, không có trade nào thuộc tín hiệu `FILTERED`/`SKIPPED`/`REJECTED`, `entry_time_ms >= signal_close_ms + 1`, `exit_time_ms >= entry_time_ms`.
4. **`FA4` — Accounting, Fees, Own/VWAP Conservation & Closed DD:**
   - Đối chiếu `trades.parquet` với `analysis.json`: `net_pnl` (`<= 1e-4`), `total_fees` (`<= 1e-4`), bảo toàn `sum(pnl_own_entry) == sum(pnl_vwap)` (`<= 1e-4`), `closed_trade_max_drawdown_dollars` (`<= 1e-3`), tổng theo symbol (`<= 2e-4`) và tổng 20 combo (`<= 1e-3` do mỗi combo làm tròn 4 chữ số thập phân).
5. **`FA5` — Dataset Hash & Non-Filter D2 Audit:**
   - Xác minh `dataset_hash == ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`.
   - Phân loại toàn bộ violations trong `D2` của validator chuẩn: chỉ chấp nhận sai lệch đếm tín hiệu đã lọc (`FILTER_RECON_PATHS` + `reconciliation_parity`) và sai số làm tròn M2M `<= 2e-4`; mọi vi phạm khác sẽ đánh `FAIL`.

---

## 2. Kết quả trên 27 Run Clean-Tree (`H0`, `LOO-19`, `H2b` × 3 Modes × 3 Profiles)

| Variant | Số run | `FA1..FA5` (`fa_overall`) | Standard Gates (`A1,A2,B2,B3,C1..C5,D1`) | `B1` (Frozen ref) | Standard `D2` |
|---|---:|---|---|---|---|
| **H0** | 9 | **9/9 `PASS`** | **9/9 `PASS`** | 9/9 `INCONCLUSIVE` | 6 `PASS`, 3 `INCONCLUSIVE` (`STRESS` regex) |
| **LOO-19** | 9 | **9/9 `PASS`** | **9/9 `PASS`** | 9/9 `INCONCLUSIVE` | 9/9 `FAIL` (chỉ `2` filter-recon viols + `1` M2M `1e-4`) |
| **H2b** | 9 | **9/9 `PASS`** | **9/9 `PASS`** | 9/9 `INCONCLUSIVE` | 9/9 `FAIL` (chỉ `8` filter-recon viols + `1` M2M `1e-4`) |

**Kết luận:** Khi chạy trên clean tree kèm `dataset_manifest.json`, toàn bộ các Gate `A1, A2, B2, B3, C1, C2, C3, C4, C5, D1` đều **`PASS` 27/27**, và toàn bộ `FA1..FA5` đều **`PASS` 27/27**. Không còn `C1 FAIL` hay `A1..C3 INCONCLUSIVE`.
