# Báo cáo Nghiên cứu & Backtest `ETH-USDT/C11` Round 1 (`V3_LAB_4@4.0.0`)

- **Kết luận đề xuất**: **`H3` (`SELL_DEMARKER_STRONG`: `DeMarker(60) < 0.20`)** *(chỉ đề xuất ở mức nghiên cứu, chưa đưa `ETH/C11` vào danh sách chiến lược đã duyệt chính thức)*.
- **Nền tảng kiểm định**: `V3_LAB_4@4.0.0` (30 combo-symbol với 4 mã nâng cấp đã chốt: `ETH/C1 H1_BOTH_1`, `BTC/C14 H3a_KUMO_BAND_V1`, `ETH/C15 H3_ASYMMETRIC`, `ETH/C10 H3_ASYMMETRIC`; `BTC/C10` giữ `H0` và 25 mã còn lại giữ nguyên).
- **Dataset M1**: `2023-11-01` → `2026-11-01` (`dataset_hash = ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`), vốn `$1,000`, notional `$100/lệnh`, `ambiguity_policy = CONSERVATIVE`.

---

## 1. Xác nhận Baseline `ETH-USDT/C11` (`H0`)

- `combo_id = 101`, timeframe `1h`, `DeMarker(60)` (`BUY > 0.75`, `SELL < 0.25`), Layer 2 `SMC_STRICT_OB`, Fixed `TP = 1.8%`, `SL = 0.9%`, không trailing.
- Kết quả tái lập `H0` (`ONE_TRADE_PER_COMBO`):
  - `BASE`: **461 lệnh** (`133 BUY / 328 SELL`), **178 TP / 283 SL**, `Own Net PnL = +$10.3875`, `Closed MaxDD = $23.1114`.
  - `STRESS`: **467 lệnh** (`134 BUY / 333 SELL`), **175 TP / 292 SL**, `Own Net PnL = -$17.8424`, `Closed MaxDD = $34.3429`.
  - Khớp **100%** số liệu đối chứng từ `V3_LAB_2_FULL_30_UPGRADED_3.zip`.

---

## 2. Phân tích dữ liệu & Nguyên nhân gốc rễ (461 lệnh `BASE` của `H0`)

- **Phân rã `BUY` vs `SELL` của `H0`**:
  - `BUY` (`133` lệnh): `54 TP / 79 SL` (`WR 40.60%`), `Own BASE = +$10.1231`, `Own STRESS = -$0.3164`.
  - `SELL` (`328` lệnh): `124 TP / 204 SL` (`WR 37.80%`), `Own BASE = +$0.2644` (chỉ hòa vốn sau phí `$32.79`), `Own STRESS = -$17.5259` (lỗ nặng khi slippage tăng lên `5 bps`).
- **Giai đoạn `SELL` thua `2025-02` → `2025-04`**:
  - `2025-02`: `9 TP / 24 SL` (`-$9.3627` BASE)
  - `2025-03`: `2 TP / 5 SL` (`-$1.7404` BASE)
  - `2025-04`: `2 TP / 8 SL` (`-$4.8022` BASE)
  - Tổng 3 tháng: `13 TP / 37 SL` (`-$15.9053` BASE).
- **Giai đoạn `SELL` thắng `2026-06`**: `19 TP / 21 SL` (`+$15.5121` BASE).
- **Vì sao `H2` và `H4` gần như không có tác dụng lọc?**
  - `DeMarker(60) < 0.25` (chu kỳ 60 giờ) kết hợp `SMC_STRICT_OB` (`smc_dir == -1`) tự thân đã chọn các pha giảm kéo dài: `ADX(14)` trung bình là `~57.7` và `-DI > +DI` đúng trên **328/328** lệnh SELL (`H4` lọc **0** lệnh); `Close(4H) < EMA100(4H)` và `slope EMA100(4H) < 0` đúng trên **327/328** lệnh SELL (`H2` lọc đúng **1** lệnh).
- **Tác động của `H3` (`DeMarker(60) < 0.20`)**:
  - Loại bỏ **185/328** lệnh SELL yếu (`0.20 <= DeMarker < 0.25`), trong đó có **119 SL** và **66 TP** của `H0` (`BASE`).
  - Trong `2025-02` → `2025-04`: lọc bỏ `24 SL / 10 TP` (`-$7.6814`), giảm lỗ từ `-$15.91` xuống `-$8.22`.
  - Trong `2026-06`: mất `18 TP / 16 SL` (`+$13.9338`) vì phần lớn SELL tháng 6/2026 nằm trong vùng `0.20–0.25` — đây là đánh đổi đã được kiểm chứng.

---

## 3. So sánh hiệu quả `H0` – `H4` (`ONE_TRADE_PER_COMBO`)

| Variant | Profile | Trades (B / S) | TP / SL | WR (%) | Buy PnL | Sell PnL | Own Net PnL | Delta vs H0 | PF | Closed MaxDD | Removed H0 (TP/SL) | New Conc. |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| `H0` (Baseline) | `IDEAL` | 460 (131/329) | 182 / 278 | 39.57% | +$14.79 | +$16.62 | +$31.4108 | $0.00 | 1.1130 | $20.1067 | 0 | 0 |
| `H0` (Baseline) | `BASE` | 461 (133/328) | 178 / 283 | 38.61% | +$10.12 | +$0.26 | +$10.3875 | $0.00 | 1.0360 | $23.1114 | 0 | 0 |
| `H0` (Baseline) | `STRESS` | 467 (134/333) | 175 / 292 | 37.47% | -$0.32 | -$17.53 | -$17.8424 | $0.00 | 0.9418 | $34.3429 | 0 | 0 |
| `H1` (`BUY_ONLY`) | `BASE` | 133 (133/0) | 54 / 79 | 40.60% | +$10.12 | $0.00 | +$10.1231 | -$0.2644 | 1.1257 | $13.0128 | 328 (124/204) | 0 |
| `H1` (`BUY_ONLY`) | `STRESS` | 134 (134/0) | 52 / 82 | 38.81% | -$0.32 | $0.00 | -$0.3164 | +$17.5260 | 0.9963 | $18.8879 | 333 (123/210) | 0 |
| `H2` (`4H_EMA100`) | `BASE` | 460 (133/327) | 178 / 282 | 38.70% | +$10.12 | +$1.29 | +$11.4081 | +$1.0206 | 1.0397 | $23.1114 | 1 (0/1) | 0 |
| `H2` (`4H_EMA100`) | `STRESS` | 466 (134/332) | 175 / 291 | 37.55% | -$0.32 | -$16.48 | -$16.7914 | +$1.0510 | 0.9451 | $34.0557 | 1 (0/1) | 0 |
| **`H3` (`DeM < 0.20`)** | **`IDEAL`** | **281 (131/150)** | **118 / 163** | **41.99%** | **+$14.79** | **+$22.82** | **+$37.6050** | **+$6.1942** | **1.2307** | **$10.5040** | **187 (69/118)** | **8** |
| **`H3` (`DeM < 0.20`)** | **`BASE`** | **283 (133/150)** | **117 / 166** | **41.34%** | **+$10.12** | **+$17.12** | **+$27.2461** | **+$16.8586** | **1.1609** | **$10.9859** | **185 (66/119)** | **7** |
| **`H3` (`DeM < 0.20`)** | **`STRESS`** | **286 (134/152)** | **114 / 172** | **39.86%** | **-$0.32** | **+$7.83** | **+$7.5104** | **+$25.3528** | **1.0416** | **$14.3889** | **186 (65/121)** | **5** |
| `H4` (`ADX>20,-DI>+DI`) | `BASE` | 461 (133/328) | 178 / 283 | 38.61% | +$10.12 | +$0.26 | +$10.3875 | $0.00 | 1.0360 | $23.1114 | 0 (0/0) | 0 |
| `H4` (`ADX>20,-DI>+DI`) | `STRESS` | 467 (134/333) | 175 / 292 | 37.47% | -$0.32 | -$17.53 | -$17.8424 | $0.00 | 0.9418 | $34.3429 | 0 (0/0) | 0 |

---

## 4. Tác động danh mục 30 mã (`BINGX_PORTFOLIO`)

| Variant | Profile | Port Trades | Port Net PnL | ROI (%) | M2M MaxDD ($) | M2M MaxDD (%) | Closed MaxDD ($) | ETH/C11 VWAP PnL |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `H0` | `BASE` | 6,237 | +$197.5788 | +19.76% | $97.6132 | 9.47% | $92.9737 | +$9.7322 |
| `H0` | `STRESS` | 6,262 | -$219.1916 | -21.92% | $222.8772 | 22.25% | $221.7923 | -$19.2052 |
| `H1` | `BASE` | 5,909 | +$197.3143 | +19.73% | $90.5114 | 8.52% | $90.1057 | +$10.9752 |
| `H1` | `STRESS` | 5,929 | -$201.6657 | -20.17% | $205.3513 | 20.50% | $204.2664 | +$1.3911 |
| `H2` | `BASE` | 6,236 | +$198.5994 | +19.86% | $97.6132 | 9.47% | $92.9737 | +$10.7528 |
| `H2` | `STRESS` | 6,261 | -$218.1407 | -21.81% | $221.8263 | 22.14% | $220.7414 | -$18.1543 |
| **`H3`** | **`BASE`** | **6,059** | **+$214.4374** | **+21.44%** | **$90.5114** | **8.60%** | **$90.1057** | **+$29.2112** |
| **`H3`** | **`STRESS`** | **6,081** | **-$193.8389** | **-19.38%** | **$197.5245** | **19.72%** | **$196.4396** | **+$8.3981** |
| `H4` | `BASE` | 6,237 | +$197.5788 | +19.76% | $97.6132 | 9.47% | $92.9737 | +$9.7322 |
| `H4` | `STRESS` | 6,262 | -$219.1916 | -21.92% | $222.8772 | 22.25% | $221.7923 | -$19.2052 |

---

## 5. Độ bền theo năm, độ nhạy chi phí & kiểm soát Overfitting

- **PnL `H3` vs `H0` theo từng năm (`ONE_TRADE_PER_COMBO`)**:
  - `2024`: `H3` `+$17.8316` (`BASE`) / `+$10.5153` (`STRESS`) vs `H0` `+$3.1860` (`BASE`) / `-$7.0444` (`STRESS`).
  - `2025`: `H3` `+$3.5889` (`BASE`) / `-$6.4648` (`STRESS`) vs `H0` `-$6.3731` (`BASE`) / `-$20.5702` (`STRESS`).
  - `2026`: `H3` `+$5.8256` (`BASE`) / `+$3.4599` (`STRESS`) vs `H0` `+$13.5746` (`BASE`) / `+$9.7722` (`STRESS`). *(Lưu ý: không coi 2026 là kiểm định ngoài mẫu độc lập.)*
- **Phân bổ theo tháng**: Top 5 tháng tốt nhất của `H3` `BASE` rải đều cả 3 năm (`2024-08 +$15.37`, `2025-05 +$11.21`, `2026-02 +$5.59`, `2026-08 +$3.90`, `2024-12 +$3.60`), không phụ thuộc một tháng hay một nhóm lệnh đơn lẻ.
- **Số mẫu còn lại**: `H3` còn **283 lệnh (`BASE`) / 286 lệnh (`STRESS`)** — vượt xa ngưỡng kiểm chứng 30 lệnh.
- **Độ nhạy chi phí (`TP ↔ SL` flip `BASE` → `STRESS`)**: `H0` có `5` lệnh lật `TP → SL`; `H3` giảm xuống còn `3` lệnh lật `TP → SL` (`0` lệnh `SL → TP`).

---

## 6. Đối chiếu 6 tiêu chuẩn đánh giá & Kết luận

`H3` (`SELL_DEMARKER_STRONG: DeMarker(60) < 0.20`) **đáp ứng đủ 6/6 tiêu chuẩn**:
1. Own PnL `STRESS` vượt `H0` (`+$7.5104` vs `-$17.8424`).
2. `BASE` tăng mạnh (`+$27.2461` vs `+$10.3875`, `+162%`).
3. Closed MaxDD giảm hơn 50% (`$10.99` vs `$23.11` `BASE`; `$14.39` vs `$34.34` `STRESS`).
4. Hiệu quả đa giai đoạn trên cả 2024, 2025 và 2026 (`BASE`), cỡ mẫu lớn (`283–286` lệnh).
5. Không làm sai quy tắc tạo tín hiệu, `TP/SL` (`1.8% / 0.9%`) hay giới hạn concurrency.
6. Portfolio động 30 mã tăng `+$16.86` (`BASE`) và `+$25.35` (`STRESS`), đồng thời giảm `M2M MaxDD`.

**Kết luận**: Đề xuất **`H3`** để Owner xem xét cho vòng duyệt/tích hợp tiếp theo (chưa đưa vào `V3_LAB_4` chính thức trong vòng này).
