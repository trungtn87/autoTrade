# VALIDATION EVIDENCE — `BTC-USDT/C14` LOO-19 & H2 A/B RESEARCH

**Nhánh nghiên cứu:** `feat/v3-c14-loo-h2` (tạo từ `feat/v3-16-fixed-tpsl-backtest` tại SHA `c99e4529ac1761614ccfd1fdbed8a92315e7a5d7`)  
**Simulator:** `v3.16-fixed-tpsl`  
**Dataset Hash:** `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`

---

## 1. Bảng Trạng thái Kiểm chứng (Validation Gates)

| Hạng mục Kiểm tra | Trạng thái | Bằng chứng & Ghi chú |
|---|---|---|
| **Dataset Integrity & Hash** | **PASS** | Toàn bộ 27 lượt chạy (9 LOO-19 + 9 H2a + 9 H2b) kiểm tra `manifest.dataset_hash == ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`. |
| **Zero Look-Ahead (Ichimoku & ATR)** | **PASS** | `3/3` unit tests trong [test_c14_ichimoku_atr_causality.py](file:///d:/AutoTrade_c14/autotrade_v3/tests/test_c14_ichimoku_atr_causality.py) chứng minh cắt cụt dữ liệu quá khứ cho giá trị trùng khớp `atol=1e-12` và khớp `_IncIchimoku` từng nến. |
| **FIXED_TP_SL & No Trailing** | **PASS** | Mọi lượt chạy xác nhận `all(t.leg_type == "SINGLE")`, `trailing_act_pct=0.0`, `trailing_cb_pct=0.0`, `tp_pct=0.02`, `sl_pct=0.00666667`. |
| **Không thay đổi 19 combo ngoài phạm vi (LOO-19)** | **PASS** | Đối chiếu [loo_19_trade_reconciliation_ledger.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/loo_19_trade_reconciliation_ledger.csv): `diff_decs = 0` trên cả 9 cấu hình; `diff_trades = 0` trên `ALL_SIGNALS` & `ONE_TRADE_PER_COMBO`; `ETH-USDT` chênh lệch `$0.0000`. |
| **Bảo toàn Accounting Own vs VWAP** | **PASS** | Tổng PnL `BINGX_PORTFOLIO` bằng chính xác `ONE_TRADE_PER_COMBO` ở mọi cấu hình (`$516.3583` / `$338.0100` / `$57.2547` H0; `$499.0916` / `$331.9564` / `$68.6206` LOO-19; `$525.0876` / `$348.2177` / `$66.7973` H2b). |
| **Phí & Slippage theo Profile** | **PASS** | `IDEAL` (slippage = 0), `BASE` (2 bps/chiều), `STRESS` (5 bps/chiều), taker fee 5 bps/chiều. |
| **Gate `C2`, `C4`, `C5`, `D1` (Run Validation)** | **PASS** | Đạt `PASS` trên toàn bộ các lượt chạy đã lưu (`validation.json`). |
| **Gate `A1, A2, B1, B2, B3, C1, C3` (Post-save runner)** | **INCONCLUSIVE** | `RunManager.save_run` không truyền `data_layer` vào `ValidationGates.audit_run` sau khi lưu nên các gate này ở trạng thái `INCONCLUSIVE` trong `validation.json` (dù `A1, A2, B2, B3, C1, C3` đã `PASS` trên baseline `20FINAL_FIXED_V1` cùng dataset/simulator). |
| **Gate `D2` (Signal-count reconciliation khi dùng Filter)** | **FAIL (Kỹ thuật)** | `D2` báo `FAIL` (`max_absolute_diff = 85` ở H2b, `316` ở LOO-19) vì `ResearchAnalyzer` so `total_signals = 5,984` (trước lọc) với `sum_by_symbol = 5,899` (sau lọc `85` tín hiệu C14); `1,441/1,441` leaves khác không có sai lệch số học. |
| **Live Parity (`autotrade_v2`)** | **INCONCLUSIVE (`PARITY_PENDING_LIVE_VERIFICATION`)** | Không sửa live, không chạy live parity theo quy định Lab-only. |
| **Không sửa File/Baseline bị cấm** | **PASS** | `git status -s` xác nhận 0 file tracked bị sửa (`autotrade_v3/live`, `autotrade_v2`, `unified_app.py`, `20FINAL`, `reports/fixed_tpsl_v1/` nguyên vẹn). |

---

## 2. Kết quả Unit & Regression Tests

Chạy ngày `2026-11-16`:
```
pytest autotrade_v3/tests/test_c14_ichimoku_atr_causality.py autotrade_v3/tests/test_fixed_tpsl_v1.py autotrade_v3/tests/test_fixed_tpsl_execution_scenarios.py -v
```
- **Kết quả:** **`15 passed in 1.72s`** (0 failed, 0 skipped).

---

## 3. Run IDs & Đường dẫn Lưu trữ trên Máy Lab

- **Baseline 20 combos:** `d:/AutoTrade/runs/fixed_tpsl_v1/RUN_20261115_040644..043328`
- **LOO-19 (`C14_DISABLED`):** `d:/AutoTrade_c14/runs/v3_c14_loo_19/RUN_20261115_163016..163909`
- **H2a (`Overextension <= 1.5`):** `d:/AutoTrade_c14/runs/v3_c14_h2a/RUN_20261115_*`
- **H2b (`Kumo >= 0.005`):** `d:/AutoTrade_c14/runs/v3_c14_h2b/RUN_20261115_*`
- **Báo cáo & CSV tổng hợp:** `d:/AutoTrade_c14/reports/v3-c14-loo-h2/`
