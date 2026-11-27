# PLUS43 V2.1 TP-Only Upgrade Report (`PORT_FINAL_TP_ONLY`)

- **Version Tag:** `PLUS43_V2_1_TP_ONLY` (`PORT_FINAL_TP_ONLY`)
- **Base Reference:** `PORT_V2_TV28H8_BASE` (`TV20_PLUS43_V2_REGISTRY.csv` + `PRUNE23`)
- **Generated At (UTC):** `2026-11-27T06:14:41Z`
- **Engine & Policy:** `HEAD` · `CONSERVATIVE` · `OWN_ENTRY_ONLY` · Full Signed Historical Funding + Fees + Slippage (`BASE 2bps`, `STRESS 5bps`)
- **Hard Invariants:** Config-only `TP%` update for 7 `TV20` combos; `0` dropped trades (`6,560` entries); `$100` notional; `max_sl_pct = 1.50%` (`Safe Lev >= 33x`); `TV21` unchanged (`TP=3.60%`, `SL=1.50%`, NO Breakeven); `TV28_V2_H8` unchanged (`BTC-USDT H8`, `TP=3.00%`, `SL=1.20%`, `warmup=120`); 23 `PRUNE23` combos unchanged; no new execution logic (`BE`, `Time-Stop`, `Trailing`); local only (NO VPS deploy).

---

## 1. Registry, Manifest & Rollback Hashes

| Artifact | Path | SHA-256 |
|---|---|---|
| **Registry V2.1 TP (Active)** | `research/v3_lab_11_plus31/registry_v2/TV20_PLUS43_V2_1_TP_REGISTRY.csv` | `dd75bd5057168162d438912ffa25dda9b7f64970bfac417d65ebfc56053db312` |
| **Manifest V2.1 TP** | `research/v3_lab_11_plus31/registry_v2/PLUS43_V2_1_TP_MANIFEST.json` | — |
| **Rollback Target V2 (`TV28_V2_H8`)** | `research/v3_lab_11_plus31/registry_v2/TV20_PLUS43_V2_REGISTRY.csv` | `a5a371c9b8251e4d45944454c6da4bdbfa838bc023b730e1834f26ff4ad1323d` |
| **Rollback Target V1 (`TV31` Freeze)** | `research/v3_lab_11_plus31/freeze/TV31_SELECTED_REGISTRY.csv` | `08071b3f56d06ea24b1635dbe1fb22b3a4b2c001f6f880b8507a5213f4ada79b` |
| **Dataset Manifest Hash** | 3Y M1 + BingX Forward | `a18fc2e1bf5985945fee6609847cce753cd319da74415dc23d4f4bae231487be` |
| **Cost Profiles Hash** | Canonical `IDEAL/BASE/STRESS` | `afbd14fb9c30c1df3a441dcfe7595dd31b9b605bbc31c3c0eb8aa87eec91a114` |

---

## 2. Standalone Reconciliation (7 Upgraded `TV20` Combos + `TV28_V2_H8` Anchor)

Window 3Y: `2023-11-01` → `2026-10-31` | Forward: `2026-11-01T00:00:00Z` → `2026-11-27T01:47:00Z`.

| Combo | Symbol / TF | Orig TP → New TP (SL) | Entries (Exp / Act) | BASE Net Own (Exp / Act / Diff) | BASE PF (Exp / Act) | STRESS Net Own (Exp / Act / Diff) | STRESS PF (Exp / Act) | FWD Entries / Net Own (Exp / Act) | Status |
|---|---|---|---:|---:|---:|---:|---:|---:|:---:|
| `TV03` (`TV03_V2_1_TP`) | `BTC-USDT` / `H4` | `3.00% → 2.40%` (`1.50%`) | `67 / 67` | `+28.17547160` / `+28.17547160` (`+3.2e-14`) | `1.545742 / 1.545742` | `+26.17787200` / `+26.17787200` (`-9.6e-14`) | `1.497784 / 1.497784` | `0 / +0.00000000` (`+0.00000000`) | **PASS** |
| `TV07` (`TV07_V2_1_TP`) | `ETH-USDT` / `H4` | `3.60% → 4.50%` (`1.50%`) | `105 / 105` | `+52.24003645` / `+52.24003645` (`+7.1e-14`) | `1.473348 / 1.473348` | `+43.07386962` / `+43.07386962` (`+5.0e-09`) | `1.377632 / 1.377632` | `3 / -4.85894045` (`-4.85894045`) | **PASS** |
| `TV08` (`TV08_V2_1_TP`) | `ETH-USDT` / `H4` | `3.60% → 4.50%` (`1.50%`) | `248 / 248` | `+125.33231549` / `+125.33231549` (`-4.2e-09`) | `1.483056 / 1.483056` | `+105.89094962` / `+105.89094962` (`-3.7e-09`) | `1.395621 / 1.395621` | `6 / +8.27446090` (`+8.27446090`) | **PASS** |
| `TV09` (`TV09_V2_1_TP`) | `ETH-USDT` / `H4` | `3.00% → 3.75%` (`1.50%`) | `188 / 188` | `+56.73492603` / `+56.73492603` (`-5.0e-09`) | `1.293453 / 1.293453` | `+45.88858275` / `+45.88858275` (`-1.4e-13`) | `1.231219 / 1.231219` | `1 / -1.62106015` (`-1.62106015`) | **PASS** |
| `TV12` (`TV12_V2_1_TP`) | `BTC-USDT` / `H8` | `3.00% → 3.75%` (`1.25%`) | `148 / 148` | `+45.51120039` / `+45.51120039` (`-2.9e-09`) | `1.340590 / 1.340590` | `+25.97167381` / `+25.97167381` (`+6.9e-10`) | `1.184405 / 1.184405` | `1 / +3.72361537` (`+3.72361537`) | **PASS** |
| `TV20` (`TV20_V2_1_TP`) | `BTC-USDT` / `H12` | `3.00% → 3.75%` (`1.25%`) | `101 / 101` | `+32.84338060` / `+32.84338060` (`-4.7e-09`) | `1.362433 / 1.362433` | `+29.74343205` / `+29.74343205` (`+2.6e-09`) | `1.320875 / 1.320875` | `1 / +3.68711538` (`+3.68711538`) | **PASS** |
| `TV24` (`TV24_V2_1_TP`) | `ETH-USDT` / `H12` | `3.60% → 4.50%` (`1.50%`) | `112 / 112` | `+52.74628743` / `+52.74628743` (`-1.6e-09`) | `1.449632 / 1.449632` | `+49.37137210` / `+49.37137210` (`-1.2e-10`) | `1.413112 / 1.413112` | `5 / -2.09570015` (`-2.09570015`) | **PASS** |
| `TV28` (`TV28_V2_H8`, anchor) | `BTC-USDT` / `H8` | `3.00%` unchanged (`1.20%`) | `129 / 129` | `+40.19922052` / `+40.19922052` (`-1.8e-13`) | `1.385893 / 1.385893` | `+36.31371380` / `+36.31371380` (`-4.8e-13`) | `1.340861 / 1.340861` | `2 / +1.64394018` (`+1.64394018`) | **PASS** |

---

## 3. Full Portfolio `PORT_FINAL_TP_ONLY` (43 Combos) & Forward Reconciliation

| Scope & Profile | Metric | Expected | Actual | Diff | Tol | Status |
|---|---|---:|---:|---:|---:|:---:|
| **3Y `BASE`** | Entries | `6,560` | `6,560` | `0` | `0` | **PASS** |
| **3Y `BASE`** | Net Own (`$`) | `+1691.79053319` | `+1691.79053319` | `-3.94e-09` | `1e-4` | **PASS** |
| **3Y `BASE`** | Net VWAP (`$`) | `+1696.27118119` | `+1696.27118119` | `-3.91e-09` | `1e-4` | **PASS** |
| **3Y `BASE`** | M2M MaxDD (`$`) | `180.0864` | `180.0864` | `0.0` | `0.005` | **PASS** |
| **3Y `BASE`** | M2M MaxDD (`%`) | `9.14%` | `9.14%` | `0.0` | `0.01` | **PASS** |
| **3Y `BASE`** | Closed MaxDD Own (`$`) | `158.047897` | `158.047897` | `-5.21e-08` | `1e-4` | **PASS** |
| **3Y `BASE`** | PF Own / PF VWAP | `1.382829 / 1.448512` | `1.382829 / 1.448512` | `-4.34e-07 / -1.67e-08` | `1e-4` | **PASS** |
| **3Y `BASE`** | Peak Concurrent Legs | `37` | `37` | `0` | `0` | **PASS** |
| **3Y `STRESS`** | Entries | `6,560` | `6,560` | `0` | `0` | **PASS** |
| **3Y `STRESS`** | Net Own (`$`) | `+1242.83532158` | `+1242.83532158` | `-3.55e-09` | `1e-4` | **PASS** |
| **3Y `STRESS`** | Net VWAP (`$`) | `+1246.91341858` | `+1246.91341858` | `-3.48e-09` | `1e-4` | **PASS** |
| **3Y `STRESS`** | M2M MaxDD (`$`) | `196.1109` | `196.1109` | `0.0` | `0.005` | **PASS** |
| **3Y `STRESS`** | M2M MaxDD (`%`) | `11.21%` | `11.21%` | `0.0` | `0.01` | **PASS** |
| **3Y `STRESS`** | Closed MaxDD Own (`$`) | `186.989494` | `186.989494` | `+4.07e-07` | `1e-4` | **PASS** |
| **3Y `STRESS`** | PF Own / PF VWAP | `1.268809 / 1.315861` | `1.268809 / 1.315861` | `+4.60e-07 / +4.73e-07` | `1e-4` | **PASS** |
| **3Y `STRESS`** | Peak Concurrent Legs | `36` | `36` | `0` | `0` | **PASS** |
| **Forward `BASE`** (`11-01 → 11-27`) | Entries | `175` | `175` | `0` | `0` | **PASS** |
| **Forward `BASE`** | Net Own (`$`) | `+108.02925846` | `+108.02925846` | `-3.10e-09` | `1e-5` | **PASS** |
| **Forward `BASE`** | M2M MaxDD (`$ / %`) | `28.7270 / 2.85%` | `28.7270 / 2.85%` | `0.0 / 0.0` | `0.005 / 0.01` | **PASS** |
| **Forward `STRESS`** (`11-01 → 11-27`) | Entries | `175` | `175` | `0` | `0` | **PASS** |
| **Forward `STRESS`** | Net Own (`$`) | `+94.64883693` | `+94.64883693` | `+3.33e-09` | `1e-5` | **PASS** |
| **Forward `STRESS`** | M2M MaxDD (`$ / %`) | `29.9498 / 2.97%` | `29.9498 / 2.97%` | `0.0 / 0.0` | `0.005 / 0.01` | **PASS** |

- **Total Reconciliation Checks:** `82 / 82 PASS`
- **Accounting Invariant Equity Diff:** `BASE = 3.91e-10`, `STRESS = 1.39e-10` (`< 1e-6` PASS)
- **Skipped / Rejected Signals:** `0` / `0`

---

## 4. Dry-Run Signal & TP/SL Verification (`data/bingx` Closed Bars)

- **Latest BingX M1 UTC:** `2026-11-27T01:47:00Z` (không rò rỉ nến đang mở; `last_closed_close_ms <= max_m1_ms + 59,999` PASS cho `H4/H8/H12`).
- **Distinct `combo_id`s (V2.1 TP):** `[3001, 3003, 3004, 3005, 3007, 3012, 3016]` (`0` va chạm với `1..43`, `1001..1020`, `2001..2020`).
- **`unique_event_ids = True`**, **`unique_signal_ids = True`**, **`verify_live_signals_against_combos = PASS`**.

| Combo | Version Key | Last Closed Bar Open UTC | 2026 YTD / Fwd Signals | Latest Verified `event_id` | Side | Entry | TP (Exact %) | SL (Exact %) |
|---|---|---|---:|---|---|---:|---:|---:|
| `TV03` | `TV03_V2_1_TP` | `2026-11-26T20:00:00Z` | `24 / 0` | `BTC-USDT\|H4\|TV03_V2_1_TP\|SELL\|1792670399999` | `SELL` | `81699.80` | `79739.0048` (`2.40%`) | `82925.2970` (`1.50%`) |
| `TV07` | `TV07_V2_1_TP` | `2026-11-26T20:00:00Z` | `32 / 3` | `ETH-USDT\|H4\|TV07_V2_1_TP\|BUY\|1795449599999` | `BUY` | `1772.86` | `1852.6387` (`4.50%`) | `1746.2671` (`1.50%`) |
| `TV08` | `TV08_V2_1_TP` | `2026-11-26T20:00:00Z` | `74 / 6` | `ETH-USDT\|H4\|TV08_V2_1_TP\|SELL\|1795190399999` | `SELL` | `1654.35` | `1579.90425` (`4.50%`) | `1679.16525` (`1.50%`) |
| `TV09` | `TV09_V2_1_TP` | `2026-11-26T20:00:00Z` | `53 / 1` | `ETH-USDT\|H4\|TV09_V2_1_TP\|SELL\|1795593599999` | `SELL` | `1640.10` | `1578.59625` (`3.75%`) | `1664.7015` (`1.50%`) |
| `TV12` | `TV12_V2_1_TP` | `2026-11-26T16:00:00Z` | `43 / 1` | `BTC-USDT\|H8\|TV12_V2_1_TP\|SELL\|1793951999999` | `SELL` | `75568.40` | `72734.585` (`3.75%`) | `76513.005` (`1.25%`) |
| `TV20` | `TV20_V2_1_TP` | `2026-11-26T12:00:00Z` | `31 / 1` | `BTC-USDT\|H12\|TV20_V2_1_TP\|SELL\|1794139199999` | `SELL` | `75944.00` | `73096.10` (`3.75%`) | `76893.30` (`1.25%`) |
| `TV24` | `TV24_V2_1_TP` | `2026-11-26T12:00:00Z` | `37 / 5` | `ETH-USDT\|H12\|TV24_V2_1_TP\|SELL\|1795219199999` | `SELL` | `1622.03` | `1549.03865` (`4.50%`) | `1646.36045` (`1.50%`) |

---

## 5. `pytest` Output Log

```text
$ .\.venv\Scripts\pytest.exe -v autotrade_v3/tests/test_plus43_v2_1_tp_only.py autotrade_v3/tests/test_tv28_v2_h8.py autotrade_v3/tests/test_v3_plus31_research.py autotrade_v3/tests/test_lab_dashboard_one_page.py
============================= test session starts =============================
platform win32 -- Python 3.12.8, pytest-9.2.0, pluggy-1.7.0 -- D:\AutoTrade\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: D:\AutoTrade
configfile: pyproject.toml
plugins: anyio-4.16.0
collecting ... collected 15 items

autotrade_v3/tests/test_plus43_v2_1_tp_only.py::test_plus43_v2_1_tp_registry_and_manifest_invariants PASSED [  6%]
autotrade_v3/tests/test_plus43_v2_1_tp_only.py::test_plus43_v2_1_tp_keys_zero_collision_and_historical_preservation PASSED [ 13%]
autotrade_v3/tests/test_plus43_v2_1_tp_only.py::test_plus43_v2_1_tp_dry_run_signals_and_no_unclosed_leakage PASSED [ 20%]
autotrade_v3/tests/test_tv28_v2_h8.py::test_tv28_v2_registry_and_untouched_freeze PASSED [ 26%]
autotrade_v3/tests/test_tv28_v2_h8.py::test_tv28_v2_keys_and_peer_isolation PASSED [ 33%]
autotrade_v3/tests/test_tv28_v2_h8.py::test_tv28_v2_h8_alignment_and_causality PASSED [ 40%]
autotrade_v3/tests/test_v3_plus31_research.py::test_tv31_freeze_and_is_parity PASSED [ 46%]
autotrade_v3/tests/test_v3_plus31_research.py::test_database_reconciles_54_portfolio PASSED [ 53%]
autotrade_v3/tests/test_v3_plus31_research.py::test_plus44_registry_and_database_reconcile_without_overwriting_plus54 PASSED [ 60%]
autotrade_v3/tests/test_lab_dashboard_one_page.py::test_live_status_badge_threshold_60 PASSED [ 66%]
autotrade_v3/tests/test_lab_dashboard_one_page.py::test_main_dashboard_renders_plus44_at_root PASSED [ 73%]
autotrade_v3/tests/test_lab_dashboard_one_page.py::test_plus44_list_runs_includes_base_and_stress PASSED [ 80%]
autotrade_v3/tests/test_lab_dashboard_one_page.py::test_plus44_combo_table_uses_own_entry_only PASSED [ 86%]
autotrade_v3/tests/test_lab_dashboard_one_page.py::test_mutation_fails_verify_and_hides_numbers PASSED [ 93%]
autotrade_v3/tests/test_lab_dashboard_one_page.py::test_serve_command_dark_and_loopback PASSED [100%]

============================= 15 passed in 10.75s =============================
```

---

## 6. Changed & Added Files / Baseline Hygiene

- **Modified tracked file (additive only for `V2_1_TP` key table):**
  - `autotrade_v3/reconcile_v3/keys.py` (`build_combo_table()` loads `TV20_PLUS43_V2_1_TP_REGISTRY.csv` at `combo_id` `3001+`, keeping all `20FINAL`, `PRUNE23`, `TV31`, `TV28_V2_H8` keys intact).
- **New deliverables created for `PLUS43_V2_1_TP_ONLY`:**
  - `research/v3_lab_11_plus31/registry_v2/TV20_PLUS43_V2_1_TP_REGISTRY.csv`
  - `research/v3_lab_11_plus31/registry_v2/PLUS43_V2_1_TP_MANIFEST.json`
  - `research/v3_lab_11_plus31/run_plus43_v2_1_tp_only.py`
  - `research/v3_lab_11_plus31/outputs/plus43_v2_1_tp_only/*` (`PORTFOLIO_SUMMARY_ALL_SIGNALS.csv`, `PORTFOLIO_SPLITS_DEV_VAL_HOLDOUT.csv`, `PORTFOLIO_YEARLY_ALL_SIGNALS.csv`, `PER_COMBO_PLUS43_ALL_SIGNALS.csv`, `DAILY_EQUITY_ALL_SIGNALS.csv`, `FORWARD_POST_3Y.json`, `RUN_MANIFEST.json`)
  - `autotrade_v3/tests/test_plus43_v2_1_tp_only.py`
  - `reports/plus43_v2_1_tp_only/plus43_v2_1_tp_verification.json`
  - `reports/plus43_v2_1_tp_only/PLUS43_V2_1_TP_UPGRADE_REPORT.md`
- **Protected baselines verified untouched (mtimes & `git status`):**
  - `reports/v3_lab_11_funding_3y/LOCKED_CONFIG_PRUNE23.json` (untouched)
  - `research/v3_lab_11_plus31/freeze/TV31_SELECTED_REGISTRY.csv` (untouched, SHA-256 verified)
  - `research/v3_lab_11_plus31/outputs/plus43_all_signals/` (untouched)
  - `research/v3_lab_11_plus31/outputs/plus43_v2_tv28_h8/` (untouched)
  - Pre-existing uncommitted workspace files outside this task were NOT altered or reverted.

```text
$ git diff --stat
 autotrade_v3/dashboard/one_page.py      |   96 +-
 autotrade_v3/dashboard/plus44_source.py |   18 +
 autotrade_v3/reconcile_v3/__main__.py   |   18 +
 autotrade_v3/reconcile_v3/keys.py       |  107 +-
 autotrade_v3/tests/test_reconcile_v3.py |   17 +
 evidence/v3-17a/reconcile_report.html   |  269 +++-
 evidence/v3-17a/reconcile_report.json   | 2678 ++++++++++++++++++++++++++++---
 evidence/v3-17a/verify_result.json      |    2 +-
 evidence/v3-18/verify_result.json       |    8 +-
 9 files changed, 2918 insertions(+), 295 deletions(-)
```
