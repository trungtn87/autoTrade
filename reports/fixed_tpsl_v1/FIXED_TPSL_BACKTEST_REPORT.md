# FIXED_TP_SL BACKTEST REPORT — AUTOTRADE V3 (`20FINAL_FIXED_V1`)

**Strategy Baseline:** `20FINAL_FIXED_V1@1` (100% Fixed TP/SL, zero Trailing Stop, zero Breakeven)  
**Simulator Version:** `v3.16-fixed-tpsl`  
**Dataset:** BTCUSDT & ETHUSDT M1 (`2023-11-01` to `2026-11-01` UTC, 3,156,480 M1 bars)  
**Dataset Hash:** `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`  
**Ambiguity Policy:** `CONSERVATIVE`  
**Parity Status:** `PARITY_PENDING_LIVE_VERIFICATION`

---

## 1. Tổng hợp 9 cấu hình Backtest chính (`20FINAL_FIXED_V1`)

| Mode | Cost Profile | Signals | Entries | Exits (Legs) | Skipped (Concurrency) | Rejected (Margin) | Gross PnL | Fees | Slippage | Net PnL | ROI | Win Rate | Profit Factor | Closed DD | M2M DD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ALL_SIGNALS` | `IDEAL` | 5,984 | 5,984 | 5,984 | 0 | 0 | \$1,257.23 | \$598.42 | \$0.00 | **+\$658.82** | +65.88% | 38.42% | 1.1972 | \$79.06 (4.79%) | — |
| `ALL_SIGNALS` | `BASE` | 5,984 | 5,984 | 5,984 | 0 | 0 | \$1,005.62 | \$598.40 | \$239.36 | **+\$407.22** | +40.72% | 37.58% | 1.1177 | \$91.18 (6.35%) | — |
| `ALL_SIGNALS` | `STRESS` | 5,984 | 5,984 | 5,984 | 0 | 0 | \$606.07 | \$598.40 | \$598.40 | **+\$7.67** | +0.77% | 36.20% | 1.0021 | \$139.17 (12.65%) | — |
| `ONE_TRADE_PER_COMBO` | `IDEAL` | 5,984 | 4,150 | 4,150 | 1,834 | 0 | \$931.43 | \$415.07 | \$0.00 | **+\$516.36** | +51.64% | 39.18% | 1.2278 | \$53.76 (3.46%) | — |
| `ONE_TRADE_PER_COMBO` | `BASE` | 5,984 | 4,157 | 4,157 | 1,827 | 0 | \$753.77 | \$415.76 | \$166.30 | **+\$338.01** | +33.80% | 38.27% | 1.1435 | \$62.64 (4.49%) | — |
| `ONE_TRADE_PER_COMBO` | `STRESS` | 5,984 | 4,168 | 4,168 | 1,816 | 0 | \$474.12 | \$416.86 | \$416.86 | **+\$57.25** | +5.73% | 36.83% | 1.0229 | \$80.77 (7.11%) | — |
| `BINGX_PORTFOLIO` | `IDEAL` | 5,984 | 4,150 | 4,150 | 1,834 | 0 | \$931.43 | \$415.07 | \$0.00 | **+\$516.36** | +51.64% | 39.95% | 1.2406 | \$53.85 (3.47%) | \$56.74 (3.65%) |
| `BINGX_PORTFOLIO` | `BASE` | 5,984 | 4,157 | 4,157 | 1,827 | 0 | \$753.77 | \$415.76 | \$166.30 | **+\$338.01** | +33.80% | 39.09% | 1.1514 | \$62.64 (4.49%) | \$65.72 (4.70%) |
| `BINGX_PORTFOLIO` | `STRESS` | 5,984 | 4,168 | 4,168 | 1,816 | 0 | \$474.12 | \$416.86 | \$416.86 | **+\$57.25** | +5.73% | 37.50% | 1.0242 | \$80.77 (7.11%) | \$81.94 (7.61%) |

---

## 2. Đối chiếu với thí nghiệm `FIXED_TP_SL` trước đây & `CURRENT_TRAILING` (`20FINAL_BASELINE`)

### A. Đối chiếu với mốc A/B `FIXED_TP_SL` (`evidence/v3-fixed-vs-trailing/comparison_summary.csv`)

| Mode / Cost Profile | `20FINAL_FIXED_V1` Net PnL | Mốc A/B trước đây | Chênh lệch |
|---|---:|---:|---:|
| `BINGX_PORTFOLIO` / `IDEAL` | +\$516.3583 | +\$516.36 (+\$516.3583) | \$0.00 |
| `BINGX_PORTFOLIO` / `BASE` | +\$338.0100 | +\$338.01 (+\$338.0100) | \$0.00 |
| `BINGX_PORTFOLIO` / `STRESS` | +\$57.2547 | +\$57.25 (+\$57.2547) | \$0.00 |

### B. So sánh `FIXED_TP_SL` (`20FINAL_FIXED_V1`) vs `CURRENT_TRAILING` (`20FINAL_BASELINE`)

| Mode / Cost Profile | `FIXED_TP_SL` Net PnL | `CURRENT_TRAILING` Net PnL | Chênh lệch | Nhận xét |
|---|---:|---:|---:|---|
| `ALL_SIGNALS` / `IDEAL` | +\$658.82 | +\$410.70 | +\$248.11 | Loại bỏ Trailing leg giúp không bị cắt non ở callback 0.5%. |
| `BINGX_PORTFOLIO` / `IDEAL` | +\$516.36 | +\$282.19 | +\$234.16 | 4,150 legs thay vì 6,265 legs; Gross PnL cao hơn đáng kể. |
| `BINGX_PORTFOLIO` / `BASE` | +\$338.01 | +\$111.28 | +\$226.73 | Giữ được lợi nhuận dương ổn định dưới slippage 2 bps/chiều. |
| `BINGX_PORTFOLIO` / `STRESS` | +\$57.25 | -\$156.60 | +\$213.85 | `FIXED_TP_SL` vẫn dương ở 5 bps/chiều trong khi Trailing âm. |

---

## 3. Phân tích chi tiết `BINGX_PORTFOLIO` (BTC/ETH, Long/Short, Năm, Exit/Reject)

### Theo Symbol (`BINGX_PORTFOLIO`)
| Cost Profile | Symbol | Trades | Win Rate | Net PnL | Profit Factor |
|---|---|---:|---:|---:|---:|
| `IDEAL` | `BTC-USDT` | 1,861 | 42.56% | +\$261.44 | 1.2885 |
| `IDEAL` | `ETH-USDT` | 2,289 | 37.83% | +\$254.92 | 1.2056 |
| `BASE` | `BTC-USDT` | 1,862 | 41.68% | +\$179.55 | 1.1905 |
| `BASE` | `ETH-USDT` | 2,295 | 36.99% | +\$158.46 | 1.1228 |
| `STRESS` | `BTC-USDT` | 1,869 | 39.81% | +\$48.05 | 1.0478 |
| `STRESS` | `ETH-USDT` | 2,299 | 35.62% | +\$9.21 | 1.0068 |

### Theo Side (`BINGX_PORTFOLIO`)
| Cost Profile | Side | Trades | Win Rate | Net PnL | Profit Factor |
|---|---|---:|---:|---:|---:|
| `IDEAL` | `BUY` (LONG) | 2,389 | 39.64% | +\$301.50 | 1.2518 |
| `IDEAL` | `SELL` (SHORT) | 1,761 | 40.37% | +\$214.86 | 1.2265 |
| `BASE` | `BUY` (LONG) | 2,396 | 38.73% | +\$194.70 | 1.1556 |
| `BASE` | `SELL` (SHORT) | 1,761 | 39.58% | +\$143.31 | 1.1459 |
| `STRESS` | `BUY` (LONG) | 2,404 | 37.52% | +\$61.04 | 1.0464 |
| `STRESS` | `SELL` (SHORT) | 1,764 | 37.47% | -\$3.78 | 0.9964 |

### Theo Năm (`BINGX_PORTFOLIO` / `BASE`)
| Năm | Trades | Win Rate | Gross PnL | Fees | Net PnL | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|
| 2023 (Nov–Dec) | 89 | 25.84% | -\$16.48 | \$8.89 | -\$25.37 | 0.5759 |
| 2024 | 1,507 | 40.81% | +\$334.04 | \$150.75 | +\$183.29 | 1.2342 |
| 2025 | 1,388 | 37.97% | +\$212.47 | \$138.82 | +\$73.65 | 1.0976 |
| 2026 (Jan–Oct) | 1,173 | 39.22% | +\$223.74 | \$117.30 | +\$106.44 | 1.1674 |

*(Chi tiết theo từng tháng và từng combo trong `breakdowns.json` và `comparison_20_combos.csv`.)*

### Lý do Exit & Lý do Từ chối (`BINGX_PORTFOLIO`)
- **IDEAL:** `TP = 1,626`, `SL = 2,524`; `ACCEPTED = 4,150`, `SKIPPED_BY_CONCURRENCY = 1,834`, `REJECTED_BY_MARGIN = 0`.
- **BASE:** `TP = 1,591`, `SL = 2,566`; `ACCEPTED = 4,157`, `SKIPPED_BY_CONCURRENCY = 1,827`, `REJECTED_BY_MARGIN = 0`.
- **STRESS:** `TP = 1,535`, `SL = 2,633`; `ACCEPTED = 4,168`, `SKIPPED_BY_CONCURRENCY = 1,816`, `REJECTED_BY_MARGIN = 0`.

---

## 4. Validation Gates V3, Reproducibility & Regression Tests

- **Clean Working Tree Run:** Toàn bộ 9 cấu hình được chạy lại trên commit `14b57851e137d7f3830c35da2c0dfb30f6eb7e43` với `code_dirty=False` (`task-257`). Tất cả 9 lượt đạt trạng thái **`PARTIALLY_VERIFIED`** (0 gate `FAIL`).
- **Bảng trạng thái từng Gate (9 cấu hình):**
  - `A1` (Dataset hash), `A2` (M1 integrity), `B2` (No-lookahead), `B3` (Batch-Live equiv), `C1` (Entry causality — đã sửa để đọc `cost_profile` slippage/spread), `C2` (ExitPolicy propagation), `C3` (Exit replay), `C4` (Mode C accounting), `C5` (Ambiguity policy), `D1` (Artifact hashes): **PASS (9/9 cấu hình)**.
  - `D2` (Reported numbers): **PASS** ở 6 cấu hình `IDEAL` & `BASE`; **`INCONCLUSIVE`** ở 3 cấu hình `STRESS` (`1,645/1,646` checked, `0 violations`, toàn bộ `1,441/1,441` leaves của `analysis.json` đều khớp, chỉ có 1 con số trong `report.md` không khớp regex).
  - `B1` (Frozen signal parity): **`INCONCLUSIVE` (9/9 cấu hình)** vì `20FINAL_FIXED_V1@1` là baseline mới chưa có frozen reference baseline đăng ký trước.
- **Reproducibility:** Chạy lại ở hai thời điểm khác nhau (trên `ec2c7f96` và `14b57851`) đều tái lập chính xác tuyệt đối 3 mốc `BINGX_PORTFOLIO`: `IDEAL = +$516.3583`, `BASE = +$338.0100`, `STRESS = +$57.2547` (sai số `0.0000`).
- **Regression & Unit Tests (tổng 428 tests trong `autotrade_v3/tests/`):**
  - `390/390` passed (`task-162`, khi chạy `--ignore` 3 file nặng `test_sim_v313_integration.py`, `test_golden_baseline_v308.py`, `test_mutation_v308.py` vốn chứa `19` tests).
  - `15/15` passed cho `test_sim_v313_integration.py` + `test_strategy_core_v311.py` (`task-27`).
  - `17/17` passed cho [test_gate_c1_cost_profiles.py](file:///d:/AutoTrade/autotrade_v3/tests/test_gate_c1_cost_profiles.py) (BUY/SELL 0/2/5bps, sai giá, ngược chiều slippage, look-ahead).
  - `12/12` passed cho [test_fixed_tpsl_v1.py](file:///d:/AutoTrade/autotrade_v3/tests/test_fixed_tpsl_v1.py) và [test_fixed_tpsl_execution_scenarios.py](file:///d:/AutoTrade/autotrade_v3/tests/test_fixed_tpsl_execution_scenarios.py).
  - *(Chênh lệch `395` vs `390` ở lần chạy trước là do `390` đã `--ignore` 3 file nặng 19 tests và trước khi thêm 17 tests Gate C1 mới; tổng toàn bộ suite hiện tại là `428` tests.)*
