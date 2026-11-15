# EXECUTIVE SUMMARY — `BTC-USDT/C14`: LOO-19 & NGHIÊN CỨU ICHIMOKU/ATR (`FIXED_TP_SL`)

**Phạm vi:** Local Lab Only (`feat/v3-c14-loo-h2`, không triển khai live).  
**Baseline:** `20FINAL_FIXED_V1@1` (`v3.16-fixed-tpsl`, BTCUSDT & ETHUSDT M1 `2023-11-01`–`2026-11-01`).

---

## 1. Bảng Phân loại Phương án

| Phương án | Phân loại | Tóm tắt `BINGX_PORTFOLIO` (IDEAL / BASE / STRESS) |
|---|---|---|
| **H0 (Giữ C14 nguyên bản)** | `BASELINE` | `+$516.36` / `+$338.01` / `+$57.25` (M2M DD BASE: `\$65.72`) |
| **LOO-19 (`C14_DISABLED`)** | **`ACCEPT` (nếu ưu tiên STRESS & DD)** | `+$499.09` / `+$331.96` / **`+$68.62`** (M2M DD BASE: **`\$57.24`**) |
| **H2a (`Overextension <= 1.5`)** | **`REJECT`** | `+$499.73` / `+$328.85` / `+$57.46` (làm C14 âm ở BASE `-$3.10`) |
| **H2b (`Kumo >= 0.5%`)** | **`ACCEPT` vs H0** / **`INCONCLUSIVE` vs LOO-19** | **`+$525.09`** / **`+$348.22`** / `+$66.80` (M2M DD BASE: `\$62.92`) |
| **H2ab (`H2a + H2b`)** | **`REJECT` (không chạy)** | Loại do `H2a` làm suy giảm rõ rệt IDEAL và BASE |

---

## 2. Trả lời 7 Câu hỏi Kết luận Bắt buộc

1. **C14 hiện tại có đóng góp tích cực hay tiêu cực cho danh mục thực tế theo từng profile?**
   - **Tích cực ở `IDEAL` (`+$17.2667`) và `BASE` (`+$6.0536`), tiêu cực ở `STRESS` (`-$11.3659`)** (theo `ONE_TRADE_PER_COMBO` / `BINGX_PORTFOLIO`).
   - Con số `VWAP Allocated PnL = -$17.1415` ở BASE **không phải** đóng góp biên thực tế; khi bỏ C14, phần VWAP của các combo BTC-USDT khác (`C1`, `TIER`, `C7`, `C4`...) giảm tổng cộng `-$23.1950`, khiến tổng danh mục giảm `-$6.0536`.

2. **Nếu bỏ C14 (LOO-19), tổng lợi nhuận và Drawdown có tốt hơn không?**
   - **Tổng lợi nhuận:** **Giảm** nhẹ ở `IDEAL` (`-$17.27`) và `BASE` (`-$6.05`), nhưng **tăng** ở `STRESS` (`+$11.37`: `+$57.25 → +$68.62`).
   - **Drawdown & Profit Factor:** **Tốt hơn rõ rệt trên cả 9 cấu hình** — M2M Max DD `BASE` giảm từ `\$65.72 (4.70%)` xuống `\$57.24 (4.13%)`, và Profit Factor tăng từ `1.1514` lên `1.1590`.

3. **Nguyên nhân nào khiến C14 yếu, và bằng chứng hỗ trợ mạnh đến đâu?**
   - **Nguyên nhân chính (bằng chứng mạnh trên dữ liệu lịch sử):** Tín hiệu Ichimoku TK-cross trong vùng **mây mỏng `< 0.5%`** (79/294 lệnh BASE) có Win Rate chỉ `24.05%`, lỗ `-$11.4758` và **âm nhất quán ở cả 3 năm đầy đủ** (`2024: -$4.76`, `2025: -$6.20`, `2026: -$6.16`). Trong khi đó `41.5%` lệnh SL (85/205) từng đi đúng hướng `>= +0.5%` trước khi quay đầu chạm SL `0.667%`.
   - **Bác bỏ giả thuyết "vào muộn do xa Kijun":** Nhóm `abs(Close - Kijun30) / ATR14 > 1.5` (110 lệnh) thực tế có Win Rate cao hơn (`32.73%`, `+$9.4640`) so với nhóm `<= 1.5` (`28.80%`, `-$3.4104`).

4. **H2a, H2b hoặc H2ab có cải thiện hơn H0 không?**
   - **`H2a`: KHÔNG (`REJECT`)** — giảm Net PnL danh mục `-$9.16` (`BASE`) và `-$16.63` (`IDEAL`).
   - **`H2b`: CÓ (`ACCEPT` so với H0)** — cải thiện toàn bộ 9 cấu hình: `IDEAL +$8.73`, `BASE +$10.21` (Own PnL C14 tăng từ `+$6.05` lên `+$16.26`), `STRESS +$9.54` (`-$11.37 → -$1.82`), giảm M2M DD BASE (`\$65.72 → \$62.92`), giữ `220` lệnh (`>= 120`) và dương ở cả 3 năm `2024/2025/2026` (`BASE`).
   - **`H2ab`: KHÔNG CHẠY** do `H2a` thất bại.

5. **Biến thể tốt nhất (`H2b`) có vượt LOO-19 không?**
   - **Có ở `IDEAL` (`+$26.00`) và `BASE` (`+$16.26`), nhưng CHƯA vượt ở `STRESS` (`-$1.82` vs LOO-19) và chưa vượt về M2M Drawdown (`\$62.92` vs `\$57.24` ở BASE).**

6. **Kết quả có bền vững qua thời gian và mức trượt giá STRESS không?**
   - **Qua thời gian (`BASE`):** Tương đối bền vững trên historical robustness splits — `H2b` dương ở cả `W1 (24M: +$8.60)` và `W2 (6M: +$8.84)`, cắt lỗ 85% ở `W3 (6M: -$1.18 vs -$7.64 H0)`, và dương cả 3 năm `2024 (+$1.20)`, `2025 (+$13.48)`, `2026 (+$3.94)`.
   - **Qua trượt giá `STRESS` (5 bps/chiều):** **Chưa bền vững hoàn toàn** — `H2b` vẫn âm nhẹ `-$1.8234` (`W1` STRESS `-$7.24`, `2024` STRESS `-$6.83`), dù đã giảm 84% mức lỗ so với H0 (`-$11.37`).

7. **Có đủ cơ sở để đề xuất nghiên cứu triển khai live không?**
   - **Chưa đề xuất triển khai live ngay** với `H2b` vì: (a) `H2b` vẫn âm nhẹ ở `STRESS` (`-$1.82`) và kém LOO-19 ở `STRESS` & Drawdown; (b) Live parity của `20FINAL_FIXED_V1` vẫn đang ở trạng thái `PARITY_PENDING_LIVE_VERIFICATION`; (c) Các phân tách 24M/6M/6M là historical robustness trên tập dữ liệu đã dùng để kiểm tra giả thuyết, chưa có kiểm chứng prospective độc lập.
   - **Khuyến nghị nghiên cứu tiếp theo (Lab only):** Nếu giữ C14 trong danh mục `BASE`, `H2b (Kumo >= 0.5%)` là ứng viên duy nhất đáng tiếp tục theo dõi shadow/prospective; ngược lại, nếu ưu tiên tối ưu Drawdown và khả năng chịu trượt giá `STRESS`, `LOO-19` (`C14_DISABLED`) cho tỷ lệ lợi nhuận/rủi ro sạch hơn.
