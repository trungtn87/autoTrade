# Ánh xạ Trường Dữ liệu & Công thức Chuẩn — Báo cáo MT5 (V3-16, Issue #102 AC1)

- **Phiên bản:** `v1.0-draft`
- **Nguồn tham chiếu codebase:** `autotrade_v3/analysis/metrics.py`, `autotrade_v3/dashboard/metrics_engine.py`, `autotrade_v3/runstore/equity.py`, `autotrade_v3/sim/account.py`, `runs/golden/v3-13/BINGX_PORTFOLIO/`.

---

## 1. Các Quy tắc & Định nghĩa Bắt buộc Cần Chốt

### 1.1. Phân tách đơn vị "Trade" (KHÔNG dùng chữ "Total Trades" mơ hồ)
Trong AutoTrade V3, một tín hiệu chiến lược đi qua 4 cấp độ khác nhau. Báo cáo **cấm** dùng một nhãn `"Total Trades"` chung chung và phải hiển thị tách bạch:

| Cấp đơn vị | Định danh trong Ledger | Ý nghĩa | Số tham chiếu Golden V3-13 (Mode C) |
| :--- | :--- | :--- | ---: |
| **Signals** | `signal_id` (`signals.parquet` / `signal_decisions.parquet`) | Tín hiệu thô sinh ra từ bộ chiến lược 20 combo | `5,984` |
| **Accepted Entries** | `trade_id` duy nhất (`trades.parquet`) | Lệnh được chấp nhận vào vị thế (sau lọc concurrency/margin) | `4,149` (`Fill rate = 69.33%`) |
| **Legs** | `leg_id` (`trades.parquet`) | Chặng đóng thực tế (`SINGLE`, `TP_HALF`, `TRAILING`). Một entry có thể có 1 hoặc 2 legs | `6,265` |
| **Merged Positions** | `(symbol, side)` trong `AccountLedger` | Vị thế gộp VWAP theo hướng trên sàn BingX (chỉ Mode C) | `Tối đa 4 vị thế đồng thời (BTC/ETH × BUY/SELL)` |

- **Quy ước cho khối Results kiểu MT5:** Các chỉ số cấp lệnh (`Profit/Loss Entries`, `Largest/Average Profit/Loss`, `Long/Short Won %`, `Expected Payoff`, `Consecutive Wins/Losses`) lấy **Accepted Entries (`trade_id`)** làm đơn vị chuẩn (tính bằng cách gộp `SUM(net_pnl)` theo `trade_id` trước khi phân loại thắng/thua), đồng thời hiển thị thêm **Legs (`leg_id`)** ở những nơi cần đối chiếu (`Leg WR %`, `Net PF (Leg)`).

### 1.2. Sharpe Ratio
- **Tần suất:** Theo **ngày (UTC)** từ chuỗi `balance` cuối mỗi ngày (`00:00..23:59 UTC`).
  - Mode C (có `snapshots.parquet`): `daily_balance = snapshots.groupby(UTC_date)["balance"].last()`.
  - Mode A/B (không có `snapshots.parquet`): dựng chuỗi balance theo `exit_time_ms` từ `equity.parquet` (`CLOSED_TRADE`), resample theo ngày UTC (`last().ffill()`, điền ngày đầu bằng `initial_equity`).
- **Lợi suất ngày:** $r_d = \frac{\text{balance}_d}{\text{balance}_{d-1}} - 1$.
- **Lãi suất phi rủi ro:** $r_f = 0$.
- **Hệ số năm hóa (Annualization):** $\sqrt{365}$ (vì thị trường crypto giao dịch 24/7, 365 ngày/năm).
- **Công thức:**
  $$\text{Sharpe} = \frac{\text{mean}(r_d)}{\text{std}(r_d, \text{ddof}=1)} \times \sqrt{365}$$
- **Điều kiện biên:** Nếu số ngày $N < 2$ hoặc $\text{std}(r_d) \le 10^{-12}$, trả về `N/A` kèm lý do. (Trên Golden V3-13 Mode C: $N = 1,095$ ngày, $\text{Sharpe} = 1.4772 \approx 1.48$).

### 1.3. Equity MTM (Mark-to-Market)
- **Nguồn:** Cột `equity` (`= balance + unrealized_pnl`) trong `snapshots.parquet` (hoặc `equity.parquet` khi `equity_kind == 'M1_MTM'`) do `AccountLedger` sinh ra.
- **Quy tắc thiếu dữ liệu:** Mode A (`ALL_SIGNALS`), Mode B (`ONE_TRADE_PER_COMBO`), hoặc các run không ghi `snapshots.parquet` **không có** đường Equity MTM. UI phải **ẩn đường Equity MTM** và ghi rõ `N/A — Không có M2M snapshots cho chế độ này`.

### 1.4. Free Margin / Used Margin / Margin Level
- **Nguồn:** `snapshots.parquet` (`used_margin`, `free_margin`, `equity`) từ `AccountLedger`.
  - `Max Used Margin` = `max(used_margin)` (`$16.00` trên Golden V3-13).
  - `Min Free Margin` = `min(free_margin)` (`$960.52` trên Golden V3-13).
  - `Min Margin Level (%)` = `min(equity / used_margin * 100)` trên các bar có `used_margin > 1e-9`.
- **Quy tắc thiếu dữ liệu:** Nếu run không có `snapshots.parquet` (hoặc thiếu cột `used_margin`/`free_margin`), bắt buộc trả về `N/A — Không có dữ liệu AccountLedger snapshots`, tuyệt đối không bịa.

### 1.5. Balance Drawdown (Absolute / Maximal / Relative) — Chuẩn MT5
Dịch đúng 3 khái niệm Strategy Tester của MT5 trên chuỗi **Balance đã chốt (`balance_t`)** và phân biệt rõ với **Equity MTM Drawdown**:

1. **Balance Drawdown Absolute (`$` và `%`):** Độ sụt sâu nhất xuống dưới **vốn ban đầu** (`initial_equity`):
   $$\text{DD}_{\text{abs}} = \max(0,\ \text{initial\_equity} - \min_t(\text{balance}_t)), \quad \text{DD}_{\text{abs}\%} = \frac{\text{DD}_{\text{abs}}}{\text{initial\_equity}} \times 100\%$$
   *(Golden V3-13 Mode C: `$1,000.00 - $965.7820 = $34.22` / `3.42%`)*.
2. **Balance Drawdown Maximal (`$`):** Mức sụt giảm **tuyệt đối lớn nhất** tính từ đỉnh balance trước đó:
   $$\text{DD}_{\text{max}} = \max_t \big(\text{peak\_balance}_t - \text{balance}_t\big), \quad \text{với } \text{peak\_balance}_t = \max_{s \le t}(\text{balance}_s)$$
   *(Golden V3-13 Mode C closed-trade: `$45.7133`; M2M Equity Max DD: `$49.0136`)*.
3. **Balance Drawdown Relative (`%`):** Mức sụt giảm **tương đối lớn nhất** tính bằng `%` từ đỉnh balance trước đó:
   $$\text{DD}_{\text{rel}} = \max_t \left(\frac{\text{peak\_balance}_t - \text{balance}_t}{\text{peak\_balance}_t} \times 100\%\right)$$
   *(Golden V3-13 Mode C closed-trade: `3.46%`; M2M Equity Max DD %: `3.70%`)*.

### 1.6. Hai cơ sở PnL của Mode C (`pnl_vwap` vs `pnl_own_entry`) & `basis_shift`
- `pnl_own_entry` (`trades.parquet`): PnL tính trên giá vào lệnh riêng của từng entry (`entry_px_fill`), thỏa bất biến chi phí AC2:
  `pnl_own_entry = gross_pnl_raw - fee_entry - fee_exit - spread_cost - slippage_cost - funding_cost`.
  → Dùng để đánh giá **chất lượng tín hiệu độc lập** của từng combo (`Own-entry PnL` trong bảng 20 combo).
- `pnl_vwap` (`net_pnl` trong `trades.parquet` ở Mode C): PnL phân bổ từ vị thế gộp VWAP theo `(symbol, side)` của `AccountLedger`.
  → Dùng cho **kế toán tài khoản thực tế**: `Portfolio Net PnL`, `Total Net Profit`, `Final Balance`, `ROI %`, `Balance Curve`.
- `basis_shift` (`attribution_delta` = `pnl_vwap - pnl_own_entry`): Cầu nối kế toán giữa hai cơ sở. Tổng `SUM(basis_shift)` trên toàn danh mục Mode C luôn bằng `0.0` (sai số `< 1e-12`).
- Trên Mode A và Mode B (không gộp vị thế): `pnl_own_entry == net_pnl`, `basis_shift == 0`.

---

## 2. Bảng Ánh xạ Trường Chi tiết (Field Map)

> Ký hiệu trạng thái:
> - **CÓ SẴN**: Đã có sẵn trong `analysis.json` / `run_manifest.json` / `metrics_engine.py` / `metrics.py`.
> - **CẦN THÊM**: Dữ liệu gốc đã có trong `trades.parquet` / `snapshots.parquet`, cần bổ sung hàm tính vào `autotrade_v3/analysis/metrics.py` ở Bước 2.
> - **N/A (có điều kiện)**: Chỉ có ở một số mode/run (ví dụ Mode C có `snapshots.parquet`), thiếu thì hiển thị `N/A` + lý do.

### 2.1. Khối 1 — Banner & Metadata Run
| Tên hiển thị (UI) | Tên MT5 | Công thức / Giá trị | Cột / Artifact nguồn | Đơn vị | Trạng thái |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Cost Profile Banner | — | `cost_profile` (`IDEAL` / `BASE` / `STRESS`) + `cost_profile_hash[:8]` | `run_manifest.json` (`cost_profile`, `cost_profile_hash`) | string | CÓ SẴN |
| Run ID & Mode | — | `run_id`, `execution_mode`, `ambiguity_policy` | `run_manifest.json` | string | CÓ SẴN |
| Time Window (UTC) | Period | `time_window.start` .. `time_window.end` | `run_manifest.json` (`time_window`) | ISO date (UTC) | CÓ SẴN |
| Governance Label (2026) | — | Nếu chứa dữ liệu `2026-01-01..2026-11-01`: gắn `VALIDATION (đã dùng để chọn biến thể)` | Quy tắc AT2-002 (`evidence/v3-15d/holdout_status.md`) | badge | CẦN THÊM |
| Commit / Dataset Hash | — | `git_sha[:8]`, `dataset_hash[:8]`, `config_hash[:8]` | `run_manifest.json` | hex string | CÓ SẴN |
| Validation Status | — | `overall_validation` (`VERIFIED` / `FAILED`) + 12 cổng `A1..D2` | `validation.json` | enum | CÓ SẴN |

### 2.2. Khối 2 — Thẻ Account / Cost Reality / Risk / Volume
| Tên hiển thị (UI) | Tên MT5 | Công thức | Cột / Artifact nguồn | Đơn vị | Trạng thái |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Initial Capital | Initial Deposit | `initial_equity` | `run_manifest.json` (`initial_equity`) | USD (`$`) | CÓ SẴN |
| Final Balance | Balance | `initial_equity + SUM(trades.net_pnl)` | `trades.parquet` (`net_pnl`) / `snapshots.parquet` (`balance[-1]`) | USD (`$`) | CÓ SẴN |
| Final Equity MTM | Equity | `snapshots.equity[-1]` | `snapshots.parquet` (`equity`) | USD (`$`) | N/A (có điều kiện: Mode A/B không có snapshot) |
| Max Used Margin | Margin (Max) | `MAX(snapshots.used_margin)` | `snapshots.parquet` (`used_margin`) | USD (`$`) | N/A (có điều kiện: thiếu snapshot → N/A) |
| Min Free Margin | Free Margin (Min) | `MIN(snapshots.free_margin)` | `snapshots.parquet` (`free_margin`) | USD (`$`) | N/A (có điều kiện: thiếu snapshot → N/A) |
| Min Margin Level | Margin Level (Min) | `MIN(equity / used_margin * 100)` khi `used_margin > 1e-9` | `snapshots.parquet` (`equity`, `used_margin`) | `%` | CẦN THÊM (N/A nếu thiếu snapshot) |
| Total Fees | Commission / Fees | `SUM(fee_entry + fee_exit)` (= `SUM(fee_total)`) | `analysis.json` (`pnl.total_fees`) / `trades.parquet` | USD (`$`) | CÓ SẴN |
| Fee Drag | — | `total_fees / gross_pnl * 100` (nếu `abs(gross_pnl) > 1e-12`) | `metrics_engine.cost_reality_metrics()` | `%` | CÓ SẴN |
| Break-even Slippage / Side | — | `net_pnl / (2 * SUM(notional)) * 100` (cũng hiển thị dạng `bps` = `× 100`) | `metrics_engine.cost_reality_metrics()` | `%` / `bps` | CÓ SẴN |
| Slippage + Spread + Funding | — | `SUM(spread_cost)`, `SUM(slippage_cost)`, `SUM(funding_cost)` | `analysis.json` (`execution_costs`) / `trades.parquet` | USD (`$`) | CÓ SẴN |
| Total Signals | — | Tổng tín hiệu đầu vào | `analysis.json` (`accounting.total_signals`) / `signal_decisions.parquet` | count | CÓ SẴN |
| Accepted Entries | — | `COUNT(DISTINCT trade_id)` | `analysis.json` (`accounting.accepted_entries`) / `trades.parquet` | count | CÓ SẴN |
| Entry Fill Rate | — | `accepted_entries / total_signals * 100` | `metrics_engine.py` / `analysis.json` | `%` | CÓ SẴN |
| Total Legs | — | `COUNT(leg_id)` | `analysis.json` (`accounting.total_legs`) / `trades.parquet` | count | CÓ SẴN |

### 2.3. Khối 3 & 4 — Chuỗi Biểu đồ (Balance / Equity / Drawdown $ & %)
| Tên hiển thị (UI) | Tên MT5 | Công thức | Cột / Artifact nguồn | Đơn vị | Trạng thái |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Balance Curve (Realized) | Balance | `initial_equity + CUMSUM(net_pnl)` theo `(exit_time_ms, leg_id)` | `equity.parquet` / `trades.parquet` | USD (`$`) | CÓ SẴN |
| Equity MTM Curve | Equity | `snapshots.equity` (`balance + unrealized_pnl`) | `snapshots.parquet` / `equity.parquet` (`M1_MTM`) | USD (`$`) | N/A (có điều kiện: ẩn nếu thiếu snapshot) |
| Drawdown ($) Curve | — | `peak_t - val_t` (Closed & M2M) | `snapshots.parquet` (`drawdown_dollars`) / `metrics.py` | USD (`$`) | CÓ SẴN (downsample cho UI) |
| Drawdown (%) Curve | — | `(peak_t - val_t) / peak_t * 100` | `snapshots.parquet` (`drawdown_pct`) / `metrics.py` | `%` | CÓ SẴN (downsample cho UI) |

### 2.4. Khối 5 — Results Kiểu MT5
| Tên hiển thị (UI) | Tên MT5 | Công thức | Cột / Artifact nguồn | Đơn vị | Trạng thái |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Total Net Profit | Total Net Profit | `SUM(trades.net_pnl)` | `analysis.json` (`pnl.net_pnl`) | USD (`$`) | CÓ SẴN |
| ROI (%) | — | `net_pnl / initial_equity * 100` | `analysis.json` (`pnl.roi_pct`) | `%` | CÓ SẴN |
| Gross Profit (Entry-level) | Gross Profit | `SUM(entry_net_pnl > 0)` (cộng thêm `gross_pnl` trước phí để đối chiếu) | `trades.parquet` nhóm theo `trade_id` | USD (`$`) | CẦN THÊM (hiện mới có ở leg-level trong `metrics_engine`) |
| Gross Loss (Entry-level) | Gross Loss | `SUM(entry_net_pnl < 0)` | `trades.parquet` nhóm theo `trade_id` | USD (`$`) | CẦN THÊM |
| Profit Factor (Entry / Leg) | Profit Factor | `Gross Profit / abs(Gross Loss)` | `analysis.json` (`pnl.net_pf_trade`, `pnl.net_pf_leg`) | ratio | CÓ SẴN |
| Expected Payoff | Expected Payoff | `net_pnl / accepted_entries` (Entry) & `net_pnl / total_legs` (Leg) | `analysis.json` (`pnl.avg_trade_pnl`, `pnl.avg_leg_pnl`) | USD (`$`) | CÓ SẴN |
| Recovery Factor (Closed / M2M) | Recovery Factor | `net_pnl / max_dd_dollars` | `analysis.json` (`pnl.net_pnl` / `risk.*_max_drawdown_dollars`) | ratio | CẦN THÊM |
| Sharpe Ratio | Sharpe Ratio | `(mean(r_d) / std(r_d, ddof=1)) * sqrt(365)`, `rf = 0` | `snapshots.parquet` (`balance`) hoặc `equity.parquet` | ratio | CẦN THÊM |
| Balance Drawdown Absolute | Balance Drawdown Absolute | `max(0, initial_equity - min(balance_t))` | `snapshots.parquet` / `equity.parquet` | USD (`$`) & `%` | CẦN THÊM |
| Balance Drawdown Maximal | Balance Drawdown Maximal | `max(peak_balance_t - balance_t)` | `analysis.json` (`risk.closed_trade_max_drawdown_dollars`) | USD (`$`) | CÓ SẴN |
| Balance Drawdown Relative | Balance Drawdown Relative | `max((peak_balance_t - balance_t) / peak_balance_t * 100)` | `analysis.json` (`risk.closed_trade_max_drawdown_pct`) | `%` | CÓ SẴN |
| M2M Max Drawdown ($ / %) | Equity Drawdown | `max(snapshots.drawdown_dollars)`, `max(snapshots.drawdown_pct)` | `analysis.json` (`risk.m2m_max_drawdown_*`) | USD / `%` | N/A (có điều kiện: thiếu snapshot → N/A) |
| Profit / Loss Entries | Profit Trades / Loss Trades | `COUNT(entry_net > 0)`, `COUNT(entry_net < 0)` + `%` | `analysis.json` (`win_rate.winning_trades`, `losing_trades`, `trade_win_rate_pct`) | count / `%` | CÓ SẴN |
| Largest Profit / Loss Entry | Largest profit/loss trade | `MAX(entry_net)`, `MIN(entry_net)` | `trades.parquet` nhóm theo `trade_id` | USD (`$`) | CẦN THÊM |
| Average Profit / Loss Entry | Average profit/loss trade | `MEAN(entry_net > 0)`, `MEAN(entry_net < 0)` | `trades.parquet` nhóm theo `trade_id` | USD (`$`) | CẦN THÊM (hiện `metrics.py` tính trên leg) |

### 2.5. Khối 6 & 7 — LONG / SHORT & Chuỗi Thắng / Thua (Streaks)
| Tên hiển thị (UI) | Tên MT5 | Công thức | Cột / Artifact nguồn | Đơn vị | Trạng thái |
| :--- | :--- | :--- | :--- | :--- | :--- |
| Long Entries (Won %) | Long Trades (won %) | `COUNT(side == 'BUY')`, `COUNT(won) / total * 100` (entry-level) | `trades.parquet` (`side`, `trade_id`, `net_pnl`) | count / `%` | CẦN THÊM |
| Short Entries (Won %) | Short Trades (won %) | `COUNT(side == 'SELL')`, `COUNT(won) / total * 100` (entry-level) | `trades.parquet` (`side`, `trade_id`, `net_pnl`) | count / `%` | CẦN THÊM |
| Long / Short Net PnL & PF | — | `SUM(net_pnl)` & PF tách theo `side` | `trades.parquet` (`side`, `trade_id`, `net_pnl`) | USD / ratio | CẦN THÊM |
| Max Consecutive Wins (count & $) | Maximum consecutive wins ($) | Chuỗi thắng liên tiếp dài nhất (theo `exit_time_ms` entry-level) và tổng `$` của chuỗi đó | `trades.parquet` (`trade_id`, `exit_time_ms`, `net_pnl`) | count / USD | CẦN THÊM |
| Max Consecutive Losses (count & $) | Maximum consecutive losses ($) | Chuỗi thua liên tiếp dài nhất và tổng `$` của chuỗi đó | `trades.parquet` | count / USD | CẦN THÊM |
| Maximal Consecutive Profit ($ & count) | Maximal consecutive profit (count) | Tổng `$` dương lớn nhất trong một chuỗi thắng liên tiếp | `trades.parquet` | USD / count | CẦN THÊM |
| Maximal Consecutive Loss ($ & count) | Maximal consecutive loss (count) | Tổng `$` âm sâu nhất trong một chuỗi thua liên tiếp | `trades.parquet` | USD / count | CẦN THÊM |
| Average Consecutive Wins / Losses | Average consecutive wins / losses | Trung bình độ dài các chuỗi thắng / chuỗi thua | `trades.parquet` | count | CẦN THÊM |

### 2.6. Khối 8, 9, 10, 11, 12 — Breakdowns & So sánh
| Khối | Trường hiển thị chính | Cột / Artifact nguồn | Trạng thái |
| :--- | :--- | :--- | :--- |
| **8. Cost Sensitivity Grid** | Mốc `0 / 1 / 2 / 3.4 / 5 bps`: Net PnL, ROI %, Fee+Slip, Net PF, M2M DD % | Tổng hợp từ các run thực tế (`IDEAL`, `BASE`, `STRESS`, sensitivity runs); ô chưa chạy = `N/A` | CẦN THÊM (bộ tổng hợp cross-run) |
| **9. Bảng 20 Combo** | `Signals`, `Entries`, `Fill %`, `Legs`, `WR %`, `Own-entry PnL`, `Portfolio Net PnL`, `Delta (basis_shift)`, `Fees`, `Net PF` | `analysis.json` (`combos`) + `metrics_engine.attribution_breakdown()` | CÓ SẴN (`Fill %` & `Delta` tính trực tiếp từ cột sẵn) |
| **10. BTC / ETH** | `Signals`, `Entries`, `Legs`, `Leg WR %`, `Gross PnL`, `Fees`, `Net PnL`, `Net PF (Trade/Leg)`, `Gross PF` | `analysis.json` (`symbols`) | CÓ SẴN |
| **11. Báo cáo tháng** | `YYYY-MM`, `Kỳ quản trị`, `Entries`, `Legs`, `Leg WR %`, `Gross PnL`, `Fees`, `Net PnL`, `Net PF` | `analysis.json` (`monthly`) | CÓ SẴN (cần gắn `Kỳ quản trị` theo AT2-002) |
| **12. So sánh 3 chế độ** | `ALL_SIGNALS`, `ONE_TRADE_PER_COMBO`, `BINGX_PORTFOLIO`: Signals, Entries, Legs, Gross, Fees, Net, ROI, Leg WR, Net PF, Closed DD, M2M DD (`N/A` nếu không có snapshot) | `analysis.json` của 3 mode | CÓ SẴN (cần loader gom 3 mode) |

---

## 3. Liệt kê Khoảng trống & Đề xuất Bổ sung vào `autotrade_v3/analysis/metrics.py` (Bước 2)

Hiện tại `autotrade_v3/analysis/metrics.py` (`BacktestMetrics` & `calculate_metrics`) chỉ mới tính các chỉ số cơ bản trên danh sách `TradeRecord` (leg-level). Để phục vụ đầy đủ báo cáo MT5-style ở Bước 2, đề xuất bổ sung một hàm thuần túy (UI-agnostic) `compute_mt5_extended_metrics(trades_df, snapshots_df=None, decisions_df=None, initial_equity=1000.0)` vào `autotrade_v3/analysis/metrics.py` (hoặc module con gọi từ `metrics.py`):

1. **Entry-level Aggregation (`groupby("trade_id")`)**:
   - Tính `gross_profit_entry`, `gross_loss_entry`, `largest_profit_entry`, `largest_loss_entry`, `avg_profit_entry`, `avg_loss_entry` trên `net_pnl` đã gộp theo `trade_id` (thay vì tính trên từng `leg_id`).
2. **LONG / SHORT Split (`side == "BUY"` / `"SELL"`)**:
   - Đếm `long_entries`, `long_won`, `long_win_rate_pct`, `long_net_pnl`, `long_pf`; tương tự cho `short_*`.
3. **Consecutive Streaks (MT5 6-tuple)**:
   - Sắp xếp entries theo `(exit_time_ms, entry_time_ms, trade_id)`, tính chính xác 6 chỉ số: `max_consecutive_wins_count` & `_dollars`, `max_consecutive_losses_count` & `_dollars`, `maximal_consecutive_profit_dollars` & `_count`, `maximal_consecutive_loss_dollars` & `_count`, `avg_consecutive_wins`, `avg_consecutive_losses`.
4. **Recovery Factor & Balance Drawdown Absolute**:
   - `recovery_factor_closed = net_pnl / closed_dd_dollars` (và `recovery_factor_m2m` nếu có snapshots).
   - `balance_dd_absolute_dollars = max(0.0, initial_equity - min_balance)` và `balance_dd_absolute_pct`.
5. **Daily Sharpe Ratio (`rf = 0`, `√365`)**:
   - Tính từ chuỗi `balance` cuối ngày UTC (`snapshots.parquet` cho Mode C, `equity.parquet` resampled daily cho Mode A/B). Trả về `None` + lý do nếu `< 2` ngày hoặc `std == 0`.
6. **Margin Summary từ `snapshots.parquet`**:
   - `max_used_margin`, `min_free_margin`, `min_margin_level_pct` (khi `snapshots_df` tồn tại và không rỗng; ngược lại trả `status = "N/A"`).
7. **Governance Labeling (AT2-002)**:
   - Gắn nhãn kỳ dữ liệu tự động cho `subwindows` và `monthly`: mọi khoảng chứa dữ liệu `2026-01-01..2026-11-01` **bắt buộc** gắn `VALIDATION (đã dùng để chọn biến thể)`, loại bỏ nhãn `Holdout` cũ trong `analysis.json` khi render báo cáo.
