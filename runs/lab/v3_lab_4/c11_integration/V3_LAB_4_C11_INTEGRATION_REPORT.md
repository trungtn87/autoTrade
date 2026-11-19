# Báo cáo Tích hợp `ETH-USDT/C11 H3_B1_DEMARKER` vào `V3_LAB_4@4.0.0`

## 1. Cấu hình `V3_LAB_4@4.0.0` (30 combo-symbol, 5 mã nâng cấp)

- **5 mã nâng cấp đã chốt**:
  1. `ETH-USDT/C1` (`combo_id=1`): `H1_BOTH_1`
  2. `BTC-USDT/C14` (`combo_id=104`): `BTC-USDT_C14_H3a_KUMO_BAND_V1`
  3. `ETH-USDT/C15` (`combo_id=105`): `H3_ASYMMETRIC`
  4. `ETH-USDT/C10` (`combo_id=10`): `H3_ASYMMETRIC`
  5. **`ETH-USDT/C11` (`combo_id=101`)**: **`H3_B1_DEMARKER`** (`DeMarker(60) BUY > 0.78 / SELL < 0.20`, `SMC_STRICT_OB`, Fixed `TP 1.8% / SL 0.9%`, không trailing)
- **25 mã còn lại**: `BTC-USDT/C10` (`H0`) và 24 mã khác (gồm `BTC-USDT/C11`) giữ nguyên `20FINAL_FIXED_V1@1`.

## 2. Trạng thái nghiên cứu & Provenance
- **Owner approved for Lab**: `true` (`research_status: RESEARCH_ACCEPTED`)
- **`live_eligible`**: `false` — chưa phê duyệt Live.
- **Baseline `H0`**: `BASE +$10.3875` / `STRESS -$17.8424` (`461` / `467` lệnh).
- **Upgraded `B1`**: `BASE +$33.9645` / `STRESS +$18.1548` (`242` / `245` lệnh).
- **Lưu ý**: Kết quả là backtest lịch sử (`2023-11-01` → `2026-11-01`); chưa chứng minh hiệu quả ngoài mẫu độc lập.
- **Provenance**: Round 1 (`V3_LAB_4_ETH_C11_ROUND1.zip`) & Round 2 (`V3_LAB_4_ETH_C11_ROUND2.zip`, commit `1d5bf09`, `ETH_C11_R2_REPORT.md`).

## 3. Kiểm chứng tái lập từng lệnh (`PASSED_EXACT_TRADE_BY_TRADE`)

| Mode | Profile | Port30 Trades | Port30 Net PnL | Port30 M2M MaxDD | ETH/C11 Trades (B/S) | ETH/C11 Own PnL | ETH/C11 PF | ETH/C11 MaxDD | BTC/C11 Own PnL |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|
| `ONE_TRADE_PER_COMBO` | `BASE` | 6,018 | +$221.1557 | — | 242 (92 / 150) | **+$33.9645** | **1.2413** | **$12.0053** | +$24.1902 |
| `ONE_TRADE_PER_COMBO` | `STRESS` | 6,040 | -$183.1944 | — | 245 (93 / 152) | **+$18.1548** | **1.1209** | **$13.8087** | +$8.4096 |
| `BINGX_PORTFOLIO` | `BASE` | 6,018 | +$221.1557 | $89.4921 | 242 (92 / 150) | **+$33.9645** | **1.2413** | **$12.0053** | +$24.1902 |
| `BINGX_PORTFOLIO` | `STRESS` | 6,040 | -$183.1944 | $186.8800 | 245 (93 / 152) | **+$18.1548** | **1.1209** | **$13.8087** | +$8.4096 |

- Khớp **100%** từng `signal_id`, `entry_time_ms`, `entry_price`, `exit_time_ms`, `exit_price`, `exit_reason`, `pnl_own_entry` với Round 2 `B1`.
- 29 mã còn lại (`BTC/C11`, `ETH/C10`, `BTC/C10`, v.v.) không có bất kỳ thay đổi ngoài ý muốn nào.
