# Đặc tả Giao diện Báo cáo MT5-Style — AutoTrade V3 Research Lab (V3-16, Issue #102 AC1)

- **Phiên bản đặc tả:** `v1.0-draft`
- **Nhánh:** `feat/v3-16-mt5-report-spec`
- **Trạng thái:** Chờ Owner duyệt (chưa code báo cáo động)
- **Nguyên tắc cốt lõi (Issue #102):**
  1. **Một nguồn sự thật (Single Source of Truth):** Mọi con số hiển thị phải tính trực tiếp từ ledger/artifacts của run (`trades.parquet`, `snapshots.parquet`, `signals.parquet`, `signal_decisions.parquet`, `analysis.json`, `run_manifest.json`), KHÔNG tự nhập hoặc sao chép số mẫu.
  2. **Không làm tròn trong dữ liệu gốc:** Làm tròn chỉ thực hiện ở lớp hiển thị UI; JSON export giữ nguyên giá trị chính xác (`unrounded`) kèm đơn vị và nguồn.
  3. **Thiếu dữ liệu = `N/A` kèm lý do:** Không bịa số, không nội suy ngầm. Nếu Mode A/B không có `snapshots.parquet`, ẩn đường Equity MTM và hiển thị `N/A` kèm lý do.
  4. **Đánh dấu bản mẫu:** Mọi ô hoặc biểu đồ chưa nối nguồn thật trong bản mock phải gắn nhãn `SAMPLE LAYOUT`.
  5. **Kiểm định tự động:** Báo cáo thật ở bước 2 phải vượt qua `autotrade_v3/tools/verify_report.py` (số lệch artifact = `FAIL`).

---

## 1. Chiến lược Mobile-First & Breakpoints

Thiết kế theo hướng **mobile-first** (CSS Grid + Flexbox, không tràn ngang trên màn hình 375px–390px):

| Breakpoint | Màn hình | Bố cục lưới (Grid) | Bảng dữ liệu |
| :--- | :--- | :--- | :--- |
| `< 640px` (Default / Mobile) | Điện thoại | 1 cột (`grid-template-columns: 1fr`), banner xếp dọc | Cuộn ngang nội bộ (`overflow-x: auto`) + ghim cột định danh đầu |
| `640px – 1023px` (Tablet) | Máy tính bảng | 2 cột cho thẻ KPI và khối MT5 Results | Cuộn ngang khi cần, font `13px` |
| `>= 1024px` (Desktop) | Màn hình lớn | 4 cột cho hàng thẻ KPI đầu; 2 cột cho khối MT5 Results & Symbol | Hiển thị đầy đủ cột, căn phải mọi cột số (`tabular-nums`) |

### Quy tắc Typography & Màu sắc
- **Font số liệu:** `font-variant-numeric: tabular-nums` cho toàn bộ bảng và thẻ KPI để thẳng hàng cột.
- **Màu trạng thái:**
  - `IDEAL` banner: nền đỏ đậm (`#991b1b` / `#dc2626`), chữ trắng, badge cảnh báo không phản ánh chi phí thực tế.
  - `BASE` banner: nền hổ phách/vàng (`#92400e` / `#d97706`), chữ trắng.
  - `STRESS` banner: nền xanh dương/xám đậm (`#1e3a8a` / `#1d4ed8`), chữ trắng.
  - Lãi dương: `#15803d` (nền nhạt `#dcfce7`); Lỗ âm: `#b91c1c` (nền nhạt `#fee2e2`); `N/A`: `#64748b` (xám trung tính + tooltip/chú thích lý do); `SAMPLE LAYOUT`: badge cam/tím viền nét đứt.

---

## 2. Thứ tự 12 Khối Bố cục Chuẩn

### Khối 1: Banner Chế độ Chi phí & Quản trị Dữ liệu (Header Banner)
- **Vị trí:** Ghim ngay đầu trang, nổi bật nhất.
- **Thành phần bắt buộc:**
  1. **Nhãn Cost Profile lớn:** `FEE MODE: IDEAL` / `FEE MODE: BASE` / `FEE MODE: STRESS` (cùng `cost_profile_hash` rút gọn 8 ký tự).
     - **Bắt buộc với `IDEAL`:** Luôn gắn nhãn **ĐỎ** kèm cảnh báo: *"MÔ PHỎNG LÝ TƯỞNG — slippage = 0, spread = 0, funding = NOT_MODELED. Không dùng để đánh giá lợi nhuận thực tế."*
  2. **Thông tin Run:** `run_id`, `execution_mode` (`ALL_SIGNALS` / `ONE_TRADE_PER_COMBO` / `BINGX_PORTFOLIO`), `ambiguity_policy`, `time_window` (UTC), `symbols`, `git_commit` (8 ký tự), `dataset_hash`, `validation_status` (`VERIFIED` / `FAILED`).
  3. **Nhãn Kỳ dữ liệu (Governance Badge — theo Codex AT2-002):**
     - Khoảng `2023-11-01 .. 2025-11-01`: `DEVELOPMENT (24M)`.
     - Khoảng `2026-01-01 .. 2026-11-01` (hoặc `2025-11-01 .. 2026-11-01`): Bắt buộc ghi **`VALIDATION (đã dùng để chọn biến thể)`**. Tuyệt đối **KHÔNG** dùng chữ `OOS`, `Out-of-Sample`, hay `Holdout` cho dữ liệu 2026.
     - Dữ liệu forward từ `2026-11-14` trở đi (với config đã đóng băng trước khi chạy): `FORWARD EVIDENCE (Frozen Config)`.
  4. **Nút hành động:** `Export AI JSON` (tải file JSON đúng chuẩn `report_schema_v1.json` để AI/Codex đọc trực tiếp) + nút `Copy Run Metadata`.

### Khối 2: Hàng 4 Thẻ KPI Tổng quan (Account / Cost Reality / Risk / Volume)
Xếp 4 thẻ (1 cột mobile, 2 cột tablet, 4 cột desktop):

1. **Thẻ Account:**
   - Initial Capital (`$1,000.00`)
   - Final Balance (`$1,282.19`)
   - Final Equity MTM (`$1,282.19` hoặc `N/A — Không có M2M snapshots`)
   - Max Used Margin / Min Free Margin / Min Margin Level (`$` / `$` / `%` hoặc `N/A` nếu không có `AccountLedger` snapshots)
2. **Thẻ Cost Reality:**
   - Total Fees (`$414.95`)
   - Fee Drag (`Fees / Gross PnL`: `59.52%`)
   - Break-even Slippage / Side (`0.0340%` = `3.40 bps`)
   - Slippage + Spread + Funding (`$0.00` ở IDEAL; ghi rõ `funding: NOT_MODELED` nếu chưa mô hình hóa)
3. **Thẻ Risk:**
   - Net Profit Factor (Entry-level & Leg-level: `1.15 / 1.14`)
   - Recovery Factor (`Net PnL / Max DD $`: `6.17` theo Closed DD, `5.76` theo M2M DD)
   - Daily Sharpe (`rf = 0, √365`: `1.48`)
   - Max Drawdown (`$45.71 / 3.46%` Closed; `$49.01 / 3.70%` M2M)
4. **Thẻ Volume (Tách rõ đơn vị — KHÔNG gộp mơ hồ):**
   - Total Signals (`5,984`)
   - Accepted Entries (`4,149`)
   - Fill Rate (`Entries / Signals`: `69.33%`)
   - Total Legs (`6,265`) + Skipped/Rejected (`1,835` concurrency / `0` margin)

### Khối 3: Biểu đồ Balance & Equity MTM
- **Hai đường trên cùng trục thời gian (hoặc toggle theo thứ tự Entry / Thời gian UTC):**
  - Đường **Balance (Realized)** (màu xanh dương đậm `#2563eb`): vốn đã chốt sau từng leg đóng.
  - Đường **Equity MTM** (màu cam/lục nét mảnh): lấy từ `snapshots.parquet` (chỉ hiển thị cho `BINGX_PORTFOLIO` hoặc run có M2M snapshots).
  - **Quy tắc thiếu snapshot:** Nếu run không có `snapshots.parquet`, **ẩn hoàn toàn** đường Equity MTM và hiển thị hộp thông báo `Equity MTM: N/A (Chế độ này không ghi M2M snapshots)`.
- Trên bản mock tĩnh: vẽ SVG minh họa có gắn nhãn `SAMPLE LAYOUT` ở góc biểu đồ (số liệu trục lấy đúng biên thật `965.78` .. `1,324.65`).

### Khối 4: Biểu đồ Drawdown `$` và `%` (Tách 2 biểu đồ riêng)
- **Panel A — Drawdown ($):** Vùng đỏ biểu diễn mức sụt giảm tuyệt đối tính bằng `$` từ đỉnh (`Closed DD` max `$45.71`; `M2M DD` max `$49.01` nếu có snapshots).
- **Panel B — Drawdown (%):** Vùng đỏ cam biểu diễn mức sụt giảm tương đối tính bằng `%` từ đỉnh (`Closed DD` max `3.46%`; `M2M DD` max `3.70%` nếu có snapshots).
- **Lý do tách riêng:** Tránh gộp hai đơn vị `$` và `%` lên cùng một biểu đồ gây hiểu nhầm quy mô rủi ro khi tài khoản tăng trưởng.

### Khối 5: Khối Results Kiểu MT5 (Strategy Tester Summary)
Bố cục bảng 2 cột (Desktop) / 1 cột (Mobile), mô phỏng trực tiếp bố cục Strategy Tester Report của MT5 nhưng chuẩn hóa đơn vị lệnh:
- **Lợi nhuận & Hệ số:**
  - Total Net Profit (`$282.19` / ROI `28.22%`)
  - Gross Profit (`$2,162.39` entry-level — tổng các entry thắng sau phí; kèm chú thích phân biệt với `Gross PnL trước phí = $697.14`)
  - Gross Loss (`-$1,880.20` entry-level)
  - Profit Factor (`1.15` entry-level / `1.14` leg-level)
  - Expected Payoff (`$0.0680 / entry` | `$0.0450 / leg`)
  - Recovery Factor (`6.17` Closed | `5.76` M2M)
  - Sharpe Ratio (`1.48` — Daily balance, `rf = 0`, `√365`)
- **Drawdown kiểu MT5:**
  - Balance Drawdown Absolute (`$34.22` / `3.42%` so với Initial Capital `$1,000`)
  - Balance Drawdown Maximal (`$45.71` — Closed Balance DD lớn nhất)
  - Balance Drawdown Relative (`3.46%` — Closed Balance DD % lớn nhất; kèm M2M Max DD `$49.01 / 3.70%`)
- **Thống kê Lệnh (rõ đơn vị):**
  - Total Signals (`5,984`), Accepted Entries (`4,149`, Fill `69.33%`), Total Legs (`6,265`)
  - Profit Entries (`1,695` — `40.85%`), Loss Entries (`2,454` — `59.15%`), Breakeven (`0`)
  - Largest Profit Entry (`$3.3041`), Largest Loss Entry (`-$1.9133`)
  - Average Profit Entry (`$1.2757`), Average Loss Entry (`-$0.7661`)

### Khối 6: Phân tích LONG / SHORT
Bảng so sánh 2 chiều (`BUY / LONG` vs `SELL / SHORT`) ở cấp **Accepted Entries** (và phụ chú Legs):
- Long Entries (`2,388`), Long Won (`975` — `40.83%`), Long Net PnL (`$154.91`), Long Profit Factor (`CẦN THÊM` trong `metrics.py`).
- Short Entries (`1,761`), Short Won (`720` — `40.89%`), Short Net PnL (`$127.28`), Short Profit Factor (`CẦN THÊM` trong `metrics.py`).

### Khối 7: Chuỗi Thắng / Thua (Consecutive Streaks)
Bảng 6 chỉ số chuẩn MT5 tính trên chuỗi **Accepted Entries** sắp xếp theo `(exit_time_ms, entry_time_ms, trade_id)`:
- Maximum Consecutive Wins (count & `$`)
- Maximum Consecutive Losses (count & `$`)
- Maximal Consecutive Profit (`$` & count)
- Maximal Consecutive Loss (`$` & count)
- Average Consecutive Wins (count)
- Average Consecutive Losses (count)
*(Hiện tại `analysis/metrics.py` chưa tính nhóm chỉ số này → gắn trạng thái `CẦN THÊM`, trong mock gắn badge `SAMPLE LAYOUT`).*

### Khối 8: Bảng Độ nhạy Chi phí (Cost Sensitivity Grid)
- Các mốc chuẩn: `0 bps (IDEAL)` | `1 bps` | `2 bps (BASE)` | `3.4 bps (Break-even)` | `5 bps (STRESS)`.
- **Quy tắc bắt buộc:** Chỉ hiển thị số từ các run đã chạy thực tế trên dữ liệu M1 thật (hoặc bảng tái hiện `sensitivity_analysis` đã kiểm chứng). **Mọi ô chưa có run thực tế phải hiển thị `N/A`** kèm lý do rõ ràng (ví dụ: `Chưa chạy run thực tế cho mức slippage này`). Tuyệt đối không nội suy hay bịa số âm/dương vô nghĩa.

### Khối 9: Bảng 20 Combo Hợp nhất (Mode C Attribution Bridge)
Bảng sắp xếp được theo PnL / Fill rate / Combo:
- Cột bắt buộc: `Combo` | `Symbol` | `TF` | `Signals` | `Entries` | `Fill %` | `Legs` | `Leg WR %` | `Own-entry PnL ($)` | `Portfolio Net PnL ($)` | `Delta / basis_shift ($)` | `Fees ($)` | `Net PF (Leg)`
- Chân bảng (Footer Total): Tổng `Own-entry PnL` = Tổng `Portfolio Net PnL` = `$282.19`, Tổng `Delta (basis_shift)` = `$0.00` (bảo toàn kế toán Mode C).

### Khối 10: Phân tách theo Symbol (BTC-USDT vs ETH-USDT)
2 thẻ đối xứng (mobile xếp dọc, desktop 2 cột) + bảng tổng hợp:
- Signals, Entries, Fill %, Legs, Leg WR %, Gross PnL, Total Fees, Net PnL, Net PF (Trade & Leg), Gross PF (Trade & Leg).

### Khối 11: Báo cáo Theo Tháng (Monthly Breakdown)
- Bảng đầy đủ các tháng `YYYY-MM` (`2023-12` .. `2026-10`): `Tháng (UTC)` | `Kỳ quản trị` | `Entries` | `Legs` | `Leg WR %` | `Gross PnL ($)` | `Fees ($)` | `Net PnL ($)` | `Net PF (Leg)`.
- Các tháng thuộc năm 2026 gắn nhãn kỳ `VALIDATION (chọn biến thể)`, không gắn `OOS`/`Holdout`.
- Kèm biểu đồ cột lãi/lỗ theo tháng (xanh/đỏ).

### Khối 12: Bảng So sánh 3 Chế độ (`ALL_SIGNALS` / `ONE_TRADE_PER_COMBO` / `BINGX_PORTFOLIO`)
Bảng đối chiếu trực tiếp 3 chế độ trên cùng bộ dữ liệu và cùng `cost_profile` + `ambiguity_policy`:
- Cột: `Mode` | `Validation` | `Signals` | `Entries` | `Legs` | `Gross PnL ($)` | `Fees ($)` | `Net PnL ($)` | `ROI %` | `Leg WR %` | `Net PF (Leg)` | `Closed DD %` | `M2M DD %`
- Nếu một mode không có M2M snapshots (`ALL_SIGNALS`, `ONE_TRADE_PER_COMBO`), cột `M2M DD %` bắt buộc ghi `N/A (Không có M2M snapshots)`.

---

## 3. Hành vi Xuất Gói Dữ liệu AI (`Export AI JSON`)
- Nút `Export AI JSON` ở Banner đầu trang xuất file `<run_id>_report_v1.json` tuân thủ nghiêm ngặt `evidence/v3-16/report_schema_v1.json`.
- Mọi trường số trong JSON đều có cấu trúc `{ "value": <unrounded_or_null>, "unit": "...", "source": "...", "status": "AVAILABLE|NEEDS_IMPL|N/A", "reason": "..." }` để AI/Codex đọc không bao giờ nhầm giữa `0` thật và dữ liệu thiếu (`null` + `N/A`).
