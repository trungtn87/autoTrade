# AutoTrade V3 Research Lab — V3-13 Evidence Report (Execution Cost & Ambiguity Model)

- **Branch:** `feat/v3-13-costs-ambiguity`
- **Base SHA (V3-12):** `ad74e292d9b781e4cc60503fd05e2cafab3208c4`
- **Code SHA (Clean Suite):** `25b76931bbdb092c4ede7d9cf09ab5dfa2cea0a6`
- **Date (UTC):** `2026-11-14`
- **Overall Acceptance Status:** `PARTIALLY_VERIFIED` (IDEAL + LEGACY/CONSERVATIVE/OPTIMISTIC/OHLC_PATH, Mode C accounting & margin, C2/C3/C4/D2 & M1–M6, 327/327 pytest = `VERIFIED`; **BASE & STRESS real-data runs = `BLOCKED_ON_OWNER`** because `OWNER_COST_INPUT` is empty and `cost_profiles.json` leaves `BASE` and `STRESS` with `status: UNCONFIRMED`).

---

## A. Git & Phạm Vi Sửa Đổi (`VERIFIED`)

- Protected diff check (`git diff --stat ad74e29..HEAD -- autotrade_v2 runs/golden/v3-08 runs/golden/v3-12 autotrade_v3/strategy_core`): **(empty)** — see `evidence/v3-13/git_state.txt`.
- Commit sequence on `feat/v3-13-costs-ambiguity`:
  1. `a4b7555` — `feat(v3-13): add CostModel, cost_profiles.json, and itemized cost columns`
  2. `8c54055` — `chore(v3-13): parity checkpoint IDEAL+LEGACY vs golden v3-12 and B3 sensitivity reproduction`
  3. `d9b6556` — `feat(v3-13): add 4 intrabar ambiguity policies and unit tests`
  4. `2d4b1b5` — `feat(v3-13): update C2/C3/C4 and independent replay for costs and ambiguity policies`
  5. `3af2928` — `feat(v3-13): add Mode C two-column PnL, accounting invariant, mmr warning, and simulation_limits`
  6. `03ec6b1` — `feat(v3-13): add CLI --cost-profile/--ambiguity-policy, uncertainty cmd, and report breakdown`
  7. `d8f849d` — `feat(v3-13): update D2 validation for V3-13 fields and add golden v3-13 + evidence`
  8. `25b7693` — `fix(v3-13): preserve V3-08 report/D2 backward compatibility`

### Large Artifacts Omitted From Git (> 20 MB)
- `D:\AutoTrade\runs\golden\v3-13\BINGX_PORTFOLIO\snapshots.csv`: `81,054,899` bytes, SHA256 `61840fe56609e5a49abdf68e4bab6483f3d61b94811043af4de4048e61fb1ec6`
- `D:\AutoTrade\runs\golden\v3-13\BINGX_PORTFOLIO\snapshots.parquet`: `23,619,083` bytes, SHA256 `a02644b48fe0cec76bd00a58afb8acb3e40c18b81832c66197dc34ea8c7dca6a`
- Read command: `.\.venv\Scripts\python.exe -c "import pandas as pd; print(pd.read_parquet('runs/golden/v3-13/BINGX_PORTFOLIO/snapshots.parquet').shape)"`
- `runs/golden/v3-13/SHA256SUMS.txt` covers all files `<= 20 MB` (`49` entries).

---

## B. Ma Trận Tiêu Chí Nghiệm Thu (AC1–AC9)

| ID | Tiêu chí | Trạng thái | Bằng chứng & Artifact |
| :--- | :--- | :--- | :--- |
| **AC1** | Không đụng `autotrade_v2/`, `runs/golden/v3-08/`, `runs/golden/v3-12/`, `strategy_core/`; `validation/` không import `sim/` | `VERIFIED` | `evidence/v3-13/git_state.txt` (PROTECTED_DIFF empty); `test_validation_import_boundary.py` PASS |
| **AC2** | Bất biến chi phí `net_pnl = gross_pnl_raw - fee_entry - fee_exit - spread_cost - slippage_cost - funding_cost` (sai số `< 1e-9`) | `VERIFIED` | `evidence/v3-13/mode_c_accounting.log` (`AC2 Max diff: 0.000e+00`); `runs/golden/v3-13/*/validation.json` (`C4=PASS`) |
| **AC3** | `IDEAL + LEGACY` trùng `runs/golden/v3-12/` trên cả 3 mode; tái hiện mốc độ nhạy lịch sử `0/1/3/5 bps` | `VERIFIED` | `evidence/v3-13/parity_ideal_legacy/{ALL_SIGNALS,ONE_TRADE_PER_COMBO,BINGX_PORTFOLIO}.txt` (0 diffs); `evidence/v3-13/sensitivity_reproduction.md` (`282.1935 / 199.4714 / 38.3273 / -156.5989`, max diff `0.0000`) |
| **AC4** | `BASE` & `STRESS` trong `cost_profiles.json` + CLI chạy trên dữ liệu thật | `BLOCKED_ON_OWNER` | `OWNER_COST_INPUT` trống; `autotrade_v3/sim/cost_profiles.json` để `BASE` và `STRESS` ở trạng thái `UNCONFIRMED` (`values: {}`); CLI từ chối chạy real-data trên `UNCONFIRMED` và `TEST_ONLY` |
| **AC5** | 4 chính sách nhập nhằng (`LEGACY`, `CONSERVATIVE`, `OPTIMISTIC`, `OHLC_PATH`) + `cli.py uncertainty` | `VERIFIED` | `test_sim_ambiguity.py` (35 tests PASS); `evidence/v3-13/uncertainty/{ALL_SIGNALS,ONE_TRADE_PER_COMBO,BINGX_PORTFOLIO}.json` & `*_signal_ids.txt` |
| **AC6** | `runs/golden/v3-13/` (`IDEAL + CONSERVATIVE`) qua đủ 12 cổng `A1..D2` trên cả 3 mode | `VERIFIED` | `evidence/v3-13/summary_metrics.json` & `evidence/v3-13/verify/*/validation.json` (`overall_validation: VERIFIED`, `A1..D2: PASS`) |
| **AC7** | Mode C: hai cột `pnl_own_entry` / `pnl_vwap`, bất biến kế toán, kiểm tra `mmr`, `simulation_limits` trong manifest | `VERIFIED` | `evidence/v3-13/mode_c_accounting.log` (`max_group_diff=8.9528e-13`, `expected_final_equity=1282.193484036123`, `actual_final_equity=1282.1935`); `evidence/v3-13/margin_scenarios.log` (`[SYNTHETIC]`) |
| **AC8** | Độc lập `validation/` (`C2`, `C3`, `C4`, `D2`) + 6 kiểm tra đột biến `M1..M6` | `VERIFIED` | `evidence/v3-13/mutation_tests.log` (M1..M6 đều FAIL đúng kỳ vọng); `test_sim_v313_integration.py` (C2/C3/C4 PASS trên `CONSERVATIVE/OPTIMISTIC/OHLC_PATH` với `TEST_ONLY`) |
| **AC9** | Bộ test đầy đủ + lint + compileall sạch | `VERIFIED` | `ruff check autotrade_v3` exit `0`; `compileall -q autotrade_v3` exit `0`; `pytest autotrade_v3/tests` -> `327 passed in 907.41s` (`evidence/v3-13/junit/all.xml`, `evidence/v3-13/pytest_all.log`) |

---

## C. Parity `IDEAL + LEGACY` vs `runs/golden/v3-12/` & Độ Nhạy Lịch Sử (`VERIFIED`)

1. **Parity `IDEAL + LEGACY` (commit `a4b7555`):**
   - `ALL_SIGNALS`: `8,258` / `8,258` matched, `only_a=0`, `only_b=0`, `bad_cols=[]` (`evidence/v3-13/parity_ideal_legacy/ALL_SIGNALS.txt`).
   - `ONE_TRADE_PER_COMBO`: `6,265` / `6,265` matched, `only_a=0`, `only_b=0`, `bad_cols=[]` (`evidence/v3-13/parity_ideal_legacy/ONE_TRADE_PER_COMBO.txt`).
   - `BINGX_PORTFOLIO`: `6,265` / `6,265` matched, `only_a=0`, `only_b=0`, `bad_cols=[]` (`evidence/v3-13/parity_ideal_legacy/BINGX_PORTFOLIO.txt`).
2. **Tái hiện độ nhạy lịch sử (`runs/v3_audited/sensitivity_analysis.json`, Mode C + `LEGACY` + `fee_taker=0.0005` + `spread=0`):**
   - `0 bps`: `6,265` legs, Net PnL `282.1935` (diff `-0.0000`) — `evidence/v3-13/sensitivity/s0.log`
   - `1 bps`: `6,270` legs, Net PnL `199.4714` (diff `+0.0000`) — `evidence/v3-13/sensitivity/s1.log`
   - `3 bps`: `6,273` legs, Net PnL `38.3273` (diff `-0.0000`) — `evidence/v3-13/sensitivity/s3.log`
   - `5 bps`: `6,289` legs, Net PnL `-156.5989` (diff `+0.0000`) — `evidence/v3-13/sensitivity/s5.log`

---

## D. Golden `runs/golden/v3-13/` (`IDEAL + CONSERVATIVE`, `VERIFIED`)

*Lưu ý: Đây là kết quả mô phỏng trên dữ liệu lịch sử với hồ sơ `IDEAL` (slippage = 0, spread = 0, funding = NOT_MODELED), không phải lợi nhuận giao dịch thực tế.*

| Mode | Validation (`A1..D2`) | Accepted Entries | Total Legs | Gross PnL (Fill) | Total Fees | Net PnL | ROI | Leg WR | Net PF | Closed DD | M2M DD |
| :--- | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `ALL_SIGNALS` | `VERIFIED` (12/12 PASS) | 5,984 | 8,258 | 1,009.0873 | 598.3866 | 410.7007 | 41.07% | 42.75% | 1.13 | 5.65% | N/A |
| `ONE_TRADE_PER_COMBO` | `VERIFIED` (12/12 PASS) | 4,149 | 6,265 | 697.1388 | 414.9453 | 282.1935 | 28.22% | 44.31% | 1.14 | 3.47% | N/A |
| `BINGX_PORTFOLIO` | `VERIFIED` (12/12 PASS) | 4,149 | 6,265 | 697.1388 | 414.9453 | 282.1935 | 28.22% | 45.06% | 1.14 | 3.46% | 3.70% |

- `CONSERVATIVE` vs `LEGACY` tại `IDEAL` trên dữ liệu M1 thật 3 năm (`evidence/v3-13/conservative_vs_legacy/*.csv`): `0` leg khác biệt trên cả 3 mode (`changed=0`, `diff=0.0`).

---

## E. Khoảng Bất Định Nhập Nhằng Nội Nến (`cli.py uncertainty`, `VERIFIED`)

Chạy ở hồ sơ `IDEAL` trên dữ liệu M1 thật 3 năm (`CONSERVATIVE` vs `OPTIMISTIC`):

| Mode | Conservative Net PnL | Optimistic Net PnL | Uncertainty Delta (`opt - con`) | Số Signal ID thay đổi | Artifact |
| :--- | ---: | ---: | ---: | ---: | :--- |
| `ALL_SIGNALS` | 410.7007 | 482.6984 | +71.9977 | 493 | `evidence/v3-13/uncertainty/ALL_SIGNALS.json`, `ALL_SIGNALS_signal_ids.txt` |
| `ONE_TRADE_PER_COMBO` | 282.1935 | 348.0677 | +65.8742 | 467 | `evidence/v3-13/uncertainty/ONE_TRADE_PER_COMBO.json`, `ONE_TRADE_PER_COMBO_signal_ids.txt` |
| `BINGX_PORTFOLIO` | 282.1935 | 348.0677 | +65.8742 | 499 | `evidence/v3-13/uncertainty/BINGX_PORTFOLIO.json`, `BINGX_PORTFOLIO_signal_ids.txt` |

Phân bố `ambiguity.by_type` trong `runs/golden/v3-13/*/analysis.json` (`CONSERVATIVE`):
- `ALL_SIGNALS`: `TP_AND_SL=0`, `SL_AND_TRAIL_ACT=3`, `TRAIL_ACT_AND_CB=312`, `TRAIL_PEAK_AND_STOP=178`.
- `ONE_TRADE_PER_COMBO`: `TP_AND_SL=0`, `SL_AND_TRAIL_ACT=3`, `TRAIL_ACT_AND_CB=295`, `TRAIL_PEAK_AND_STOP=168`.
- `BINGX_PORTFOLIO`: `TP_AND_SL=0`, `SL_AND_TRAIL_ACT=3`, `TRAIL_ACT_AND_CB=295`, `TRAIL_PEAK_AND_STOP=168`.

---

## F. Mode C Kế Toán Hai Cột & Kịch Bản Margin (`VERIFIED`)

1. **Kế toán hai cột & bất biến (`evidence/v3-13/mode_c_accounting.log`):**
   - Total Legs: `6,265`
   - AC2 Max diff (`net_pnl` vs `gross_pnl_raw - all_costs`): `0.000e+00`
   - Mode C Accounting Invariant (`verify_mode_c_accounting_invariant`): `passed: true`, `max_group_diff: 8.952838470577262e-13`, `expected_final_equity: 1282.193484036123`, `actual_final_equity: 1282.1935`.
2. **Kịch bản Margin `[SYNTHETIC]` (`evidence/v3-13/margin_scenarios.log`):**
   - Scenario 1 (`INSUFFICIENT_MARGIN`): `rejected=1`, `reason=INSUFFICIENT_MARGIN`.
   - Scenario 2 (`LIQUIDATION_WARNING`): `status=LIQUIDATION_WARNING`, `time_ms=1704067260000`, `adverse_equity=1.45`, `mm=4.8`.

---

## G. Kiểm Tra Đột Biến `M1..M6` (`VERIFIED`, `evidence/v3-13/mutation_tests.log`)

- **M1** (sửa `slippage_cost` không sửa `net_pnl`): `C4=FAIL` (`NET_COST_INVARIANT`).
- **M2** (trả `exit_px_fill` SL về giá trigger): `C3=FAIL` (`EXIT_PRICE`).
- **M3** (`[SYNTHETIC]` chạy `OPTIMISTIC` trên nến `TP_AND_SL`, gắn nhãn `CONSERVATIVE`; control `C3=PASS`): `C3=FAIL` (`EXIT_REASON,EXIT_PRICE`).
- **M4** (sửa `pnl_vwap` Mode C thêm `+1.0`): `invariant_passed=False`, `max_group_diff=0.9999999999991331`.
- **M5** (sửa `cost_profiles.json` giữ nguyên tên `IDEAL`): `C4=FAIL` (`COST_PROFILE_HASH_MISMATCH`).
- **M6** (đổi chiều slippage thành có lợi): `pytest_exit=1`.

---

## H. Kiểm Tra Tĩnh & Toàn Bộ Test Suite (`25b7693`)

- `.\.venv\Scripts\python.exe -m ruff check autotrade_v3`: **VERIFIED** (`All checks passed!`, exit `0`).
- `.\.venv\Scripts\python.exe -m compileall -q autotrade_v3`: **VERIFIED** (exit `0`).
- `.\.venv\Scripts\python.exe -m pytest autotrade_v3/tests --junitxml=evidence/v3-13/junit/all.xml -q`: **VERIFIED** (`327 passed in 907.41s`, 0 failed, 0 skipped; log tại `evidence/v3-13/pytest_all.log`).
- `mypy`: **UNVERIFIED** (`mypy` chưa được cài trong `.venv`).

---

## I. Hạng Mục `BLOCKED_ON_OWNER` & Điều Kiện Nghiệm Thu Tiếp Theo

- **Hạng mục bị chặn:** Chạy dữ liệu thật cho hồ sơ `BASE` và `STRESS` (`AC4`).
- **Lý do cụ thể:** `OWNER_COST_INPUT` trong lệnh triển khai đang trống; `autotrade_v3/sim/cost_profiles.json` giữ `BASE` và `STRESS` ở trạng thái `status: "UNCONFIRMED"` với `values: {}`. Không tự điền hoặc ước lượng tham số chi phí thực tế.
- **Hướng mở khóa:** Khi Owner cung cấp tham số `fee_taker`, `fee_maker`, `half_spread_bps`, `slippage_bps` (theo từng loại lệnh `entry_market`, `tp`, `stop`, `trailing_stop`), và `funding` (hoặc bảng funding lịch sử) cho `BASE` và `STRESS` theo `docs/autotrade-v3/13-cost-calibration-method.md`, cập nhật `cost_profiles.json` thành `CONFIRMED` và chạy `cli.py run --cost-profile BASE` / `STRESS`.
