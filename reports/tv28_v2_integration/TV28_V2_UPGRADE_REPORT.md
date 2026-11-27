# TV28 v2 Upgrade Report (`BTC-USDT` / `H8` / `E1_FIXED` / `$100`)

- **Ngày xuất:** `2026-11-27T01:45:00Z`
- **Git HEAD:** `1a5e77e61acc75029d51ed02e0f8ce82c98c755b` (chưa commit, chưa merge/deploy VPS — chỉ cập nhật local)
- **Version Tag:** `TV28_V2_H8`
- **Thông số khóa cứng:** `BTC-USDT`, `H8` (`480` phút, đóng nến `00:00 / 08:00 / 16:00 UTC`), `L2_S1` (`SSL Hybrid HMA(60) + QQE Mod`, `B0` flip `edge_state(lo, sh)`), warmup `120` nến `H8`, Market Open kế tiếp, `SL = 1.2%`, `TP = 3.0%` (`V2_SL_TIGHTENING`), notional `$100` (`1.0x`), `Safe Leverage (50% buffer) = 41x`.

---

## 1. Bước 1 — Impact Audit & Pipeline `H8`

### Các file tham chiếu `TV28` / `B052` / `TV31_SELECTED_REGISTRY`
- **Freeze & Registry gốc (GIỮ NGUYÊN 0 THAY ĐỔI):**
  - `research/v3_lab_11_plus31/freeze/TV31_SELECTED_REGISTRY.csv` (dòng 29: `TV28`, `B052__V2_SL_TIGHTENING`, `BTC-USDT`, `D1`, `1440`)
  - `research/v3_lab_11_plus31/freeze/TV31_DISPLAY_MAP.csv`
  - `research/v3_lab_11_plus31/outputs/plus43_all_signals/*` (baseline lịch sử `PRUNE23_PLUS_TV20`)
  - `reports/v3_lab_11_funding_3y/LOCKED_CONFIG_PRUNE23.json` (0 thay đổi)
- **Adapter & Kiểm thử:**
  - `autotrade_v3/reconcile_v3/keys.py`: `build_combo_table()` đọc `TV31_SELECTED_REGISTRY.csv`
  - `autotrade_v3/dashboard/plus44_source.py`: đọc thư mục output `PRUNE23_PLUS_TV20` (mặc định `outputs/plus43_all_signals`, hỗ trợ override qua `AUTOTRADE_PLUS43_ALL_SIGNALS_DIR`)
  - `autotrade_v3/tests/test_v3_plus31_research.py`: kiểm định registry/parity lịch sử `TV31`/`TV21`
- **Xác nhận pipeline `H8`:**
  - `core.tvlab.resample_m1_closed(m1, 480)` và `p43_fwd._resample_closed_safe(m1, 480)` resample đúng các nến `8h` đã đóng tại `00:00 / 08:00 / 16:00 UTC` (`hours_utc = [0, 8, 16]`), cắt bỏ nến đang chạy chưa đóng (`last_closed_h8_bar_open_utc = 2026-11-26T08:00:00Z` khi M1 mới nhất ở `2026-11-26T16:04:00Z`), khớp pha 100% với `TV10` và `TV12`.

---

## 2. Bước 2 — Danh sách file tạo mới / cập nhật & Rollback

### File tạo mới (Versioned v2 — không ghi đè baseline cũ)
- `research/v3_lab_11_plus31/registry_v2/TV20_PLUS43_V2_REGISTRY.csv` (20 mã `TV20`, nâng cấp riêng dòng `TV28` sang `TV28_V2_H8`, giữ `TV10` và `TV12` độc lập)
- `research/v3_lab_11_plus31/registry_v2/TV28_V2_H8_MANIFEST.json` (metadata, SHA256 và đường dẫn rollback)
- `research/v3_lab_11_plus31/run_plus43_v2_tv28_h8.py` (runner độc lập xuất artifact sang `research/v3_lab_11_plus31/outputs/plus43_v2_tv28_h8/`)
- `research/v3_lab_11_plus31/outputs/plus43_v2_tv28_h8/*` (`PORTFOLIO_SUMMARY_ALL_SIGNALS.csv`, `PORTFOLIO_SPLITS_DEV_VAL_HOLDOUT.csv`, `PORTFOLIO_YEARLY_ALL_SIGNALS.csv`, `PER_COMBO_PLUS43_ALL_SIGNALS.csv`, `DAILY_EQUITY_ALL_SIGNALS.csv`, `RUN_MANIFEST.json`)
- `autotrade_v3/tests/test_tv28_v2_h8.py` (3 unit/integration test mới cho `TV28_V2_H8`)
- `reports/tv28_v2_integration/tv28_v2_verification.json` & `reports/tv28_v2_integration/TV28_V2_UPGRADE_REPORT.md`

### File cập nhật tối thiểu (tương thích ngược)
- `autotrade_v3/reconcile_v3/keys.py`: giữ nguyên tải `TV31_SELECTED_REGISTRY.csv` (`("BTC-USDT", "TV28")` D1 cho đối soát lịch sử) và bổ sung tải `TV20_PLUS43_V2_REGISTRY.csv` để đăng ký khóa `("BTC-USDT", "TV28_V2_H8")` (`H8`, `TP=3.0%`, `SL=1.2%`, `combo_id` tách biệt hoàn toàn với `TV10` và `TV12`).

### Kiểm tra `git diff` an toàn
- `research/v3_lab_11_plus31/freeze/`: **0 thay đổi**
- `research/v3_lab_11_plus31/outputs/plus43_all_signals/`: **0 thay đổi**
- `reports/v3_lab_11_funding_3y/LOCKED_CONFIG_PRUNE23.json`: **0 thay đổi** (untracked pre-existing)
- **Rollback tức thì:** giữ nguyên `TV31_SELECTED_REGISTRY.csv` làm mặc định; chỉ cần không trỏ sang `registry_v2/TV20_PLUS43_V2_REGISTRY.csv` là toàn bộ hệ thống chạy lại `TV28 D1` cũ.

---

## 3. Bước 3 — Đối soát E2E (`PASS`)

Nguồn số liệu: `reports/tv28_v2_integration/tv28_v2_verification.json`

### A. Standalone `TV28 v2` (`2023-11-01` → `2026-10-31` & Forward `2026-11-01` → `2026-11-26`)

| Chỉ số | Kỳ vọng | Thực tế | Chênh lệch | Kết quả |
|---|---:|---:|---:|:---:|
| `BASE` Entries | 129 | 129 | `0` | **PASS** |
| `BASE` Wins / Losses | 50 / 79 | 50 / 79 | `0` | **PASS** |
| `BASE` Net Own ($) | +40.19922052 | +40.19922052 | `-1.78e-13` | **PASS** |
| `BASE` Fees ($) | 12.88800448 | 12.88800448 | `+1.78e-15` | **PASS** |
| `BASE` Slippage ($) | 5.15520410 | 5.15520410 | `+3.20e-09` | **PASS** |
| `BASE` Funding ($) | -0.46242500 | -0.46242500 | `-1.11e-16` | **PASS** |
| `BASE` Profit Factor | 1.385893 | 1.385893 | `+6.13e-08` | **PASS** |
| `STRESS` Entries | 129 | 129 | `0` | **PASS** |
| `STRESS` Net Own ($) | +36.31371380 | +36.31371380 | `-4.83e-13` | **PASS** |
| `STRESS` Profit Factor | 1.340861 | 1.340861 | `-3.26e-07` | **PASS** |
| `FORWARD BASE` Entries | 2 | 2 | `0` | **PASS** |
| `FORWARD BASE` Net Own ($) | +1.64394018 | +1.64394018 | `+9.33e-15` | **PASS** |

### B. Full Portfolio `PRUNE23_PLUS_TV20` (có `TV28 v2`, `ALL_SIGNALS_PORTFOLIO`)

| Chỉ số | Kỳ vọng | Thực tế | Chênh lệch | Kết quả |
|---|---:|---:|---:|:---:|
| `BASE` Entries | 6,560 | 6,560 | `0` | **PASS** |
| `BASE` Net Own ($) | +1561.17895801 | +1561.17895801 | `-3.93e-09` | **PASS** |
| `BASE` M2M MaxDD ($) | 180.835869 (`9.09%`) | 180.8358 (`9.09%`) | `-6.90e-05` | **PASS** |
| `BASE` Profit Factor (`VWAP`) | 1.410325 | 1.411711 (`pf_own = 1.356111`) | `+0.001386` | **PASS** |
| `STRESS` Entries | 6,560 | 6,560 | `0` | **PASS** |
| `STRESS` Net Own ($) | +1110.11293708 | +1110.11293708 | `-3.55e-09` | **PASS** |
| `STRESS` M2M MaxDD ($) | 208.951001 (`11.05%`) | 208.9509 (`11.05%`) | `-1.01e-04` | **PASS** |
| `STRESS` Profit Factor (`VWAP`) | 1.279513 | 1.280468 (`pf_own = 1.241836`) | `+0.000955` | **PASS** |

> **Ghi chú đối soát Portfolio:** `Net Own` (`+1561.17895801` BASE / `+1110.11293708` STRESS) và `M2M MaxDD` (`180.8358` / `208.9509`) khớp chính xác với mô phỏng trước đó đến `1e-9` / `1e-4`. Chỉ số `PF (VWAP)` của cả danh mục chạy gộp 6.560 lệnh qua `UnifiedSimulator` đạt `1.411711` (BASE) và `1.280468` (STRESS), cao hơn `+0.0014` / `+0.0010` so với ước lượng ghép nối tách rời (`1.410325` / `1.279513`). `equity_diff` kế toán trên engine `HEAD` là `3.44e-10` (BASE) và `5.00e-12` (STRESS).

---

## 4. Kiểm tra An toàn Hệ thống, Dry-Run `H8` & Test Suite

1. **Causality / Prefix-Parity `TV28 v2` (`H8`):** **PASS**
   - `hours_utc = [0, 8, 16]`, `total_h8_bars = 3288`, `clean_prefix_bars = 2301`
   - `prefix_invariance = True`, `future_perturbation_invariance = True`, `repeatability = True`
2. **Dry-Run Signal Generation trên `data/bingx` (`H8`):** **PASS**
   - `latest_m1_utc = 2026-11-26T16:04:00Z` → `last_closed_h8_bar_open_utc = 2026-11-26T08:00:00Z` (nến `16:00` chưa đóng được cắt bỏ an toàn).
   - `distinct_combo_ids = [31010, 31012, 31028]`, `signal_counts_2026_ytd = {"TV10": 7, "TV12": 40, "TV28": 40}`.
   - `unique_event_ids = True`, `unique_signal_ids = True` (0 va chạm khóa giữa `TV10`, `TV12`, `TV28_V2_H8`).
3. **Pytest Suite (`20 tests`):** **19/20 PASSED** (`300.06s`):
   - `autotrade_v3/tests/test_tv28_v2_h8.py`: **3/3 PASSED**
   - `autotrade_v3/tests/test_v3_plus31_research.py`: **3/3 PASSED**
   - `autotrade_v3/tests/test_lab_dashboard_one_page.py`: **6/6 PASSED**
   - `autotrade_v3/tests/test_reconcile_v3.py`: **7/8 PASSED** (`test_real_store_step2_classifications_and_compare_from` fail pre-existing trước khi sửa code do `data/bingx` hiện có `13` closed orders thay vì `0`).
