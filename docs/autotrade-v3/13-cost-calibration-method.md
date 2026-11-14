# Phương pháp hiệu chuẩn chi phí thực thi (V3-13)

Tài liệu này mô tả phương pháp ước lượng **spread**, **slippage theo loại lệnh** (`entry_market`, `tp`, `stop`, `trailing_stop`), **phí giao dịch (taker/maker)** và **funding** từ bản xuất dữ liệu fill thật (chỉ đọc) khi Owner cung cấp, để điền vào hồ sơ `BASE` và `STRESS` trong `autotrade_v3/sim/cost_profiles.json`.

> **Lưu ý:** Tài liệu này chỉ mô tả phương pháp và dữ liệu cần thiết; **không** chứa con số ước lượng mặc định. Mọi giá trị trong `BASE` và `STRESS` phải đến từ Owner (`CONFIRMED_BY_OWNER`) hoặc giữ trạng thái `UNCONFIRMED`.

## 1. Dữ liệu đầu vào cần từ Owner (bản xuất chỉ đọc)
Để hiệu chuẩn chính xác theo từng symbol (`BTC-USDT`, `ETH-USDT`), cần tối thiểu các trường sau từ lịch sử khớp lệnh thật:
1. `symbol`, `side` (`BUY` / `SELL`), `order_type` (`MARKET`, `LIMIT_TP`, `STOP_MARKET`, `TRAILING_STOP`), `position_side` (`LONG` / `SHORT`).
2. `trigger_time_ms` / `submit_time_ms` và `fill_time_ms` (để khớp với nến M1 / giá tham chiếu).
3. `trigger_price` (giá kích hoạt lệnh dừng / giá mục tiêu TP / giá mở nến tham chiếu) và `avg_fill_price` (giá khớp thực tế trên sàn).
4. `executed_qty`, `executed_notional`, `fee_paid`, `fee_asset`, `is_maker`.
5. (Tuỳ chọn) Chuỗi `funding_rate` theo kỳ 8 giờ theo từng `symbol` và `timestamp_ms`.

## 2. Công thức ước lượng từng thành phần

### 2.1 Phí giao dịch (`fee_taker`, `fee_maker`)
- Tính trực tiếp từ tỷ lệ `fee_paid / executed_notional` phân nhóm theo `is_maker` và `symbol`.
- Xác định loại lệnh nào thực tế khớp `maker` (`maker_order_types`, ví dụ `tp` nếu đặt limit TP) và loại nào khớp `taker`.

### 2.2 Half-spread (`half_spread_bps`) và Slippage (`slippage_bps`)
Với mỗi lệnh khớp thật, độ lệch bất lợi tổng hợp (tính bằng bps) so với giá tham chiếu `ref_price` là:

- Lệnh mua (`BUY` mở long hoặc `BUY` đóng short):
  $$\text{adverse\_bps} = \frac{\text{avg\_fill\_price} - \text{ref\_price}}{\text{ref\_price}} \times 10{,}000$$
- Lệnh bán (`SELL` mở short hoặc `SELL` đóng long):
  $$\text{adverse\_bps} = \frac{\text{ref\_price} - \text{avg\_fill\_price}}{\text{ref\_price}} \times 10{,}000$$

Cách tách **spread** và **slippage** theo loại lệnh:
1. Nếu có dữ liệu Best Bid / Best Ask tại thời điểm đặt lệnh:
   - `half_spread_bps` = trung vị (cho `BASE`) hoặc phân vị cao, ví dụ P90/P95 (cho `STRESS`), của $\frac{\text{ask} - \text{bid}}{2 \times \text{mid}} \times 10{,}000$.
   - `slippage_bps` cho từng `order_type` = phân vị của $(\text{adverse\_bps} - \text{half\_spread\_bps})$ (cắt dưới tại `0` với lệnh limit maker nếu không trượt giá âm).
2. Nếu **chỉ** có giá kích hoạt (`trigger_price`) và `avg_fill_price` (không có snapshot bid/ask):
   - Gộp phần chi phí nửa spread + trượt giá vào phân phối `adverse_bps` theo từng `order_type` (`entry_market`, `tp`, `stop`, `trailing_stop`).
   - Có thể gán `half_spread_bps = 0` và đặt toàn bộ phân vị trung vị vào `slippage_bps` (`BASE`), phân vị cao (ví dụ P90/P95 hoặc giai đoạn biến động mạnh) vào `STRESS`, kèm ghi chú rõ trong trường `source` của `cost_profiles.json` để tránh tính trùng.

### 2.3 Funding (`funding_bps_per_8h` hoặc bảng lịch sử)
- Nếu có bảng funding lịch sử theo kỳ 8h: nạp trực tiếp vào `CostModel(funding_table=...)` để tính theo đúng thời gian giữ vị thế qua các mốc 00:00, 08:00, 16:00 UTC.
- Nếu chỉ có phân phối tổng hợp: dùng trung vị (`BASE`) và phân vị bất lợi (`STRESS`) theo tỷ lệ bps mỗi 8 giờ; nếu không có dữ liệu thì giữ `NOT_MODELED`.

## 3. Quy trình cập nhật `cost_profiles.json`
1. Owner cung cấp con số đã duyệt cho từng symbol và nguồn/ngày đo.
2. Cập nhật `BASE` và `STRESS` với `status: "CONFIRMED_BY_OWNER"` và `source: "<ngày> - <nguồn đo>"`.
3. Chạy lại pipeline với `--cost-profile BASE` và `--cost-profile STRESS`; kiểm tra `cost_profile_hash` được ghi nhất quán vào `run_manifest.json`.
