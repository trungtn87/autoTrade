# NHIỆM VỤ C — BÁO CÁO NGHIÊN CỨU A/B H2 ICHIMOKU/ATR (`BTC-USDT/C14`)

**Cấu hình giữ nguyên:** Ichimoku 1H `12/30/60`, Layer 2 `OB`, `FIXED_TP_SL` (`TP = 2.00%`, `SL = 0.666667%`, 100% một leg, không Trailing, không Breakeven).  
**Ngưỡng đăng ký trước (không tuning):**
- **H0:** Baseline `20FINAL_FIXED_V1@1` (không lọc).
- **H2a (Overextension Filter):** Chỉ giữ Entry C14 khi `abs(Close - Kijun30) / ATR14 <= 1.5`.
- **H2b (Kumo Thickness Filter):** Chỉ giữ Entry C14 khi `abs(SpanA - SpanB) / Close >= 0.005` (`0.5%`).
- **H2ab (Kết hợp):** Không chạy vì `H2a` làm xấu đáng kể cả IDEAL và BASE so với H0 (không có cơ sở kết hợp theo quy định thiết kế thí nghiệm).

**Bảng dữ liệu đầy đủ:** [C14_H2_AB_RESULTS.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/C14_H2_AB_RESULTS.csv), [C14_PORTFOLIO_IMPACT.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/C14_PORTFOLIO_IMPACT.csv), [c14_h2_breakdowns.json](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/c14_h2_breakdowns.json).

---

## 1. So sánh 9 Cấu hình: H0 vs LOO-19 vs H2a vs H2b

| Mode | Profile | H0 Port PnL | LOO-19 Port PnL | H2a Port PnL (Δ vs H0) | **H2b Port PnL (Δ vs H0)** | **H2b Marginal vs LOO-19** | H2b M2M DD (vs H0 / LOO-19) |
|---|---|---:|---:|---:|---:|---:|---|
| `ALL_SIGNALS` | `IDEAL` | +\$658.82 | +\$639.75 | +\$640.32 (-\$18.50) | **+\$665.31 (+\$6.50)** | **+\$25.56** | Closed \$76.76 (vs \$79.06 / \$72.23) |
| `ALL_SIGNALS` | `BASE` | +\$407.22 | +\$399.81 | +\$396.35 (-\$10.86) | **+\$415.41 (+\$8.19)** | **+\$15.60** | Closed \$88.82 (vs \$91.18 / \$83.14) |
| `ALL_SIGNALS` | `STRESS` | +\$7.67 | +\$20.41 | +\$8.26 (+\$0.59) | **+\$18.41 (+\$10.74)** | **-\$2.00** | Closed \$134.43 (vs \$139.17 / \$138.04) |
| `ONE_TRADE_PER_COMBO` | `IDEAL` | +\$516.36 | +\$499.09 | +\$499.73 (-\$16.63) | **+\$525.09 (+\$8.73)** | **+\$26.00** | Closed \$51.46 (vs \$53.76 / \$46.06) |
| `ONE_TRADE_PER_COMBO` | `BASE` | +\$338.01 | +\$331.96 | +\$328.85 (-\$9.16) | **+\$348.22 (+\$10.21)** | **+\$16.26** | Closed \$60.28 (vs \$62.64 / \$54.86) |
| `ONE_TRADE_PER_COMBO` | `STRESS` | +\$57.25 | +\$68.62 | +\$57.46 (+\$0.21) | **+\$66.80 (+\$9.54)** | **-\$1.82** | Closed \$80.97 (vs \$80.77 / \$75.99) |
| `BINGX_PORTFOLIO` | `IDEAL` | +\$516.36 | +\$499.09 | +\$499.73 (-\$16.63) | **+\$525.09 (+\$8.73)** | **+\$26.00** | **\$54.02** (vs \$56.74 / \$48.62) |
| `BINGX_PORTFOLIO` | `BASE` | +\$338.01 | +\$331.96 | +\$328.85 (-\$9.16) | **+\$348.22 (+\$10.21)** | **+\$16.26** | **\$62.92** (vs \$65.72 / \$57.24) |
| `BINGX_PORTFOLIO` | `STRESS` | +\$57.25 | +\$68.62 | +\$57.46 (+\$0.21) | **+\$66.80 (+\$9.54)** | **-\$1.82** | **\$81.81** (vs \$81.94 / \$76.36) |

### Tác động lên số lượng lệnh của C14 (`ONE_TRADE_PER_COMBO` / `BINGX_PORTFOLIO`)
- **H2a:** Loại `115` tín hiệu, còn `187` lệnh (`IDEAL`/`BASE`, nhận mới `3` lệnh do thay đổi concurrency) và `190` lệnh (`STRESS`, nhận mới `2` lệnh).
- **H2b:** Loại `85` tín hiệu, còn `220` lệnh (`IDEAL`/`BASE`, nhận mới `5` lệnh do thay đổi concurrency) và `221` lệnh (`STRESS`, nhận mới `4` lệnh). Đạt mục tiêu `>= 120` lệnh/3 năm.

---

## 2. Đánh giá Chi tiết `H2a` (`REJECT`)

- **Own Entry PnL C14:** `+$0.6363` (`IDEAL`), `-$3.1023` (`BASE`), `-$11.1595` (`STRESS`).
- **Tác động danh mục:** Giảm Net PnL danh mục ở BASE (`-$9.1559` so với H0) và IDEAL (`-$16.6303` so với H0), kéo C14 xuống âm ngay cả ở BASE (`-$3.1023` so với LOO-19).
- **Nguyên nhân:** Như phân tích ở Nhiệm vụ B, các lệnh có `abs(Close - Kijun30) / ATR14 > 1.5` thực chất là các lệnh breakout có động lượng tốt (Win Rate `32.73%`), việc cắt bỏ chúng làm mất phần lợi nhuận chính của C14.
- **Kết luận:** **`REJECT H2a`**.

---

## 3. Đánh giá Chi tiết `H2b` (Kumo Thickness `>= 0.5%`) & Kiểm soát Overfitting

> [!NOTE]
> Dữ liệu 3 năm (`2023-11` – `2026-10`) đã được dùng để hình thành giả thuyết. Do đó các lát cắt dưới đây là **historical robustness / research OOS**, chưa phải kiểm chứng prospective độc lập.

### A. Hiệu suất theo Hướng (BUY vs SELL — `ONE_TRADE_PER_COMBO__BASE`)
- **BUY:** `122` lệnh, Win Rate `32.79%`, Net PnL **`+$10.6768`** (PF `1.1656`, H0: `+$1.3338`).
- **SELL:** `98` lệnh, Win Rate `31.63%`, Net PnL **`+$5.5845`** (PF `1.1059`, H0: `+$4.7198`).
- Ở `STRESS`: BUY đạt `+$0.8671` (PF `1.0125`), SELL đạt `-$2.6904` (PF `0.9523`).

### B. Kiểm soát Overfitting — Chia giai đoạn 24m / 6m / 6m (`ONE_TRADE_PER_COMBO`)
| Giai đoạn | H0 BASE (Trades / WR / PnL) | **H2b BASE (Trades / WR / PnL / PF)** | **H2b STRESS (PnL / PF)** |
|---|---|---|---|
| `W1 (24M: 2023-11 → 2025-10)` | 198 / 31.31% / +\$9.5737 | 145 / 31.72% / **+\$8.5980** (PF 1.1104) | -\$7.2351 (PF 0.9148) |
| `W2 (6M: 2025-11 → 2026-04)` | 49 / 32.65% / +\$4.1204 | 43 / 37.21% / **+\$8.8385** (PF 1.4160) | **+\$7.5477** (PF 1.3422) |
| `W3 (6M: 2026-05 → 2026-10)` | 47 / 23.40% / -\$7.6404 | 32 / 28.12% / **-\$1.1752** (PF 0.9350) | -\$2.1360 (PF 0.8863) |

### C. Hiệu suất theo Năm (`ONE_TRADE_PER_COMBO__BASE`)
- `2023 (Nov–Dec)`: 3 lệnh, 0 TP, `-$2.3595` (H0: `+$3.2819`).
- `2024`: 90 lệnh, 30.00% WR, **`+$1.1957`** (PF `1.0241`, H0: `-$1.9933`).
- `2025`: 71 lệnh, 36.62% WR, **`+$13.4832`** (PF `1.3809`, H0: `+$8.8607`).
- `2026 (Jan–Oct)`: 56 lệnh, 32.14% WR, **`+$3.9420`** (PF `1.1319`, H0: `-$4.0956`).

### D. Đối chiếu 6 Tiêu chuẩn Ưu tiên cho `H2b`
1. **Không có look-ahead:** **ĐẠT** (kiểm chứng bởi [test_c14_ichimoku_atr_causality.py](file:///d:/AutoTrade_c14/autotrade_v3/tests/test_c14_ichimoku_atr_causality.py)).
2. **Cải thiện BASE sau phí:** **ĐẠT RÕ RỆT** — Own Entry PnL tăng từ `+$6.0536` lên **`+$16.2613`** (`+$10.2077` toàn danh mục; `+$8.1949` ở `ALL_SIGNALS`).
3. **STRESS không âm hoặc cải thiện đáng kể:** **CHỈ ĐẠT MỘT PHẦN** — Cải thiện mạnh `+$9.5425` so với H0 (`-$11.3659 → -$1.8234`), nhưng **vẫn âm nhẹ `-$1.8234`** và kém `LOO-19` (`+$68.6206` vs `+$66.7973`).
4. **Không làm xấu đi Max Drawdown toàn danh mục:** **ĐẠT so với H0** (M2M DD `BASE` giảm từ `\$65.72` xuống `\$62.92`), **nhưng vẫn cao hơn `LOO-19` (`\$57.24`)**.
5. **Số lượng giao dịch đủ ý nghĩa (`>= 120`/3 năm):** **ĐẠT** (`220` lệnh `ONE_TRADE_PER_COMBO`, `231` lệnh `ALL_SIGNALS`).
6. **Không chỉ tập trung trong 1 năm:** **ĐẠT** (dương ở cả 3 năm đầy đủ 2024, 2025, 2026 trên `BASE`).

### Phân loại `H2b`:
- **So với H0 (giữ C14):** **`ACCEPT` (vượt trội H0 trên cả 9 cấu hình)**.
- **So với LOO-19 (loại bỏ C14):** **`INCONCLUSIVE` / Cân nhắc tùy khẩu vị rủi ro** — `H2b` thắng LOO-19 về lợi nhuận ở `IDEAL` (`+$26.00`) và `BASE` (`+$16.26`), nhưng vẫn thua LOO-19 ở `STRESS` (`-$1.82`) và có M2M DD cao hơn LOO-19 (`$62.92` vs `$57.24` ở BASE).
