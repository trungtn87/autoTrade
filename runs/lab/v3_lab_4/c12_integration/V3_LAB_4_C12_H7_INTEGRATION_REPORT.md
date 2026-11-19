# Báo cáo Tích hợp `ETH-USDT/C12 H7_SELL_4H_TREND_ONLY` vào `V3_LAB_4@4.0.0`

## 1. Cấu hình `V3_LAB_4@4.0.0` (30 combo-symbol, 6 mã nâng cấp)
- **6 mã nâng cấp đã chốt**:
  1. `ETH-USDT/C1` (`combo_id=1`): `H1_BOTH_1`
  2. `BTC-USDT/C14` (`combo_id=104`): `BTC-USDT_C14_H3a_KUMO_BAND_V1`
  3. `ETH-USDT/C15` (`combo_id=105`): `H3_ASYMMETRIC`
  4. `ETH-USDT/C10` (`combo_id=10`): `H3_ASYMMETRIC`
  5. `ETH-USDT/C11` (`combo_id=101`): `H3_B1_DEMARKER`
  6. **`ETH-USDT/C12` (`combo_id=102`)**: **`H7_SELL_4H_TREND_ONLY`** (`Aroon(40) >= 70`, `SMC_VETO_OB`, SELL bổ sung `close_4h < ema100_4h` trên nến 4H đã đóng, Fixed `TP 1.8% / SL 0.9%`, không trailing).
- **24 mã còn lại**: `BTC-USDT/C10`, `BTC-USDT/C12` và 22 mã khác giữ nguyên `20FINAL_FIXED_V1@1 H0`.

## 2. Khóa Phụ thuộc & Hash
- **Git Branch**: `lab/v3-lab-4-eth-c11-r1`
- **Integration Commit SHA**: `f765a80ddd720deef4b1aa45646875540afe70c3`
- **Config SHA-256 (`v3_lab_4_config.json`)**: `f4ef7b0d3991bcf980eed3304ea2128289a383a647209a64de03a4d1c145a8de`
- **Signal Spec Hash**: `626a0b1195e8329d93c5e5b9ea3c8903e98c61cfaebb13ccd6be0cd8b631810e`
- **Spec Hash**: `95a324e2436ab29f740414827a8448e1e2f72ba90cc505137a0cf91e90625189`
- **Dataset SHA-256**: `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`
- **Khóa phụ thuộc**: Toàn bộ source/config dependency trước đây `untracked` (`c14_h3a_kumo_band.py`, `v3_lab_2.py`, `v3_lab_2_config.json`, `v3_lab_2_loader.py`, `20final_fixed_v1_v1/`, `p3_c1_research_strategy.py`, `c1_sell_corrected_strategy.py`, `fixed_tpsl_strategy.py`, `run_v3_lab_2_eth_c15_*.py`) đã được đưa vào Git. Dữ liệu lớn và cache (`data/`, `catalog/`) vẫn giữ ngoài Git qua `.gitignore`.

## 3. Kiểm chứng Sau Tích hợp trên Clean Git Clone (`f765a80`)
- **Clone sạch độc lập**: `git clone --no-hardlinks --branch lab/v3-lab-4-eth-c11-r1` — **không sao chép bất kỳ file source `untracked` nào** từ workspace gốc.
- **Unit & Regression Tests**: `13/13 PASSED` (`test_v3_lab_4_c10_integration.py`, `test_v3_lab_4_c11_integration.py`, `test_v3_lab_4_c12_integration.py`, `test_eth_c12_h7_no_lookahead.py`).
- **Tái lập `H7` & Parity từng lệnh (`verify_v3_lab_4_c12_parity.py`)**: **`ALL 732/732 TRADE-BY-TRADE PARITY CHECKS PASSED`** (`366` BASE + `366` STRESS khớp 100% với `V3_LAB_4_ETH_C12_ROUND3.zip`):

| Mode | Profile | Port30 Trades | Port30 Net PnL | Port30 M2M MaxDD | ETH/C12 Trades (B/S) | ETH/C12 Own PnL | ETH/C12 PF | ETH/C12 MaxDD |
|---|---|---:|---:|---:|---|---:|---:|---:|
| `ONE_TRADE_PER_COMBO` | `BASE` | 5,932 | +$235.9798 | — | 366 (243 / 123) | **+$34.3680** | **1.1568** | **$13.9818** |
| `ONE_TRADE_PER_COMBO` | `STRESS` | 5,954 | -$165.7878 | — | 366 (243 / 123) | **+$15.2814** | **1.0668** | **$18.4619** |
| `BINGX_PORTFOLIO` | `BASE` | 5,932 | +$235.9798 | $91.1139 | 366 (243 / 123) | **+$34.3680** | **1.1568** | **$13.9818** |
| `BINGX_PORTFOLIO` | `STRESS` | 5,954 | -$165.7878 | $170.5243 | 366 (243 / 123) | **+$15.2814** | **1.0668** | **$18.4619** |

- **29 mã còn lại**: `BTC/C12`, `ETH/C11` (`+$33.9645` BASE / `+$18.1548` STRESS), `BTC/C11` (`+$24.1902` BASE / `+$8.4096` STRESS) và các mã khác không đổi.
- **Trạng thái Live**: `live_eligible: false` (chưa merge `main`, chưa deploy Live).

## 4. Kết luận
**`INTEGRATED_VERIFIED`**
