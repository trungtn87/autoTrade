# Báo cáo Tích hợp `V3_LAB_4@4.0.0` (`ETH-USDT/C10 H3_ASYMMETRIC` & `BTC-USDT/C10 H0`)

## 1. Cấu hình `V3_LAB_4@4.0.0` (30 combo-symbol, Fixed TP/SL)

- **4 mã nâng cấp đã chốt**:
  1. `ETH-USDT/C1` (`combo_id=1`): `H1_BOTH_1` (`TP 2.0% / SL 0.666667%`)
  2. `BTC-USDT/C14` (`combo_id=104`): `BTC-USDT_C14_H3a_KUMO_BAND_V1` (`TP 2.0% / SL 0.666667%`)
  3. `ETH-USDT/C15` (`combo_id=105`): `H3_ASYMMETRIC` (`BUY dist_kama_atr <= 2.0`; `SELL 60m cooldown sau TP`)
  4. `ETH-USDT/C10` (`combo_id=10`): `H3_ASYMMETRIC` (`BUY: ADX > 28 + SMC_VETO`; `SELL: ADX > 28 + SMC_STRICT`; `TP 1.8% / SL 0.9%`, không trailing)
- **26 mã còn lại**: Giữ nguyên `H0` từ `20FINAL_FIXED_V1@1` (gồm `BTC-USDT/C10`: `ADX > 22`, `SMC_VETO`, `TP 1.0% / SL 0.8%` và 25 mã còn lại).

## 2. Trạng thái nghiên cứu
- `ETH-USDT/C10 H3_ASYMMETRIC`: **`RESEARCH_ACCEPTED`**
- `sample_size`: **`23 trades / 3 years`** (`15 BUY + 8 SELL`)
- `live_eligible`: **`false`** — chưa đạt ngưỡng kiểm chứng `30` lệnh.
- `BTC-USDT/C10`: giữ nguyên **`H0`**.
- **Top 20 Live**: Không thay đổi. Không sửa `V2`, `V3 Live`, `20FINAL`, `Golden` hoặc `V3_LAB_2` gốc.

## 3. Kết quả kiểm chứng tái lập (`V3_LAB_4@4.0.0`, `2023-11-01` → `2026-11-01`)

| Mode | Profile | Port30 Trades | Port30 Net PnL | Port30 M2M MaxDD | ETH/C10 Trades (B/S) | ETH/C10 Own PnL | BTC/C10 Trades | BTC/C10 Own PnL |
|---|---|---:|---:|---:|---|---:|---:|---:|
| `ONE_TRADE_PER_COMBO` | `BASE` | 6,237 | +$197.5788 | — | 23 (15 / 8) | **+$14.3407** | 46 | **+$6.2829** |
| `ONE_TRADE_PER_COMBO` | `STRESS` | 6,262 | -$219.1916 | — | 23 (15 / 8) | **+$10.9484** | 46 | **+$1.3041** |
| `BINGX_PORTFOLIO` | `BASE` | 6,237 | +$197.5788 | $97.6132 | 23 (15 / 8) | **+$14.3407** | 46 | **+$6.2829** |
| `BINGX_PORTFOLIO` | `STRESS` | 6,262 | -$219.1916 | $222.8772 | 23 (15 / 8) | **+$10.9484** | 46 | **+$1.3041** |

- Khớp 100% với nghiên cứu đã chốt từ `scripts/run_v3_lab_2_eth_c10_h3_asymmetric.py` và `exports/V3_LAB_2_ETH_C10_H3_ASYMMETRIC.zip`.
- Không có bất kỳ thay đổi ngoài ý muốn nào trên 28 mã còn lại.
