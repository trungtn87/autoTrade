# NHIỆM VỤ A — BÁO CÁO LEAVE-ONE-OUT (LOO-19: `C14_DISABLED`)

**Chiến lược gốc:** `20FINAL_FIXED_V1@1` (20 combos)  
**Biến thể LOO-19:** Loại bỏ duy nhất `BTC-USDT/C14` (`combo_id=104`), giữ nguyên 19 combos còn lại, TP/SL, phí, trượt giá, khối lượng và quy tắc thực thi.  
**Dữ liệu:** BTCUSDT & ETHUSDT M1 (`2023-11-01` – `2026-11-01`, hash `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`).  
**Bảng đối chiếu chi tiết:** [LOO_19_COMPARISON.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/LOO_19_COMPARISON.csv), [loo_19_trade_reconciliation_ledger.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/loo_19_trade_reconciliation_ledger.csv), [loo_19_combo_vwap_shift.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/loo_19_combo_vwap_shift.csv).

---

## 1. Bảng Đối chiếu 9 Cấu hình (Baseline 20 vs LOO-19)

| Mode | Profile | Base Entries (TP/SL) | LOO Entries (TP/SL) | Base Net PnL | LOO-19 Net PnL | **Marginal C14 (`20 - 19`)** | Base PF → LOO PF | Base Closed DD → LOO Closed DD | Base M2M DD → LOO M2M DD |
|---|---|---:|---:|---:|---:|---:|---|---|---|
| `ALL_SIGNALS` | `IDEAL` | 5,984 (2,299/3,685) | 5,668 (2,201/3,467) | +\$658.8155 | +\$639.7515 | **+\$19.0640** | 1.1972 → 1.2016 | \$79.06 → \$72.23 | — |
| `ALL_SIGNALS` | `BASE` | 5,984 (2,249/3,735) | 5,668 (2,153/3,515) | +\$407.2189 | +\$399.8090 | **+\$7.4099** | 1.1177 → 1.1217 | \$91.18 → \$83.14 | — |
| `ALL_SIGNALS` | `STRESS` | 5,984 (2,166/3,818) | 5,668 (2,074/3,594) | +\$7.6693 | +\$20.4072 | **-\$12.7379** | 1.0021 → 1.0059 | \$139.17 → \$138.04 | — |
| `ONE_TRADE_PER_COMBO` | `IDEAL` | 4,150 (1,626/2,524) | 3,856 (1,535/2,321) | +\$516.3583 | +\$499.0916 | **+\$17.2667** | 1.2278 → 1.2364 | \$53.76 → \$46.06 | — |
| `ONE_TRADE_PER_COMBO` | `BASE` | 4,157 (1,591/2,566) | 3,863 (1,502/2,361) | +\$338.0100 | +\$331.9564 | **+\$6.0536** | 1.1435 → 1.1513 | \$62.64 → \$54.86 | — |
| `ONE_TRADE_PER_COMBO` | `STRESS` | 4,168 (1,535/2,633) | 3,870 (1,448/2,422) | +\$57.2547 | +\$68.6206 | **-\$11.3659** | 1.0229 → 1.0295 | \$80.77 → \$75.99 | — |
| `BINGX_PORTFOLIO` | `IDEAL` | 4,150 (1,626/2,524) | 3,856 (1,535/2,321) | +\$516.3583 | +\$499.0916 | **+\$17.2667** | 1.2406 → 1.2489 | \$53.85 → \$46.15 | \$56.74 (3.65%) → \$48.62 (3.20%) |
| `BINGX_PORTFOLIO` | `BASE` | 4,157 (1,591/2,566) | 3,863 (1,502/2,361) | +\$338.0100 | +\$331.9564 | **+\$6.0536** | 1.1514 → 1.1590 | \$62.64 → \$54.86 | \$65.72 (4.70%) → \$57.24 (4.13%) |
| `BINGX_PORTFOLIO` | `STRESS` | 4,168 (1,535/2,633) | 3,870 (1,448/2,422) | +\$57.2547 | +\$68.6206 | **-\$11.3659** | 1.0242 → 1.0310 | \$80.77 → \$75.99 | \$81.94 (7.61%) → \$76.36 (6.89%) |

### Chi phí Phí giao dịch & Trượt giá (Baseline → LOO-19)
- `ALL_SIGNALS`: Fees `\$598.42 → \$566.82` (giảm `\$31.60`); Slippage BASE `\$239.36 → \$226.72`, STRESS `\$598.40 → \$566.80`.
- `ONE_TRADE_PER_COMBO` / `BINGX_PORTFOLIO`:
  - `IDEAL`: Fees `\$415.07 → \$385.67` (giảm `\$29.40`).
  - `BASE`: Fees `\$415.76 → \$386.36` (giảm `\$29.40`), Slippage `\$166.30 → \$154.54` (giảm `\$11.76`).
  - `STRESS`: Fees `\$416.86 → \$387.07` (giảm `\$29.80`), Slippage `\$416.86 → \$387.07` (giảm `\$29.80`).

---

## 2. Phân biệt Rõ 3 Khái niệm PnL của C14 (`ONE_TRADE_PER_COMBO` & `BINGX_PORTFOLIO`)

| Profile | Own Entry PnL (`ONE_TRADE_PER_COMBO`) | VWAP Allocated PnL (`BINGX_PORTFOLIO`) | **Marginal Portfolio Contribution (`20 - 19`)** |
|---|---:|---:|---:|
| `IDEAL` | **+\$17.2667** | -\$4.8136 | **+\$17.2667** |
| `BASE` | **+\$6.0536** | -\$17.1415 | **+\$6.0536** |
| `STRESS` | **-\$11.3659** | -\$33.3082 | **-\$11.3659** |

**Kết luận quan trọng:** `VWAP Allocated PnL = -$17.1415` ở BASE KHÔNG phải là đóng góp biên thực tế của C14. Đóng góp biên thực tế của C14 vào tổng lợi nhuận danh mục là **`+$6.0536` (BASE)** và **`+$17.2667` (IDEAL)** — tức nếu tắt C14 ở BASE hoặc IDEAL, tổng lợi nhuận danh mục **GIẢM** chứ không tăng. Chỉ ở `STRESS` (trượt giá 5 bps/chiều), C14 mới có đóng góp biên âm **`-$11.3659`**.

---

## 3. Kiểm toán Chuỗi Giao dịch 19 Combo Còn lại

Đối chiếu toàn bộ `signal_decisions` và `trades` của 19 combo còn lại giữa Baseline 20 và LOO-19:

1. **Tín hiệu của 19 combo có thay đổi không?** **Không (`0` khác biệt).**
2. **Có lệnh nào của 19 combo được nhận hoặc bỏ qua khác đi không?** **`diff_decs = 0` trên cả 9 cấu hình.** Không có lệnh nào bị thay đổi do concurrency, margin (`rejected_margin = 0` ở cả hai), hoặc equity.
3. **Ở `ALL_SIGNALS` và `ONE_TRADE_PER_COMBO`:** `diff_trades = 0` trên cả 6 cấu hình — mọi `entry_time_ms`, `exit_time_ms` và `net_pnl` của 19 combo trùng khớp tuyệt đối.
4. **Ở `BINGX_PORTFOLIO` (VWAP pooling):** Có `255` lệnh (`IDEAL`) và `258` lệnh (`BASE`/`STRESS`) trên `BTC-USDT` thay đổi giá trị `net_pnl` (`pnl_vwap`) khi không còn gộp vị thế với C14, nhưng **0 lệnh thay đổi thời điểm Entry/Exit**.
   - Ở `BASE`, khi bỏ C14 (`-$17.1414` VWAP), các combo BTC-USDT khác đều **giảm** VWAP Allocated PnL tổng cộng `-$23.1950`: `C1` (`-$6.9413`), `TIER` (`-$5.2808`), `C7` (`-$4.0245`), `C4` (`-$2.2365`), `C16` (`-$1.6524`), `C10` (`-$1.2679`), `C2` (`-$1.1094`), `C6` (`-$0.4222`), `C9` (`-$0.2439`), `C11` (`-$0.0162`). Tổng hợp: `+17.1414 - 23.1950 = -$6.0536` (chính xác bằng `-Own_Entry_PnL(C14)`).
   - `ETH-USDT` (9 combos, 2,295 lệnh BASE): `diff_net_pnl = $0.0000` trên tất cả các cấu hình.

---

## 4. Tác động lên Drawdown & Chất lượng Danh mục

Dù C14 có Marginal PnL dương nhẹ ở IDEAL (`+$17.27`) và BASE (`+$6.05`), C14 làm **xấu đi** hiệu suất điều chỉnh rủi ro của danh mục:
- **Max Drawdown (M2M, `BINGX_PORTFOLIO`):**
  - `IDEAL`: tăng từ `\$48.62 (3.20%)` lên `\$56.74 (3.65%)` (`+\$8.12`).
  - `BASE`: tăng từ `\$57.24 (4.13%)` lên `\$65.72 (4.70%)` (`+\$8.48`).
  - `STRESS`: tăng từ `\$76.36 (6.89%)` lên `\$81.94 (7.61%)` (`+\$5.58`).
- **Profit Factor (`BINGX_PORTFOLIO`):** giảm ở cả 3 profile khi có C14 (`1.2489 → 1.2406` IDEAL, `1.1590 → 1.1514` BASE, `1.0310 → 1.0242` STRESS).
- **Lý do:** Với 294–298 lệnh, Win Rate chỉ ~30% (1H fixed 2%/0.667%) và chi phí phí+trượt giá cao (`$41.16` ở BASE, `$59.60` ở STRESS), C14 đóng góp biên rất mỏng ở BASE (`+$0.0206`/lệnh) nhưng thêm `+$8.48` M2M Drawdown cho danh mục.
