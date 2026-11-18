# VALIDATION ROOT CAUSE ANALYSIS (36 RUNS — H0, LOO-19, H2a, H2b)

**Nguồn dữ liệu kiểm toán:**
- [AUDIT_36_RUNS_MATRIX_WITH_HASH.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/AUDIT_36_RUNS_MATRIX_WITH_HASH.csv)
- [D2_VIOLATION_AUDIT_DETAILED.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/D2_VIOLATION_AUDIT_DETAILED.csv)
- `autotrade_v3/validation/runner.py`, `gates_c.py`, `gates_d.py`

---

## 1. Phân loại Root Cause trên 36 Run Trước Sửa Lỗi

| Nhóm Run | Số run | Trạng thái cũ | Phân loại Root Cause |
|---|---:|---|---|
| **H0 (`BASE`, `STRESS`)** | 6 | `FAILED` (`C1 = FAIL`) | **(6) Lỗi validator / phiên bản code cũ:** Các run H0 trong `runs/fixed_tpsl_v1/` (`RUN_20261115_040644..043328`) được chạy tại commit `ec2c7f96` (`code_dirty=True`), TRƯỚC commit `14b5785` (`fix(v3-validation): make Gate C1 use cost_profile slippage`). Do đó `C1` kiểm tra entry price với slippage=0 thay vì 2 bps / 5 bps. |
| **H0 (`IDEAL`)** | 3 | `UNVERIFIED_DIRTY` | **(5) Thiếu chứng cứ clean-tree:** Chạy trên `ec2c7f96` khi working tree dirty (`code_dirty=True`). `B1 = INCONCLUSIVE` (chưa có frozen reference baseline cho `20FINAL_FIXED_V1@1`). |
| **H0 (`STRESS` D2)** | 3 | `D2 = INCONCLUSIVE` | **(4) Độ chính xác regex / report:** `1,645/1,646` checked, `0 violations`, chỉ có 1 số trong `report.md` không khớp regex parser. |
| **LOO-19 (9 runs)** | 9 | `FAILED` (`D2 = FAIL`, `A1..C3 = INCONCLUSIVE`) | **(1) Expected difference do filter có chủ đích (`D2`):** `D2` so `total_signals = 5,984` (trước lọc) với `sum_by_symbol = 5,668` (sau lọc `316` tín hiệu C14), tạo `2` violations/run.<br>**(4) Làm tròn M2M (`1e-4`):** 1 run (`BINGX_PORTFOLIO STRESS`) có `m2m_max_drawdown_dollars` lệch `0.0001` (`76.3591` vs `76.3592`).<br>**(5) Thiếu `dataset_manifest.json` trong `runs_dir`:** Khiến `A1, A2, B2, B3, C1, C3` trả về `INCONCLUSIVE`. |
| **H2a (9 runs)** | 9 | `FAILED` (`D2 = FAIL`, `A1..C3 = INCONCLUSIVE`) | **(1) Expected difference do filter có chủ đích (`D2`):** `8` violations/run do `5,984` vs `5,869` (lọc `115` tín hiệu C14).<br>**(4) Làm tròn M2M (`1e-4`):** 1 run (`BINGX_PORTFOLIO STRESS`) lệch `0.0001` (`80.5719` vs `80.5720`).<br>**(5) Thiếu `dataset_manifest.json` trong `runs_dir`.** |
| **H2b (9 runs)** | 9 | `FAILED` (`D2 = FAIL`, `A1..C3 = INCONCLUSIVE`) | **(1) Expected difference do filter có chủ đích (`D2`):** `8` violations/run do `5,984` vs `5,899` (lọc `85` tín hiệu C14).<br>**(4) Làm tròn M2M (`1e-4`):** 1 run (`BINGX_PORTFOLIO IDEAL`) lệch `0.0001` (`54.0187` vs `54.0186`).<br>**(5) Thiếu `dataset_manifest.json` trong `runs_dir`.** |

---

## 2. Kết luận Kiểm toán Root Cause

1. **Sai lệch accounting thực sự (`(2)`):** **`0` trên cả 36 run.** Không có bất kỳ sai lệch nào về Net PnL, Gross PnL, Fees, Slippage, Own/VWAP PnL hay Closed Drawdown.
2. **Sai lệch tín hiệu/Entry/Exit thực sự (`(3)`):** **`0` trên cả 36 run.**
3. **Nguyên nhân `D2 FAIL` giả:** `gate_d2` dùng `signals.parquet` (chưa lọc) đối chiếu với `analysis.symbols.*.signals` (đã trừ `FILTERED`). Giải pháp đúng là giữ nguyên validator dùng chung cho baseline/golden và bổ sung [filter_aware_validation.py](file:///d:/AutoTrade_c14/autotrade_v3/lab/filter_aware_validation.py) kiểm tra đẳng thức bảo toàn `Original Signals = Kept + Dropped`.
4. **Nguyên nhân `A1..C3 INCONCLUSIVE`:** Thiếu `dataset_manifest.json` trong `runs_dir` của thí nghiệm Lab; đã khắc phục bằng việc sao chép từ `runs/golden/v3-08/dataset_manifest.json` và chạy lại `run_validation` trên clean tree.
