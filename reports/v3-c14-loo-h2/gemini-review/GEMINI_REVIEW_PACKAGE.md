# GEMINI REVIEW PACKAGE — KIỂM TOÁN ĐỘC LẬP `BTC-USDT/C14` (`LOO-19` & `H2b`)

**Repository:** `trungtn87/autoTrade`  
**PR:** https://github.com/trungtn87/autoTrade/pull/141 (`Draft`, target `v3_lab`, `MERGEABLE`)  
**Branch:** `feat/v3-c14-loo-h2`  
**Base (`origin/v3_lab`):** `ae57ea079376175d6ab27ba8bafa336566633085` (merge-base `c99e4529ac1761614ccfd1fdbed8a92315e7a5d7`)  
**Commit chạy 27 Clean-Tree Runs:** `4ed1497c5d1f2886c52564012e8b407861975833`  
**Commit tham chiếu trước gói Gemini Review:** `1299f1c29ba956c5e12195d8722601b5e021093f` (chỉ bổ sung dung sai làm tròn trong `autotrade_v3/lab/filter_aware_validation.py` và báo cáo `acceptance-final/`, **0 thay đổi** ở simulator/strategy/data/filters).

---

## 1. Executive Summary

- **Nghiệm thu kỹ thuật Local Lab:** **`PASS`** — 27/27 clean-tree runs (`H0`, `LOO-19`, `H2b` × 3 modes × 3 profiles) tái lập chính xác `0.0000` sai lệch Net PnL với `code_dirty = False`; `FA1–FA5` PASS 27/27; `43/43` unit/negative/regression tests PASS; `0` diff trên `live`, `autotrade_v2`, `unified_app.py`, `strategy_core`, `validation`, `runs/golden`.
- **Những điểm chưa đóng / còn `FAIL` hoặc `INCONCLUSIVE`:**
  - **GitHub Actions CI (`lab-quality-and-tests`) `FAIL` trên `1299f1c`:** Thiếu package `streamlit` trên CI runner gây lỗi import ở 4 file test dashboard có sẵn (`test_dashboard_*.py`), không do code C14 nhưng khiến trạng thái CI của PR #141 đang đỏ.
  - **Standard Gate `B1` (`INCONCLUSIVE` 27/27):** `20FINAL_FIXED_V1@1` chưa có frozen reference baseline đăng ký trước.
  - **Standard Gate `D2` (`FAIL` 18/18 run có lọc; `INCONCLUSIVE` 3 run `H0 STRESS`):** Validator chuẩn so sánh `5,984` tín hiệu trước lọc với tổng sau lọc (`5,668` hoặc `5,899`).
  - **Độ bền chiến lược `H2b` (`INCONCLUSIVE`):** Dù `H2b` vượt `H0` (`+$10.21` BASE, `+$9.54` STRESS), `H2b` vẫn âm ở `STRESS` (`-$1.8234` Own PnL), thua `LOO-19` ở `STRESS` (`+$66.80` vs `+$68.62`), có M2M DD `BASE` cao hơn `LOO-19` (`$62.92` vs `$57.24`), 6 tháng cuối `05–10/2026` âm (`-$1.18`), và độ nhạy ngưỡng Kumo `0.3%–0.7%` cho thấy đỉnh nhọn tại `0.5%` thay vì một cao nguyên ổn định.
  - **Live/Backtest Parity:** Vẫn ở trạng thái `PARITY_PENDING_LIVE_VERIFICATION`.

---

## 2. Phạm vi và Mục tiêu Nghiên cứu

- **Mục tiêu:** Xác định đóng góp biên thực tế của `BTC-USDT/C14` (`combo_id=104`, Ichimoku 1H `12/30/60`, Layer 2 `OB`, `FIXED_TP_SL` `2.00% / 0.666667%`) trong danh mục `20FINAL_FIXED_V1@1`, chẩn đoán nguyên nhân thua lỗ, và kiểm thử A/B hai bộ lọc Ichimoku/ATR đăng ký trước (`H2a`, `H2b`) trong phạm vi **Local Lab Only** (không sửa live, không deploy).

---

## 3. Git SHA, Baseline và Dataset

- **Strategy Baseline:** `20FINAL_FIXED_V1@1` (`signal_spec_hash = 4729dfeabea57994d1e955c9f8ce4716b4fbe118cbce2765fa8fddeeaf17da79`, `exit_spec_hash = 451883a51503a3f4b3c2dd286f9e96246ddbc3732efc46cfe6a1e63ae998abcd`).
- **Simulator:** `v3.16-fixed-tpsl`, Ambiguity Policy: `CONSERVATIVE`.
- **Dataset:** BTCUSDT & ETHUSDT M1 (`2023-11-01` – `2026-11-01`), hash: `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`.
- **Cấu hình vốn:** Equity `$1,000`, Notional `$100`/lệnh, Taker fee `5 bps`/chiều, Slippage `IDEAL = 0 bps`, `BASE = 2 bps`/chiều, `STRESS = 5 bps`/chiều.
- **Lịch sử Commit trên `feat/v3-c14-loo-h2`:**
  - `829d597`: Nghiên cứu ban đầu LOO-19, Feature Diagnostics, H2a/H2b (`reports/v3-c14-loo-h2/`).
  - `9673931`, `4794acb`, `5715a0e`: Kiểm toán order chain, H2b math, Kumo sensitivity (`0.3%–0.7%`), D2 audit.
  - `494c4e5`, `4ed1497`: Thêm `filter_aware_validation.py`, negative tests, và chạy lại 27 clean-tree runs (`code_dirty=False`).
  - `1299f1c`: Cập nhật dung sai làm tròn 20 combo (`1e-3`) và xuất `reports/v3-c14-loo-h2/acceptance-final/`.

---

## 4. Thiết kế `H0`, `LOO-19`, `H2a`, `H2b`

- **H0 (Baseline):** Giữ nguyên 20 combo (`5,984` tín hiệu, trong đó C14 có `316` tín hiệu: 174 BUY, 142 SELL).
- **LOO-19 (`C14_DISABLED`):** Loại bỏ hoàn toàn `316` tín hiệu `BTC-USDT/C14`; giữ nguyên 19 combo còn lại (`5,668` tín hiệu).
- **H2a (`Overextension Filter`):** Chỉ giữ Entry C14 khi `abs(Close - Kijun30) / ATR14 <= 1.5` (loại `115` tín hiệu C14).
- **H2b (`Kumo Thickness Filter`):** Chỉ giữ Entry C14 khi `abs(SpanA - SpanB) / Close >= 0.005` (`0.5%`, loại `85` tín hiệu C14).
- **H2ab:** Không chạy theo quy định thiết kế vì `H2a` làm giảm hiệu suất ở cả IDEAL (`-$16.63`) và BASE (`-$9.16`).

---

## 5. Kết quả Backtest 3 Năm (`BINGX_PORTFOLIO` & `ONE_TRADE_PER_COMBO`)

| Phương án | IDEAL Port PnL | BASE Port PnL | STRESS Port PnL | M2M DD BASE | C14 Own PnL BASE | C14 VWAP PnL BASE | C14 Own PnL STRESS |
|---|---:|---:|---:|---:|---:|---:|---:|
| **H0** | +\$516.3583 | +\$338.0100 | +\$57.2547 | \$65.7158 | +\$6.0536 | -\$17.1414 | -\$11.3659 |
| **LOO-19** | +\$499.0916 | +\$331.9564 | **+\$68.6206** | **\$57.2396** | \$0.0000 | \$0.0000 | \$0.0000 |
| **H2a** | +\$499.7280 | +\$328.8541 | +\$57.4611 | \$60.9968 | -\$3.1023 | -\$17.0488 | -\$11.1595 |
| **H2b** | **+\$525.0876** | **+\$348.2177** | +\$66.7973 | \$62.9187 | **+\$16.2613** | -\$0.3972 | -\$1.8234 |

- **Chi tiết H2b `BASE` (`220` giao dịch C14):** `71` TP / `149` SL (Win Rate `32.27%`), Gross PnL `+$21.6065`, Fees `\$22.0038`, Slippage `\$8.8015`, Own PnL `+$16.2613`, VWAP PnL `-$0.3972`, Portfolio Net PnL `+$348.2177` (hơn `LOO-19` đúng `+$16.2613`).

---

## 6. Ma trận 27 Clean-Tree Runs (`H0`, `LOO-19`, `H2b`)

Chi tiết đầy đủ từng run (`run_id`, `trades_sha256`, `trades_content_sha256`, `A1..D2`, `FA1..FA5`) nằm tại:
- `reports/v3-c14-loo-h2/acceptance-final/FINAL_GATE_MATRIX.csv`
- `reports/v3-c14-loo-h2/acceptance-final/RUN_MANIFESTS_AND_HASHES.csv`
- `reports/v3-c14-loo-h2/gemini-review/GEMINI_REVIEW_EVIDENCE.json`

Tóm tắt trạng thái trên 27 run (`4ed1497`, `code_dirty=False`):
- `A1, A2, B2, B3, C1, C2, C3, C4, C5, D1`: **`PASS` 27/27**.
- `FA1, FA2, FA3, FA4, FA5`: **`PASS` 27/27**.
- `B1`: **`INCONCLUSIVE` 27/27**.
- Standard `D2`: `6 PASS` (`H0` IDEAL/BASE), `3 INCONCLUSIVE` (`H0` STRESS), `18 FAIL` (`LOO-19` & `H2b` do đếm tín hiệu trước/sau lọc).

---

## 7. Đối soát Cấp Giao dịch (Order Chain & VWAP Conservation)

Nguồn: `reports/v3-c14-loo-h2/ORDER_CHAIN_AUDIT_SUMMARY.json`, `H2B_C14_RECONCILIATION_SUMMARY.json`, `loo_19_trade_reconciliation_ledger.csv`:
- **19 combo ngoài C14:** `5,668` tín hiệu gốc, `3,863` ACCEPTED / `1,805` SKIPPED (`BASE`) hoàn toàn **không đổi** giữa `H0`, `LOO-19` và `H2b` (`0` sai lệch `entry_time_ms`, Own PnL cố định `+$331.9564`).
- **Bảo toàn VWAP (`BINGX_PORTFOLIO`):** Tổng `pnl_vwap` trên toàn danh mục luôn bằng chính xác tổng `pnl_own_entry` (`+$348.2177` ở H2b BASE: `-$0.3972` C14 VWAP + `+$348.6149` 19 combo khác = `+$16.2613` C14 Own + `+$331.9564` 19 combo Own).
- **Bài học kế toán:** Không thể dùng `VWAP Allocated PnL` (`-$17.1414` H0, `-$0.3972` H2b) để đánh giá đóng góp biên; phải chạy lại danh mục (`Portfolio 20 - Portfolio 19`).

---

## 8. Phân tích `FA1–FA5` & Cơ sở Toán học của Dung sai (`1299f1c`)

- **Mã nguồn:** `autotrade_v3/lab/filter_aware_validation.py` và `autotrade_v3/tests/test_filter_aware_validation.py`.
- **Cơ sở toán học của dung sai tại `1299f1c`:**
  - `analysis.json` làm tròn riêng từng `combo.net_pnl` tới 4 chữ số thập phân (`round(x, 4)`), sai số làm tròn tối đa mỗi combo là `0.5 × 10^-4`.
  - Khi cộng `N = 20` combo, cận trên sai số tích lũy tất định là `20 × 0.5 × 10^-4 = 10^-3` (`0.001`). Tương tự với `2` symbol, cận trên là `2 × 0.5 × 10^-4 = 10^-4` (đặt `2e-4` cho an toàn biên float).
  - **Không che giấu sai lệch PnL thực sự:** Kiểm tra `NET_PNL_MISMATCH` (`sum(trades.net_pnl)` vs `analysis.pnl.net_pnl`), `FEES_MISMATCH`, và `VWAP_OWN_CONSERVATION_MISMATCH` vẫn giữ chặt tại **`1e-4`** (`$0.0001`). Test biên `test_negative_boundary_tolerance_enforcement` chứng minh sai lệch `2e-4` ở `net_pnl` hoặc `1.1e-3` ở tổng combo đều bị bắt `FAIL`.
- **Kết quả chạy test trên HEAD mới nhất:** **`43/43 passed in 3.16s`** (`11/11` positive/negative tests trong `test_filter_aware_validation.py`, `3/3` zero look-ahead tests, `5/5` `20FINAL_FIXED_V1` tests, `7/7` execution scenario tests, `17/17` Gate C1 cost profile tests).

---

## 9. Những Gate Còn `FAIL` / `INCONCLUSIVE`

| Gate / Check | Phạm vi | Trạng thái | Nguyên nhân gốc & Ý nghĩa |
|---|---|---|---|
| **CI `lab-quality-and-tests`** | PR #141 (`1299f1c`) | **`FAIL`** | Thiếu `streamlit` trên runner CI (lỗi ở 4 file test dashboard có sẵn). Không phải lỗi mô phỏng C14, nhưng CI của PR hiện đỏ. |
| **Standard `B1`** | 27/27 runs | **`INCONCLUSIVE`** | `20FINAL_FIXED_V1@1` chưa có frozen reference baseline đăng ký trước trong `runs/golden/v3-08`. |
| **Standard `D2`** | 18/18 runs có lọc (`LOO-19`, `H2b`) | **`FAIL`** | `gate_d2` so `signals.parquet` (`5,984`) với số tín hiệu sau lọc (`5,668`/`5,899`) gây `2` hoặc `8` violations/run, cộng `1` sai lệch `1e-4` ở `m2m_max_drawdown_dollars` trên 2 run. Đây là hạn chế của validator chuẩn khi gặp filter, không phải lỗi accounting. |
| **Standard `D2`** | 3/3 `H0 STRESS` runs | **`INCONCLUSIVE`** | Regex parser của `D2` kiểm tra `1645/1646` số với `0` violations (1 token markdown không khớp regex). |
| **Live Parity** | `autotrade_v2` vs `v3` | **`INCONCLUSIVE`** | `PARITY_PENDING_LIVE_VERIFICATION` (ngoài phạm vi Lab-only). |

*(Phân biệt rõ: **Technical Validation Acceptance** trên Local Lab đã đạt qua `A1,A2,B2,B3,C1..C5,D1` + `FA1..FA5`, khác hoàn toàn với **Live/Backtest Parity** hiện vẫn `UNVERIFIED`.)*

---

## 10. Kết quả Kiểm toán Clean Tree (`4ed1497` → `1299f1c`)

- **27/27 runs** chạy trên `d:\AutoTrade_c14_clean` (`4ed1497`) với `code_dirty = False`, `dataset_hash = ad51966ec...`, xóa sạch `signal_cache` trước khi chạy (`H0__ALL_SIGNALS__IDEAL` = `COMPUTED`, 26 run sau dùng lại cache vừa sinh trong cùng worktree).
- **Khác biệt `4ed1497` → `1299f1c`:** `git diff --stat 4ed1497..1299f1c` xác nhận **0 byte thay đổi** ở simulator, strategy, filter, data layer; chỉ cập nhật `filter_aware_validation.py` và thư mục báo cáo `acceptance-final/`.
- **Reproducibility:** Mọi chỉ số kinh tế (Net PnL, entries, TP/SL, fees, M2M DD) khớp `0.0000` với nghiên cứu trước đó. `trades_content_sha256` khác với chuỗi hash cũ trong `AUDIT_36_RUNS_MATRIX_WITH_HASH.csv` chỉ do khác hàm chuẩn hóa tuần tự hóa CSV/cột.

---

## 11. Kiểm soát Look-Ahead (`Ichimoku 12/30/60` & `ATR(14)`)

- `_ichimoku` (`autotrade_v3/strategy_core/strategies/final20/logic.py`) tính `span_a` và `span_b` bằng `.shift(+30)` (dịch giá trị quá khứ về hiện tại), khớp step-by-step với `_IncIchimoku`.
- `atr(df, 14)` dùng Wilder RMA trên nến 1H đã đóng.
- Kiểm chứng cắt cụt dữ liệu (`test_c14_ichimoku_atr_causality.py` — `3/3 PASS`) và kiểm chứng trên `12` tín hiệu BTC-USDT thực tế (`2023–2026`, `CLEAN_TREE_REPRO_AUDIT.json`): sai số tuyệt đối tối đa = **`0.0`**.

---

## 12. Kiểm tra Thống kê, Độ nhạy & Overfitting (`H2b`)

- **Phân tách thời gian `ONE_TRADE_PER_COMBO__BASE` (`H2b` vs `H0`):**
  - `24M (2023-11 → 2025-10)`: `145` lệnh, `+$8.5980`, PF `1.1104` (H0: `+$9.5737`).
  - `6M-1 (2025-11 → 2026-04)`: `43` lệnh, `+$8.8385`, PF `1.4160` (H0: `+$4.1204`).
  - `6M-2 (2026-05 → 2026-10)`: `32` lệnh, **`-$1.1752`**, PF `0.9350` (H0: `-$7.6404`).
  - *Lưu ý:* Toàn bộ 3 năm đã dùng để hình thành/kiểm tra giả thuyết nên đây là **historical robustness / research OOS**, **KHÔNG phải prospective OOS**.
- **Độ nhạy ngưỡng Kumo (`reports/v3-c14-loo-h2/KUMO_SENSITIVITY_SIMULATOR.csv`):**
  - `0.3%`: `+$338.43` (`BASE`), `+$55.41` (`STRESS`)
  - `0.4%`: `+$343.80` (`BASE`), `+$62.11` (`STRESS`)
  - `0.5%`: **`+$348.22`** (`BASE`), **`+$66.80`** (`STRESS`)
  - `0.6%`: `+$331.64` (`BASE`), `+$51.72` (`STRESS`)
  - `0.7%`: `+$332.78` (`BASE`), `+$53.32` (`STRESS`)
  - → Ngưỡng `0.5%` nằm ở đỉnh nhọn cục bộ (tụt `-$16.58` khi nhích lên `0.6%`), **chưa chứng minh được vùng tham số ổn định**.

---

## 13. Phân tích Rủi ro, MaxDD & `STRESS`

- **Đánh đổi giữa `H2b` và `LOO-19` (`BINGX_PORTFOLIO`):**
  - `H2b` tối đa hóa lợi nhuận `IDEAL` (`+$525.09` vs `+$499.09`) và `BASE` (`+$348.22` vs `+$331.96`), giảm M2M DD so với `H0` (`$65.72 → $62.92`).
  - `LOO-19` tốt hơn `H2b` ở `STRESS` (`+$68.62` vs `+$66.80`) và có M2M DD thấp hơn (`$57.24` vs `$62.92` BASE; `$76.36` vs `$81.81` STRESS).
- **Rủi ro SL cố định `0.667%`:** `41.5%` lệnh SL của C14 (`85/205`) từng đạt `MFE >= +0.5%` trước khi quay đầu — phản ánh độ nhạy cao với nhiễu M1 và slippage `STRESS`.

---

## 14. Danh sách Phát hiện Chưa Xử lý

Xem đầy đủ trong `reports/v3-c14-loo-h2/gemini-review/GEMINI_REVIEW_FINDINGS.csv` (`F01`–`F07` vẫn ở trạng thái `FAIL` hoặc `INCONCLUSIVE`).

---

## 15. Kết luận Được Dữ liệu Chứng minh vs Giả thuyết

- **Được chứng minh bởi dữ liệu:**
  1. `LOO-19` không làm thay đổi 19 combo còn lại (`diff_decs=0`); đóng góp biên thực tế của C14 bằng Own Entry PnL (`+$6.0536` BASE, `-$11.3659` STRESS), không phải VWAP PnL (`-$17.1414`).
  2. `H2a` làm xấu hiệu suất (`REJECT`). `H2b` cải thiện `H0` trên cả 9 cấu hình nhờ loại bỏ 79 lệnh mây mỏng `< 0.5%` (Win Rate `24.05%`, `-$11.48` BASE).
  3. Toàn bộ 27 clean-tree runs tái lập `0.0000` sai lệch với `FA1–FA5` PASS và zero look-ahead.
- **Vẫn còn là giả thuyết / chưa đủ chứng cứ:**
  1. Liệu `H2b` (`0.5%`) có duy trì được lợi thế `+10.21$` BASE trên dữ liệu prospective tương lai hay chỉ là nhiễu cục bộ trên 3 năm lịch sử.
  2. Khả năng sống sót thực tế của `H2b` dưới mức trượt giá cao (`STRESS` vẫn âm `-$1.8234`).
  3. Live execution parity của `20FINAL_FIXED_V1@1` và `H2b` trên `autotrade_v2`.

---

## 16. Sáu Câu hỏi Dành riêng cho Gemini

1. Thiết kế Filter-Aware FA1–FA5 có thực sự độc lập và đủ mạnh để phát hiện sai lệch accounting/order-chain hay không? Có lỗi nào bị bỏ qua do dung sai hoặc whitelist D2 không?
2. 27 run Clean-Tree có đủ bằng chứng về khả năng tái lập hay vẫn tồn tại lỗ hổng về cache, Git SHA, config hash và ledger hash?
3. Việc Standard B1 INCONCLUSIVE và D2 FAIL có làm mất hiệu lực của kết quả nghiên cứu hay chỉ giới hạn trạng thái nghiệm thu?
4. C14 H2b có lợi thế thống kê và kinh tế đáng tin cậy, hay kết quả +$10,21 BASE chỉ là nhiễu/overfitting trên 3 năm?
5. Nếu so sánh H0, LOO-19 và H2b theo cả lợi nhuận, Drawdown, STRESS và chi phí triển khai, phương án nào có bằng chứng tốt nhất?
6. PR #141 đã đủ an toàn để merge vào "v3_lab" cho mục đích nghiên cứu hay chưa? Những điều kiện nào phải hoàn tất trước khi xem xét bất kỳ thay đổi live nào?
