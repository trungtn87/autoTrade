# BASELINE VERIFICATION — `20FINAL_FIXED_V1@1` & `BTC-USDT/C14`

**Ngày kiểm tra:** `2026-11-15`  
**Nhánh nghiên cứu:** `feat/v3-c14-loo-h2` (tạo từ `feat/v3-16-fixed-tpsl-backtest`)  
**Git SHA:** `c99e4529ac1761614ccfd1fdbed8a92315e7a5d7` (`code_dirty = False`)  
**Simulator:** `v3.16-fixed-tpsl`

---

## 1. Xác minh Strategy Spec & Exit Policy

- **Strategy:** `20FINAL_FIXED_V1@1`
- **Signal Spec Hash:** `4729dfeabea57994d1e955c9f8ce4716b4fbe118cbce2765fa8fddeeaf17da79`
- **Exit Spec Hash:** `451883a51503a3f4b3c2dd286f9e96246ddbc3732efc46cfe6a1e63ae998abcd`
- **Cấu hình `BTC-USDT/C14`:**
  - `combo_key`: `BTC-USDT/C14`
  - `symbol`: `BTC-USDT`
  - `combo_id`: `104`
  - `native_timeframe`: `1h`
  - `entry_variant`: `tkcross_12_30_60` (`Ichimoku`, `conv=12`, `base=30`, `spanb=60`)
  - `layer2`: `OB`
  - `exit_policy`: `tp_pct=0.02` (`2.00%`), `sl_pct=0.006666666666666667` (`0.666667%`), `split_legs=False`, `leg_weights=(1.0,)`, `trailing_act_pct=0.0`, `trailing_cb_pct=0.0`

---

## 2. Xác minh Dataset & Golden Hash

- **Giai đoạn đánh giá:** `2023-11-01` đến `2026-11-01` (BTCUSDT và ETHUSDT, nến M1)
- **Golden Dataset Hash (`runs/golden/v3-08/dataset_manifest.json`):**  
  `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`
- **DataLayer Manifest Hash:** `a18fc2e1bf5985945fee6609847cce753cd319da74415dc23d4f4bae231487be`
- **Ghi chú:** Tất cả 9 cấu hình baseline trong `reports/fixed_tpsl_v1/` đều khớp `expected_dataset_hash = ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628` (Gate `A1 = PASS`, `A2 = PASS`).

---

## 3. Xác minh 9 Cấu hình Baseline (`reports/fixed_tpsl_v1/comparison_portfolio.csv`)

| Mode | Cost Profile | Signals | Entries | Exits | Skipped | Rejected | Net PnL | ROI | Profit Factor | Closed DD | M2M DD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ALL_SIGNALS` | `IDEAL` | 5,984 | 5,984 | 5,984 | 0 | 0 | +\$658.8155 | 65.88% | 1.1972 | \$79.0603 (4.79%) | — |
| `ALL_SIGNALS` | `BASE` | 5,984 | 5,984 | 5,984 | 0 | 0 | +\$407.2189 | 40.72% | 1.1177 | \$91.1777 (6.35%) | — |
| `ALL_SIGNALS` | `STRESS` | 5,984 | 5,984 | 5,984 | 0 | 0 | +\$7.6693 | 0.77% | 1.0021 | \$139.1733 (12.65%) | — |
| `ONE_TRADE_PER_COMBO` | `IDEAL` | 5,984 | 4,150 | 4,150 | 1,834 | 0 | +\$516.3583 | 51.64% | 1.2278 | \$53.7634 (3.46%) | — |
| `ONE_TRADE_PER_COMBO` | `BASE` | 5,984 | 4,157 | 4,157 | 1,827 | 0 | +\$338.0100 | 33.80% | 1.1435 | \$62.6438 (4.49%) | — |
| `ONE_TRADE_PER_COMBO` | `STRESS` | 5,984 | 4,168 | 4,168 | 1,816 | 0 | +\$57.2547 | 5.73% | 1.0229 | \$80.7719 (7.11%) | — |
| `BINGX_PORTFOLIO` | `IDEAL` | 5,984 | 4,150 | 4,150 | 1,834 | 0 | +\$516.3583 | 51.64% | 1.2406 | \$53.8514 (3.47%) | \$56.7360 (3.65%) |
| `BINGX_PORTFOLIO` | `BASE` | 5,984 | 4,157 | 4,157 | 1,827 | 0 | +\$338.0100 | 33.80% | 1.1514 | \$62.6438 (4.49%) | \$65.7158 (4.70%) |
| `BINGX_PORTFOLIO` | `STRESS` | 5,984 | 4,168 | 4,168 | 1,816 | 0 | +\$57.2547 | 5.73% | 1.0242 | \$80.7719 (7.11%) | \$81.9412 (7.61%) |

---

## 4. Xác minh Số liệu C14 Baseline (`comparison_20_combos.csv`)

- **ALL_SIGNALS (316 lệnh: 174 BUY, 142 SELL):**
  - `IDEAL`: `+$10.5860` (BUY) + `+$8.4780` (SELL) = **`+$19.0640`**
  - `BASE`: `+$4.4373` (BUY) + `+$2.9726` (SELL) = **`+$7.4099`**
  - `STRESS`: `-$6.1156` (BUY) + `-$6.6222` (SELL) = **`-$12.7378`**
- **ONE_TRADE_PER_COMBO (Own Entry PnL — 294 lệnh ở IDEAL/BASE, 298 lệnh ở STRESS):**
  - `IDEAL`: `+$7.2217` (BUY, 161) + `+$10.0450` (SELL, 133) = **`+$17.2667`**
  - `BASE`: `+$1.3338` (BUY, 161) + `+$4.7198` (SELL, 133) = **`+$6.0536`**
  - `STRESS`: `-$7.7959` (BUY, 163) + `-$3.5700` (SELL, 135) = **`-$11.3659`**
- **BINGX_PORTFOLIO (VWAP Allocated PnL — cùng số lệnh với ONE_TRADE_PER_COMBO):**
  - `IDEAL`: `-$5.1051` (BUY) + `+$0.2915` (SELL) = **`-$4.8136`**
  - `BASE`: `-$11.4116` (BUY) + `-$5.7299` (SELL) = **`-$17.1415`**
  - `STRESS`: `-$20.4176` (BUY) + `-$12.8906` (SELL) = **`-$33.3082`**

---

## 5. Trạng thái Validation & Live Parity Baseline

- **Validation (`reports/fixed_tpsl_v1/validation_results.json`):** `PARTIALLY_VERIFIED` trên cả 9 cấu hình (run sạch tại SHA `14b57851`, `code_dirty=False`):
  - `A1, A2, B2, B3, C1, C2, C3, C4, C5, D1`: **PASS (9/9)**
  - `D2`: **PASS (6/9 IDEAL & BASE)**, **`INCONCLUSIVE` (3/9 STRESS)**
  - `B1` (Frozen signal parity): **`INCONCLUSIVE` (9/9)** vì `20FINAL_FIXED_V1@1` là baseline mới chưa có frozen reference đăng ký trước.
- **Live Parity (`LIVE_BACKTEST_PARITY_REPORT.md`):** **`PARITY_PENDING_LIVE_VERIFICATION`** — KHÔNG giả định parity live đã PASS.
