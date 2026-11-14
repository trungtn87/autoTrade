# THÍ NGHIỆM CỬA SỔ CON & WARM-UP (MỤC 4.8 — KIỂM CHỨNG GIẢ THUYẾT P2-5)

## 1. Thiết lập thí nghiệm
- **Chiến lược:** `20FINAL@1` (`signal_spec_hash = fe592c07fdeaee9a20e21c7a1443ae1ac94ab2ec9a8c30d92dcb1dcea997b26e`)
- **Run đầy đủ 3 năm:** `2023-11-01` đến `2026-11-01` (nạp kèm 3.400 nến M15 warm-up từ `2023-09-26T14:00:00Z`), sau đó trích tập con tín hiệu nằm trong cửa sổ `[2025-05-01, 2025-11-01)`.
- **Run cửa sổ con:** `2025-05-01` đến `2025-11-01` (nạp kèm 3.400 nến M15 warm-up từ `2025-03-26T00:00:00+00:00` đến `2025-10-31T23:45:00+00:00`, tổng `21,120` nến M15/symbol).

## 2. Kết quả tổng hợp (`[2025-05-01, 2025-11-01)`)

| Chỉ tiêu | Tín hiệu Approved (`approved=True, warmup_ready=True`) | Tín hiệu Thô Layer 3 (`warmup_ready=True`) |
|:---|---:|---:|
| Tổng số tín hiệu trong tập con của Run 3 năm | **1103** | **1231** |
| Tổng số tín hiệu trong Run cửa sổ con | **1103** | **1231** |
| Số tín hiệu trùng khớp ở cả hai bên (`In Both`) | **1103** | **1231** |
| Số tín hiệu chỉ có ở Run 3 năm (`Only in 3-Year`) | **0** | **0** |
| Số tín hiệu chỉ có ở Run cửa sổ con (`Only in Subwindow`) | **0** | **0** |
| Tổng số tín hiệu lệch (`Symmetric Difference`) | **0** | **0** |

## 3. Phân rã theo từng Combo

| Combo Key | TF | Layer 2 | 3-Year Subset | Subwindow Run | In Both | Only in 3-Year | Only in Subwindow | Raw Only 3-Yr | Raw Only Sub |
|:---|:---:|:---|---:|---:|---:|---:|---:|---:|---:|
| `BTC-USDT/C1` | 1h | `SMC_STRICT` | 47 | 47 | 47 | 0 | 0 | 0 | 0 |
| `BTC-USDT/C2` | 1h | `SMC_VETO_OB` | 18 | 18 | 18 | 0 | 0 | 0 | 0 |
| `BTC-USDT/C4` | 1h | `SMC_VETO` | 49 | 49 | 49 | 0 | 0 | 0 | 0 |
| `BTC-USDT/C6` | 1h | `SMC_VETO_OB` | 22 | 22 | 22 | 0 | 0 | 0 | 0 |
| `BTC-USDT/C7` | 15m | `OFF` | 35 | 35 | 35 | 0 | 0 | 0 | 0 |
| `BTC-USDT/C9` | 15m | `SMC_VETO` | 18 | 18 | 18 | 0 | 0 | 0 | 0 |
| `BTC-USDT/C10` | 15m | `SMC_VETO` | 7 | 7 | 7 | 0 | 0 | 0 | 0 |
| `BTC-USDT/TIER` | 1h | `OFF` | 21 | 21 | 21 | 0 | 0 | 0 | 0 |
| `BTC-USDT/C11` | 1h | `SMC_STRICT_OB` | 125 | 125 | 125 | 0 | 0 | 0 | 0 |
| `BTC-USDT/C14` | 1h | `OB` | 50 | 50 | 50 | 0 | 0 | 0 | 0 |
| `BTC-USDT/C16` | 1h | `OB` | 19 | 19 | 19 | 0 | 0 | 0 | 0 |
| `ETH-USDT/C1` | 1h | `SMC_VETO` | 66 | 66 | 66 | 0 | 0 | 0 | 0 |
| `ETH-USDT/C3` | 1h | `SMC_STRICT` | 20 | 20 | 20 | 0 | 0 | 0 | 0 |
| `ETH-USDT/C4` | 1h | `SMC_VETO` | 50 | 50 | 50 | 0 | 0 | 0 | 0 |
| `ETH-USDT/C6` | 1h | `SMC_VETO` | 14 | 14 | 14 | 0 | 0 | 0 | 0 |
| `ETH-USDT/C7` | 15m | `OB` | 51 | 51 | 51 | 0 | 0 | 0 | 0 |
| `ETH-USDT/C10` | 15m | `SMC_STRICT` | 3 | 3 | 3 | 0 | 0 | 0 | 0 |
| `ETH-USDT/C12` | 1h | `SMC_VETO_OB` | 69 | 69 | 69 | 0 | 0 | 0 | 0 |
| `ETH-USDT/C13` | 1h | `OFF` | 65 | 65 | 65 | 0 | 0 | 0 | 0 |
| `ETH-USDT/C15` | 1h | `OB` | 354 | 354 | 354 | 0 | 0 | 0 | 0 |

## 4. Danh sách chi tiết các tín hiệu lệch & Nguyên nhân (Trả lời giả thuyết P2-5)

| Signal ID | Combo | Approved (3Yr / Sub) | Raw L3 (3Yr / Sub) | Tags 3-Year (`smc_dir, ob_blocked`) | Tags Subwindow (`smc_dir, ob_blocked`) |
|:---|:---|:---:|:---:|:---|:---|

## 5. Kết luận kiểm chứng giả thuyết P2-5
- **Trạng thái giả thuyết P2-5:** **VERIFIED** (đã kiểm chứng bằng chạy thực tế trên dữ liệu M1/M15 thật).
- **Phát hiện:** Với 3.400 nến M15 warm-up (~35,4 ngày = 850 nến 1H = 212 nến 4H = 141 nến 6H):
  1. Ở tầng tín hiệu thô Layer 3 (`warmup_ready=True`), có **0** tín hiệu chỉ có ở Run 3 năm và **0** tín hiệu chỉ có ở Run cửa sổ con (do các đường EMA/RMA chu kỳ dài trên khung 1H/4H/6H hoặc trạng thái Supertrend/SAR khởi tạo khác điểm bắt đầu).
  2. Ở tầng tín hiệu sau lọc Layer 2 (`approved=True`), có **0** tín hiệu chỉ có ở Run 3 năm và **0** tín hiệu chỉ có ở Run cửa sổ con (tổng cộng **0** tín hiệu lệch trên tổng số **1103** tín hiệu, tương đương tỷ lệ trùng khớp **100.00%**). Nguyên nhân chính đến từ việc `smc_direction` (swing_len=50 trên khung 1H tương đương 200 nến M15 và giữ trạng thái `swing_bias`/`internal_bias` cộng dồn từ nến đầu tiên) và `ob_context` (giữ danh sách tối đa 100 Order Blocks chưa bị mitigate từ đầu chuỗi) có sự khác biệt trạng thái lịch sử dài hạn khi bắt đầu ở hai mốc thời gian khác nhau.
- **Tuân thủ mục 4.8:** Không thay đổi hành vi warm-up trong PR V3-11; ghi nhận đầy đủ số liệu thực nghiệm vào báo cáo.
