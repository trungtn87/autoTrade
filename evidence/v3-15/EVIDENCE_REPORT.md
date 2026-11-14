# EVIDENCE REPORT — V3-15: LOCAL RESEARCH DASHBOARD

## A. Thông tin triển khai
- **Task**: AutoTrade V3 Research Lab — V3-15: Local Research Dashboard
- **Branch**: `feat/v3-15-dashboard`
- **Base SHA (`feat/v3-14-run-store`)**: `66a46c6d4d3f9ffb41ac835acf5fdb85d014c110`
- **Code Head SHA**: `011ea68c3e221c61744b29c9f40130971b01d2d8`
- **Commit SHAs**:
  - `09fb099` `feat(v3-15): add read-only dashboard queries, notes store, and unit tests`
  - `c780531` `feat(v3-15): add whitelisted dashboard actions and CLI ops`
  - `f96f0f9` `feat(v3-15): add Run Detail dashboard page`
  - `4c48efa` `feat(v3-15): add Signal Explorer dashboard page`
  - `08c9013` `feat(v3-15): add Compare dashboard page`
  - `8d63c14` `feat(v3-15): add Datasets dashboard page`
  - `b18e145` `feat(v3-15): add Strategies dashboard page`
  - `ea3164b` `feat(v3-15): add New Run dashboard page`
  - `654b69e` `feat(v3-15): add Research Log page and dashboard app entrypoint`
  - `16d8799` `chore(v3-15): add offline streamlit config, run_dashboard.bat, requirements, and AppTest suite`
  - `242ea8c` `test(v3-15): add integration, mutation, offline tests, docs, and evidence collector`
  - `011ea68` `fix(v3-15): query v3_m1 via load_m1_from_catalog and finalize integration checks`
- **Environment**: Windows (`Microsoft Windows NT 10.0.19045.0`), Python `3.12.8`, `streamlit==1.67.0`, `plotly==7.1.0`, `mypy==2.4.1`
- **Scope**:
  - Added `autotrade_v3/dashboard/` (`__init__.py`, `queries.py`, `notes.py`, `actions.py`, `_cli_ops.py`, `ui_shared.py`, `app.py`, `pages/p01_datasets.py` .. `pages/p07_research_log.py`)
  - Added `autotrade_v3/requirements-dashboard.txt`, `.streamlit/config.toml`, `run_dashboard.bat`, `docs/autotrade-v3/15-dashboard.md`
  - Added tests: `test_dashboard_queries_and_actions.py`, `test_dashboard_apptest.py`, `test_dashboard_mutations.py`, `test_dashboard_offline.py`, `test_dashboard_integration.py`
  - Added evidence bundle in `evidence/v3-15/`
  - `autotrade_v2/` untouched (`git diff --stat feat/v3-14-run-store..HEAD -- autotrade_v2` is empty)
- **CI / Static Checks on `011ea68`**:
  - `ruff check autotrade_v3/dashboard autotrade_v3/tests/test_dashboard_*.py`: `All checks passed!` (exit 0)
  - `python -m compileall -q autotrade_v3/dashboard`: exit 0
  - `python -m mypy autotrade_v3/dashboard --ignore-missing-imports --follow-imports=silent`: `Success: no issues found in 14 source files` (exit 0)

---

## B. Các hạng mục đã hoàn thành

| Task ID | Công việc | Trạng thái | File / Commit chứng minh |
|---|---|---|---|
| B1 | Tạo nhánh `feat/v3-15-dashboard` từ `feat/v3-14-run-store` (`66a46c6d`) | VERIFIED | `git_state.txt` |
| B2 | Bảng ánh xạ phần tử giao diện -> `queries.py` -> nguồn store | VERIFIED | `evidence/v3-15/ui_map.md`, `docs/autotrade-v3/15-dashboard.md` (`242ea8c`) |
| B3 | Lớp truy vấn thuần chỉ đọc `queries.py` + `notes.py` (`notes.duckdb`) | VERIFIED | `autotrade_v3/dashboard/queries.py`, `notes.py` (`09fb099`, `011ea68`) |
| B4 | Lớp hành động danh sách trắng `actions.py` + `_cli_ops.py` | VERIFIED | `autotrade_v3/dashboard/actions.py`, `_cli_ops.py` (`c780531`) |
| B5.1 | Màn hình (1) Datasets | VERIFIED | `autotrade_v3/dashboard/pages/p01_datasets.py` (`8d63c14`) |
| B5.2 | Màn hình (2) Strategies | VERIFIED | `autotrade_v3/dashboard/pages/p02_strategies.py` (`b18e145`) |
| B5.3 | Màn hình (3) New Run | VERIFIED | `autotrade_v3/dashboard/pages/p03_new_run.py` (`ea3164b`) |
| B5.4 | Màn hình (4) Run Detail | VERIFIED | `autotrade_v3/dashboard/pages/p04_run_detail.py` (`f96f0f9`) |
| B5.5 | Màn hình (5) Signal Explorer | VERIFIED | `autotrade_v3/dashboard/pages/p05_signal_explorer.py` (`4c48efa`) |
| B5.6 | Màn hình (6) Compare | VERIFIED | `autotrade_v3/dashboard/pages/p06_compare.py` (`08c9013`) |
| B5.7 | Màn hình (7) Research Log + `app.py` | VERIFIED | `autotrade_v3/dashboard/pages/p07_research_log.py`, `app.py` (`654b69e`) |
| B6 | Cấu hình offline `.streamlit/config.toml`, `run_dashboard.bat`, `requirements-dashboard.txt` | VERIFIED | `16d8799` |
| B7–B9 | Test dữ liệu thật, AppTest (real & empty store), kiểm offline socket-blocking | VERIFIED | `evidence/v3-15/data_tests/`, `apptest.xml`, `offline.log` |
| B10 | Tài liệu `docs/autotrade-v3/15-dashboard.md` | VERIFIED | `docs/autotrade-v3/15-dashboard.md` (`242ea8c`) |

---

## C. Kết quả kiểm thử

| Test ID | Loại test | Lệnh chạy | Kết quả + exit code | Log / Artifact |
|---|---|---|---|---|
| T1 | Static lint (`ruff`) | `ruff check autotrade_v3/dashboard autotrade_v3/tests/test_dashboard_*.py` | `All checks passed!`, exit `0` | — |
| T2 | Bytecode compile | `python -m compileall -q autotrade_v3/dashboard` | exit `0` | — |
| T3 | Type check (`mypy`) | `python -m mypy autotrade_v3/dashboard --ignore-missing-imports --follow-imports=silent` | `Success: no issues found in 14 source files`, exit `0` | — |
| T4 | Dashboard Unit + AppTest + Mutation + Offline | `pytest autotrade_v3/tests/test_dashboard_queries_and_actions.py autotrade_v3/tests/test_dashboard_apptest.py autotrade_v3/tests/test_dashboard_mutations.py autotrade_v3/tests/test_dashboard_offline.py -v` | `28 passed`, exit `0` | `evidence/v3-15/apptest.xml` |
| T5 | Dashboard Integration (real store) | `pytest autotrade_v3/tests/test_dashboard_integration.py -v` | `11 passed`, exit `0` | `evidence/v3-15/data_tests/` |
| T6 | AppTest JUnit (real + empty store) | `pytest autotrade_v3/tests/test_dashboard_apptest.py --junitxml=evidence/v3-15/apptest.xml -v` | `15 passed`, exit `0` | `evidence/v3-15/apptest.xml` |
| T7 | Full V3 Regression Suite | `pytest autotrade_v3/tests --junitxml=evidence/v3-15/junit/all.xml -v` | `381 passed, 0 failed, 0 skipped` (6 pandas FutureWarnings), `1068.02s`, exit `0` | `evidence/v3-15/junit/all.xml`, `evidence/v3-15/pytest_all.log` |
| T8 | Offline Live Server + Socket-blocking | `python evidence/v3-15/collect_evidence.py` | 7/7 pages `exceptions=0`; `streamlit_server_127.0.0.1:8599 http_status=200`; `socket_attempts_count=7` (127.0.0.1 only) | `evidence/v3-15/offline.log` |
| T9 | Screenshot Capture (7 screens) | `python evidence/v3-15/capture_screens.py` | 7/7 PNGs rendered on `R_86b3e8b4c96cd4b6` | `evidence/v3-15/screens/` |

---

## D. Kết quả đối chiếu dữ liệu

1. **Run Detail vs `tools/recompute_metrics.py`** (`evidence/v3-15/data_tests/run_detail.log`):
   - `R_bb110ea243c8f593` (`ALL_SIGNALS`, `PARTIALLY_VERIFIED`, `IDEAL`, `CONSERVATIVE`): `5,984` signals, `5,984` entries, `8,258` legs, `net_pnl=410.700675`, `diff_net=0.000e+00` (`VERIFIED`).
   - `R_d828f33604204021` (`ONE_TRADE_PER_COMBO`, `PARTIALLY_VERIFIED`, `IDEAL`, `CONSERVATIVE`): `5,984` signals, `4,149` entries, `6,265` legs, `net_pnl=282.193484`, `diff_net=0.000e+00` (`VERIFIED`).
   - `R_86b3e8b4c96cd4b6` (`BINGX_PORTFOLIO`, `PARTIALLY_VERIFIED`, `IDEAL`, `CONSERVATIVE`): `5,984` signals, `4,149` entries, `6,265` legs, `net_pnl=282.193484`, `M2M DD=$49.01`, `diff_net=0.000e+00` (`VERIFIED`).

2. **Signal Explorer (30 seeded signals, `seed=20261114`)** (`evidence/v3-15/data_tests/signal_explorer.log`, `signal_explorer_30.csv`):
   - 30/30 signals (covering `AMBIGUOUS`, `TP`, `SL`, `TRAIL` exit groups on `R_bb110ea243c8f593`): `entry_diff=0.00e+00`, `tp_diff=0.00e+00`, `sl_diff=0.00e+00`, `m1_match=True`, `match=True` (`VERIFIED`).

3. **Compare vs `diff_runs`** (`evidence/v3-15/data_tests/compare.log`):
   - `R_bb110ea243c8f593` (`ALL_SIGNALS`) vs `R_d828f33604204021` (`ONE_TRADE_PER_COMBO`):
   - `total_signals=5984`, `net_a=410.70067524980027`, `net_b=282.1934840361231`, `expected_delta_net=-128.50719121367717`, `invariant_diff=0.0`, `unexplained_count=0`, `IDENTICAL=4149`, `ENTRY=1835` (`VERIFIED`).

4. **Datasets / Strategies / New Run / Research Log** (`evidence/v3-15/data_tests/{datasets,strategies,new_run,research_log}.log`):
   - Dataset `3Y_BTC_ETH_M1_HYBRID_PARQUET`: `3,156,480` candles (`BTC-USDT=1,578,240`, `ETH-USDT=1,578,240`), `BINANCE 99.91%`, `BINGX 0.09%`, `dataset_hash=a18fc2e1bf5985945fee6609847cce753cd319da74415dc23d4f4bae231487be`, `status=VERIFIED_PASS`.
   - Strategy `20FINAL@1`: `is_locked=true`, `combo_count=20`, `spec_hash=1145d7f5494a9e9aaa250a48c214acdd8c87bf58869ff6c24861fbfe65033b81`.
   - Cost profiles: only `IDEAL` (`CONFIRMED`) has `selectable_for_real_data=true`; `BASE`/`STRESS` (`UNCONFIRMED`) and `TEST_ONLY` (`TEST_ONLY`) have `selectable_for_real_data=false`.

---

## E. Mutation Tests

Implemented and executed in `autotrade_v3/tests/test_dashboard_mutations.py` (`6/6 passed`):

| Mutation ID | Mô tả đột biến | Kết quả kỳ vọng | Trạng thái |
|---|---|---|---|
| M1 | Bắn lệnh không nằm trong whitelist (`rm -rf`, `curl`, shell metacharacters) vào `actions.py` | Bị từ chối (`ActionValidationError`), không chạy subprocess | VERIFIED |
| M2 | Thử sửa tham số trực tiếp trên baseline đã khóa (`20FINAL@1`) | Bị từ chối (`ActionValidationError`) | VERIFIED |
| M3 | Thử ghi (`INSERT`/`CREATE`) qua kết nối DuckDB của `queries.py` | Bị từ chối (read-only connection error) | VERIFIED |
| M4 | Mở store rỗng / thiếu bảng tùy chọn (`uncertainty.json`, `annotations.parquet`, `notes.duckdb`) | Render trạng thái rỗng, không crash | VERIFIED |
| M5 | Kiểm tra import và chuỗi cấm (`autotrade_v2`, `requests`, `httpx`, `websocket`, `aiohttp`, `supabase`, `bingx`, `.env`, CDN URLs) | 0 vi phạm trong `autotrade_v3/dashboard/` | VERIFIED |
| M6 | Chặn mọi socket ngoài `127.0.0.1` khi khởi chạy dashboard + AppTest | 7/7 trang render 0 exception, 0 kết nối ngoài loopback | VERIFIED |

---

## F. Regression
- Full `autotrade_v3/tests` suite after final code commit `011ea68`: **`381 passed, 0 failed, 0 skipped`** (`evidence/v3-15/junit/all.xml`, `evidence/v3-15/pytest_all.log`).
- Store immutability check (`evidence/v3-15/store_hash_before_after.txt`): `tracked_files=32`, **`changed_files=0`**.
- `autotrade_v2/` unchanged: `git diff --stat feat/v3-14-run-store..HEAD -- autotrade_v2` is empty.

---

## G. Reproducibility
- **Dataset hash (catalog)**: `a18fc2e1bf5985945fee6609847cce753cd319da74415dc23d4f4bae231487be`
- **Dataset hash (run artifact)**: `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`
- **Strategy spec hash (`20FINAL@1`)**: `1145d7f5494a9e9aaa250a48c214acdd8c87bf58869ff6c24861fbfe65033b81`
- **Code SHA**: `011ea68c3e221c61744b29c9f40130971b01d2d8`
- **Golden Run IDs in `runs/lab/store/`**:
  - `R_bb110ea243c8f593` (`ALL_SIGNALS`)
  - `R_d828f33604204021` (`ONE_TRADE_PER_COMBO`)
  - `R_86b3e8b4c96cd4b6` (`BINGX_PORTFOLIO`)
- **Exact Reproduction Commands**:
  ```powershell
  .\.venv\Scripts\python.exe -m pytest autotrade_v3/tests/test_dashboard_queries_and_actions.py autotrade_v3/tests/test_dashboard_apptest.py autotrade_v3/tests/test_dashboard_mutations.py autotrade_v3/tests/test_dashboard_offline.py autotrade_v3/tests/test_dashboard_integration.py -v
  .\.venv\Scripts\python.exe -m pytest autotrade_v3/tests --junitxml=evidence/v3-15/junit/all.xml -v
  .\.venv\Scripts\python.exe evidence/v3-15/collect_evidence.py
  .\.venv\Scripts\python.exe evidence/v3-15/capture_screens.py
  ```
- **Launch Dashboard Locally**:
  ```powershell
  .\run_dashboard.bat
  ```

---

## H. Các vấn đề tồn đọng
1. **Trạng thái `PARTIALLY_VERIFIED` của các golden run trong store**: Các run thật (`R_bb110ea243c8f593`, `R_d828f33604204021`, `R_86b3e8b4c96cd4b6`) mang nhãn `PARTIALLY_VERIFIED` theo quy tắc V3-14/N9 (chưa có đối chiếu live exchange ledger từ Owner và hồ sơ chi phí `BASE`/`STRESS` vẫn ở trạng thái `UNCONFIRMED`). Dashboard hiển thị đúng nhãn trạng thái này trên mọi màn hình và khóa `BASE`/`STRESS` trên New Run.
2. **6 `FutureWarning` từ pandas**: `queries.py` còn 6 `FutureWarning` về `DataFrameGroupBy.apply` / resampling khi chạy full pytest; không ảnh hưởng kết quả số liệu (tất cả `diff_net=0.000e+00`).

---

## I. Tự đánh giá nghiệm thu
- **V3-15 Dashboard Implementation**: **`VERIFIED`** — Toàn bộ 7 màn hình, 3 lớp kiến trúc tách biệt (`queries.py` read-only, `actions.py` whitelist, `pages/` UI-only), offline `127.0.0.1`, đối chiếu số liệu `0.00e+00` sai số, M1–M6 mutation tests, AppTest (store thật + store rỗng), và `381/381` test toàn repo đều đạt.
- **Lưu ý dữ liệu nền**: Các run trong store giữ nguyên nhãn `PARTIALLY_VERIFIED` theo N9; không nâng nhãn giả tạo.
- **Trạng thái PR**: Sẵn sàng để Claude review và Owner nghiệm thu/merge trên nhánh `feat/v3-15-dashboard` (không tự merge theo N10).
