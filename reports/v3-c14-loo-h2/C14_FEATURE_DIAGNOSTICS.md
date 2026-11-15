# NHIỆM VỤ B — PHÂN TÍCH NGUYÊN NHÂN THUA LỖ `BTC-USDT/C14`

**Phạm vi dữ liệu:** 294 giao dịch `BTC-USDT/C14` trong chế độ `ONE_TRADE_PER_COMBO` / `BASE` (`RUN_20261115_042341_20FINAL_FIXED_V1_ONE_TRADE_PER_COMBO_20231101_20261101`, Own Entry PnL = `+$6.0536`, 89 TP / 205 SL, Win Rate `30.27%`).  
**Kiểm chứng Zero Look-Ahead:** Toàn bộ feature Entry (Tenkan 12, Kijun 30, Span A/B shift +30, ATR(14) 1H) được tái dựng chỉ từ nến 1H đã đóng tại `signal_close_ms`, đã được kiểm chứng bằng unit test [test_c14_ichimoku_atr_causality.py](file:///d:/AutoTrade_c14/autotrade_v3/tests/test_c14_ichimoku_atr_causality.py) (`3 passed`).  
**Dữ liệu chi tiết:** [c14_feature_diagnostics_trades.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/c14_feature_diagnostics_trades.csv), [c14_feature_diagnostics_summary.json](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/c14_feature_diagnostics_summary.json).

---

## 1. So sánh Entry Thắng (TP = 89) vs Thua (SL = 205)

| Feature (tại thời điểm đóng nến 1H) | Winners Mean (Median) | Losers Mean (Median) | Nhận xét mô tả |
|---|---:|---:|---|
| `abs(Close - Kijun30) / ATR14` | 1.3071 (1.1970) | 1.2590 (1.2334) | Không có bằng chứng thắng thấp hơn thua; winners thậm chí có mean hơi cao hơn. |
| `abs(Close - Kumo) / Close` | 1.609% (1.261%) | 1.687% (1.121%) | Tương đương nhau giữa thắng và thua. |
| `Kumo Thickness / Close` | 1.128% (0.847%) | 1.161% (0.891%) | Mean gần như nhau, nhưng phân phối theo ngưỡng cho thấy đuôi mây quá mỏng `< 0.5%` thua nặng. |
| `ATR14 / Close` (1H) | 0.673% (0.639%) | 0.726% (0.655%) | Lệnh thua có biến động trung bình cao hơn nhẹ. |
| `abs(Tenkan - Kijun) / ATR14` | 0.2361 (0.2155) | 0.2468 (0.1644) | Tương đương. |
| Thời gian giữ lệnh (`bars_held`, phút) | 800.1 (548.0) | 433.4 (252.0) | Lệnh SL bị quét nhanh hơn đáng kể (~4.2h median vs ~9.1h median cho TP). |
| **MFE (M1, chẩn đoán sau Entry)** | 2.110% (2.062%) | 0.568% (0.382%) | 85/205 lệnh SL (41.5%) từng đi đúng hướng `>= +0.5%`; 42/205 (20.5%) từng đạt `>= +1.0%`; 17/205 (8.3%) từng đạt `>= +1.5%` trước khi quay đầu chạm SL `0.667%`. |
| **MAE (M1, chẩn đoán sau Entry)** | 0.324% (0.319%) | 0.743% (0.707%) | SL `0.667%` rất sát với nhiễu M1 thông thường. |

---

## 2. Phân tích theo Ngưỡng & Regime

### A. Theo Overextension `abs(Close - Kijun30) / ATR14`
| Bucket | Trades | TP | SL | Win Rate | Net PnL (BASE) | Avg PnL |
|---|---:|---:|---:|---:|---:|---:|
| `<= 0.75` | 76 | 23 | 53 | 30.26% | +\$1.5485 | +\$0.0204 |
| `0.75 – 1.50` | 108 | 30 | 78 | 27.78% | -\$4.9589 | -\$0.0459 |
| `1.50 – 2.50` | 92 | 29 | 63 | 31.52% | +\$4.9592 | +\$0.0539 |
| `> 2.50` | 18 | 7 | 11 | 38.89% | +\$4.5048 | +\$0.2503 |

### B. Theo Độ dày mây `abs(SpanA - SpanB) / Close`
| Bucket | Trades | TP | SL | Win Rate | Net PnL (BASE) | Avg PnL |
|---|---:|---:|---:|---:|---:|---:|
| `< 0.5%` | 79 | 19 | 60 | **24.05%** | **-\$11.4758** | -\$0.1453 |
| `0.5% – 1.0%` | 83 | 34 | 49 | **40.96%** | **+\$25.3794** | +\$0.3058 |
| `1.0% – 2.0%` | 82 | 22 | 60 | 26.83% | -\$5.8445 | -\$0.0713 |
| `> 2.0%` | 50 | 14 | 36 | 28.00% | -\$2.0055 | -\$0.0401 |

### C. Theo Volatility Regime (`ATR14 / Close` terciles)
| Regime | Trades | TP | SL | Win Rate | Net PnL (BASE) | Avg ATR% |
|---|---:|---:|---:|---:|---:|---:|
| `LOW_VOL` | 98 | 28 | 70 | 28.57% | -\$2.4089 | 0.432% |
| `MID_VOL` | 98 | 34 | 64 | **34.69%** | **+\$13.5661** | 0.652% |
| `HIGH_VOL` | 98 | 27 | 71 | 27.55% | -\$5.1036 | 1.046% |

### D. Theo Năm & Hướng (BUY / SELL)
| Năm | Side | Trades | TP | Win Rate | Net PnL (BASE) |
|---|---|---:|---:|---:|---:|
| 2023 (Nov–Dec) | BUY / SELL | 3 / 3 | 1 / 2 | 33.3% / 66.7% | +\$0.3062 / +\$2.9756 |
| 2024 | BUY / SELL | 72 / 39 | 22 / 10 | 30.6% / 25.6% | +\$2.0199 / -\$4.0133 |
| 2025 | BUY / SELL | 48 / 56 | 14 / 20 | 29.2% / 35.7% | -\$0.4299 / +\$9.2906 |
| 2026 (Jan–Oct) | BUY / SELL | 38 / 35 | 11 / 9 | 28.9% / 25.7% | -\$0.5624 / -\$3.5332 |

---

## 3. Trả lời 5 Câu hỏi Chẩn đoán (Phân biệt Mô tả vs Giả thuyết Nhân quả)

1. **C14 thua vì tín hiệu Ichimoku vào muộn, hay vì tín hiệu trong thị trường sideway?**
   - **Phát hiện mô tả:** Dữ liệu KHÔNG ủng hộ giả thuyết "vào muộn do giá đi quá xa Kijun": nhóm `dist_close_kijun_atr > 1.5` (110 lệnh) thực tế có Win Rate cao hơn (`32.73%`, Net PnL `+$9.4640`) so với nhóm `<= 1.5` (184 lệnh, Win Rate `28.80%`, Net PnL `-$3.4104`). Ngược lại, trong vùng mây mỏng `< 0.5%` (đặc trưng của giai đoạn tích lũy/sideway hẹp sau khi xu hướng yếu đi, mean ATR `0.611%`), Win Rate tụt xuống `24.05%` (`-$11.4758`), và trong regime `LOW_VOL` Win Rate chỉ `28.57%` (`-$2.4089`).
   - **Giả thuyết nhân quả:** Tín hiệu TK-cross trong vùng mây mỏng/sideway dễ bị nhiễu quét SL `0.667%` trước khi xu hướng hình thành; tuy nhiên đây mới là tương quan mô tả trên dữ liệu lịch sử, cần kiểm chứng bằng A/B test có kiểm soát.

2. **Khoảng cách tới Kijun/mây có liên hệ đáng kể với xác suất TP không?**
   - Với **khoảng cách tới Kijun (`dist_close_kijun_atr`)**: quan hệ là phi tuyến/nghịch nhẹ với giả thuyết H2a — các lệnh có `dist_close_kijun_atr > 1.5` lại đạt tỷ lệ TP cao hơn (`32.73%` vs `28.80%`). Mean của winners (`1.307`) và losers (`1.259`) gần như không khác biệt.
   - Với **độ dày mây (`kumo_thickness_pct`)**: có sự phân hóa rõ ở ngưỡng dưới `0.5%` (`24.05%` WR, `-$11.48`) so với `0.5%–1.0%` (`40.96%` WR, `+$25.38`), nhưng trên `1.0%` hiệu suất lại giảm về `26.8%–28.0%` (tức không đơn điệu tăng).

3. **ATR cao hoặc thấp có làm hiệu suất thay đổi không?**
   - Có dạng chữ U ngược: `MID_VOL` (`ATR% ~ 0.65%`) đạt hiệu suất tốt nhất (`34.69%` WR, `+$13.57`), trong khi cả `LOW_VOL` (`28.57%` WR, `-$2.41`) và `HIGH_VOL` (`27.55%` WR, `-$5.10`) đều âm. Giả thuyết hợp lý: khi `HIGH_VOL` (`ATR ~ 1.05%`), SL cố định `0.667%` nhỏ hơn 1 ATR nên dễ bị nhiễu trong 1 nến 1H; khi `LOW_VOL`, giá thiếu động lượng chạm TP `2.00%` (41.5% lệnh SL từng đạt MFE `>= 0.5%` rồi quay đầu).

4. **Tại sao năm 2024 và 2026 có hiệu suất kém hơn 2025?**
   - Năm **2024** (`-$1.99`): BUY dương (`+$2.02`, 72 lệnh) nhưng SELL thua nặng (`-$4.01`, 39 lệnh, WR `25.64%`) và các lệnh mây mỏng `< 0.5%` lỗ `-$4.76`.
   - Năm **2026** (`-$4.10`): Cả BUY (`-$0.56`) và SELL (`-$3.53`) đều yếu, đặc biệt biến động thấp (`avg ATR% = 0.621%`) và nhóm mây mỏng `< 0.5%` lỗ `-$6.16` (WR `16.67%` trên 18 lệnh).
   - Năm **2025** (`+$8.86`): SELL đạt hiệu suất rất tốt (`+$9.29`, 56 lệnh, WR `35.71%`), bù đắp cho nhóm mây mỏng (`-$6.20`). Đáng chú ý, ở cả 3 năm 2024, 2025, 2026, phần `kumo >= 0.5%` đều có Net PnL dương (`+$2.77`, `+$15.06`, `+$2.06`), còn phần `kumo < 0.5%` đều âm (`-$4.76`, `-$6.20`, `-$6.16`).

5. **BUY và SELL có cùng nguyên nhân thất bại không?**
   - Có điểm chung ở **mây mỏng `< 0.5%`**: cả BUY (`21.43%` WR, `-$9.04`) và SELL (`27.03%` WR, `-$2.44`) đều kém ở bucket `< 0.5%`, và cùng đạt đỉnh Win Rate ở `0.5%–1.0%` (BUY `37.50%`, SELL `44.19%`).
   - Điểm khác: SELL nhạy hơn với mây quá dày `> 2.0%` (`21.05%` WR, `-$4.28`) và có sự phân kỳ lớn theo chu kỳ thị trường (âm nặng ở 2024 & 2026, dương mạnh ở 2025).
