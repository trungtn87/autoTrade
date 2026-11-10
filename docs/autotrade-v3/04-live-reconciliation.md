# AutoTrade V3-04 — Đối chứng giao dịch thật bằng export chỉ đọc

**Đã xây dựng bộ kiểm kê/đối chứng offline; Gate C2 vẫn `INCONCLUSIVE`.** V3 chạy trên PC, V2 vẫn là nơi duy nhất giao dịch thật. Không tạo migration, không có credential BingX/V2 trong V3, không đọc API private khi backtest.

## 1. Nguồn và chuẩn bằng chứng

V3-04 sử dụng CSV đã rút gọn bằng câu lệnh `SELECT` (không xuất `raw` đầy đủ):

| File bắt buộc | Nguồn V2 | Mục đích |
|---|---|---|
| `signal_links.csv` | `autotrade_analytics.signal_links` | `signal_id`, symbol, side, combo, thời điểm nến đóng và Entry order ID |
| `bingx_orders.csv` | `autotrade_analytics.bingx_orders` | order ID, loại lệnh, trạng thái, khối lượng và positionSide |
| `bingx_fills.csv` | `autotrade_analytics.bingx_fills` | trade ID, order ID, khối lượng, giá, fee, realized PnL, timestamp |
| `bingx_income.csv` | `autotrade_analytics.bingx_income` | đối chiếu fee/funding/income theo exact trade ID (không ghép theo thời gian) |
| `processed_targets.csv` | `public.processed_targets` | `exit_mode`, `protection.sl/tp/trailing.order_id` và cờ vị thế gộp; **payload đã lọc** |

Sử dụng `scripts/v3-04-export-readonly.psql` với tài khoản PostgreSQL **SELECT-only** được chủ hệ thống cấp theo cơ chế hiện có; kết nối TLS, không copy service-role hoặc private exchange keys. Chạy psql từ thư mục riêng trên PC, ví dụ `D:/AutoTradeV3/exports/v2/2026-11-10/` để các CSV nằm ngoài Git. PostgreSQL transaction `REPEATABLE READ READ ONLY` bảo đảm nhất quán trong một lần export.

```powershell
# Trong thư mục xuất CSV, đã cấu hình kết nối read-only và SSL ngoài repo.
psql -X -v ON_ERROR_STOP=1 -f path/to/scripts/v3-04-export-readonly.psql
python -m autotrade_v3.data_backtest.reconcile_live seal `
  --input-dir "D:/AutoTradeV3/exports/v2/2026-11-10" `
  --exported-at-utc 2026-11-10T04:30:00Z
python -m autotrade_v3.data_backtest.reconcile_live reconcile `
  --input-dir "D:/AutoTradeV3/exports/v2/2026-11-10" `
  --output-dir "D:/AutoTradeV3/runs/V3-04" `
  --scenario-split "D:/AutoTradeV3/runs/V3-03/split/scenario_trades.jsonl" `
  --scenario-hard "D:/AutoTradeV3/runs/V3-03/hard/scenario_trades.jsonl" `
  --holdout-start 2026-10-15
```

Timestamp của manifest phải là thời gian **thực tế** xuất dữ liệu theo UTC (ở ví dụ chỉ minh họa). Công cụ kiểm tra cả 5 file có đủ các cột bắt buộc, đóng SHA-256, kiểm số dòng và từ chối nếu bị thay đổi. Báo cáo `gate_c2_report.json` và `reconciled_evidence.jsonl` lưu **trên máy**, chỉ dùng `event_hash` thay ID tài khoản/order ID, có reason code theo từng tín hiệu.

## 2. Nguyên tắc ghép và chống sai kết quả

- Ghép `signal_links → orders → fills` theo **exact order_id + symbol**. `FILLED` status không đồng nghĩa có dữ liệu fill; fill không được tự lấy từ OHLC M1 hoặc các order cùng giá.
- Lấy ID lệnh bảo vệ từ `processed_targets.protection.sl/tp/trailing.order_id`; phải đối chiếu đúng order ID, loại `STOP_MARKET`/`TAKE_PROFIT_MARKET`/`TRAILING_STOP_MARKET`, side ngược Entry, cùng positionSide, có fill đầy đủ và không duplicate/qty conflict.
- Không dùng price/time proximity để suy `triggerOrderId → fill`. Với BingX native stop, order điều kiện và fill trigger có thể mang ID khác; V3 **không tự coi chúng thuộc cùng combo** nếu thiếu bằng chứng exact parent-child từ BingX.
- Hai combo dùng cùng ID con, hoặc khoảng nắm giữ cùng symbol/cùng chiều chồng lấn, đều giữ trạng thái `MERGED_OR_SHARED_UNRESOLVED`. `positionID` là container sàn, **không phải ID combo**. Không cấm Entry cùng chiều trong V2.
- Không nhân đôi thu nhập `bingx_income` và `bingx_fills`. Nếu `income.trade_id` không khớp exact `fills.trade_id`, không phân bổ funding/PnL theo combo. Trade net PnL và ROI vẫn **chưa xác minh**.
- `signal_close_ms` V3 phải **bằng chính xác** `signal_links.signal_time_ms` và cùng combo/side/symbol. Không ghép gần phút hoặc dùng ứng viên M1 lệch sàn để giả parity.
- M1 hybrid lịch sử kết thúc trước 01/11/2026 UTC; tín hiệu tháng 11 được gắn `OUTSIDE_V3_M1`. Giai đoạn còn lại đa số là giá Binance, không phải giá BingX, nên phân riêng `CROSS_VENUE_PRICE_SERIES`.
- Calibration và Holdout tách theo thời điểm đã cố định; báo số cặp so được và khoảng Wilson 95% **mô tả**. Chưa đủ tối thiểu 30 **verified independent pairs / exit profile**, không báo fit error/win-rate hay khẳng định simulator khớp sàn.

V3-04 không thay đổi input profile của V3-03, không đưa tham số từ holdout quay trở lại calibration; không nâng cấp Gate C2 thành PASS bằng cách loại các lệnh khó.

## 3. Kiểm kê production read-only thực tế (10/11/2026)

Nguồn: truy vấn SELECT trực tiếp trên Supabase V2, không thay đổi dữ liệu. Đây là **số đo metadata tại một thời điểm**, không phải lần chạy toàn bộ export CSV trong PC.

| Trường | BTC | ETH | Tổng |
|---|---:|---:|---:|
| Signal → Entry | 68 | 64 | 132 |
| Entry có exact fill theo entry_order_id (cả khối lượng) | 35 | 24 | 59 |
| Linked signal có `exit_mode` trong state | 19 | 18 | 37 |
| Linked signal có native ID SL bảo vệ | 66 | 64 | 130 |
| Linked signal có native ID TP bảo vệ | 66 | 64 | 130 |
| Linked signal có native ID trailing | 17 | 15 | 32 |
| Linked signal có exact exit fill theo ID bảo vệ | 0 | 0 | 0 |

Tổng bảng: `bingx_orders` 435, `bingx_fills` 134, `bingx_income` 497, `processed_targets` 263. **0/497 income có trade_id ghép trực tiếp với bảng fills** (vì vậy không phân bổ theo event). Trong `bingx_orders.raw`, `triggerOrderId`/`positionID` có trường nhưng **không tìm thấy direct fill theo triggerOrderId** cho các child ID liên kết. Không lấy các order `FILLED` khác trong cùng symbol/side tự gán làm Exit của combo.

Phạm vi dữ liệu M1 được kiểm chứng 01/11/2023–31/10/2026: 109/132 tín hiệu có ngày nằm trong khung nến, 23 tín hiệu thuộc 11/2026 không có M1; **2 tín hiệu BTC trong ngày 28/10/2026** dùng nến BingX thực sự, phần lớn các tín hiệu khác là khác nguồn (Binance M1 ↔ BingX live).

**Kết luận tạm thời:** `Gate C2 = INCONCLUSIVE`. Không thể dùng 132 Entry làm 132 lệnh có PnL thật, hoặc dùng 497 income phân bổ cho từng combo. Cần bổ sung bằng chứng parent/trigger child exchange, exit fills tương ứng, cùng phương thức quy funding và shared position trước khi hiệu chỉnh sai số thực thi của BingX.

## 4. Công việc còn phải thực hiện để nghiệm thu C2

- Dùng CSV export sanitized trên local, chạy đủ bộ kiểm tra SHA, identity, fill, trạng thái/exit mode và đối chiếu chính xác cùng V3-03.
- Nếu muốn đối chiếu Exit native BingX: **ưu tiên export lịch sử từ một tài khoản chỉ đọc riêng** kèm parent/triggered-child order identifier hoặc giao dịch có tham chiếu chính xác, không dùng khóa giao dịch của V2. Ghi lại nguồn/loại bằng chứng; không chuyển dữ liệu tài khoản nhạy cảm vào Git.
- Chỉ mô hình hóa phân phối slippage/latency trên **verified pairs** cùng venue, phân tách calibration/holdout theo thời gian và mẫu số. Nếu vẫn thiếu, ghi `CALIBRATION_INCONCLUSIVE` thay vì đưa ra kết luận đẹp nhưng sai.

## 5. Kiểm thử và ranh giới an toàn

`python -m unittest autotrade_v3.tests.test_reconcile_live -v` chạy offline với dữ liệu fixture nhỏ: mismatch order/fill, trùng order con, shared container, thiếu exit_mode, sửa CSV sau khi seal, timestamp, cùng-sàn/khác-sàn, trùng tín hiệu, PnL không xác minh. Không có kết nối Supabase/BingX, không sửa engine 20FINAL, không tạo API mới.
