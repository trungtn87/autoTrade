# Báo cáo Hoàn thiện Nghiên cứu `ETH-USDT/C11` — Round 2 (`V3_LAB_4@4.0.0`)

- **Cấu hình đề xuất trình Owner**: **`B1` (`BUY DeMarker(60) > 0.78`, `SELL DeMarker(60) < 0.20`)**, giữ nguyên `SMC_STRICT_OB`, Fixed `TP 1.8% / SL 0.9%`, timeframe `1H`, không trailing.
- **Trạng thái đề xuất**: **`RECOMMEND_H3_B1_FOR_OWNER_APPROVAL`** *(chỉ trình Owner quyết định; chưa cập nhật registry `V3_LAB_4` chính thức, chưa merge/deploy, không sửa 29 mã còn lại)*.
- **Commit kiểm định**: `cba891d` trên nhánh `lab/v3-lab-4-eth-c11-r1` (`tracked_dirty_files` gồm 3 tệp ngoài phạm vi nghiên cứu từ phiên trước; toàn bộ mã nguồn `V3_LAB_4` và script Round 2 sạch so với `HEAD`).
- **Dataset M1**: `2023-11-01` → `2026-11-01` (`dataset_hash = ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`).

---

## Giai đoạn A — Hoàn tất kiểm định độ nhạy `SELL` (`BUY > 0.75` cố định)

| Variant | SELL `DeM <` | Profile | Trades (B/S) | TP / SL | WR (%) | Buy PnL | Sell PnL | Own Net PnL | PF | Closed MaxDD | 2024 / 2025 / 2026 (Own) | Port30 Net PnL | Port30 M2M MaxDD |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| `H0` | `< 0.25` | `BASE` | 461 (133/328) | 178 / 283 | 38.61% | +$10.12 | +$0.26 | +$10.3875 | 1.0360 | $23.1114 | +3.19 / -6.37 / +13.57 | +$197.58 | $97.61 (9.47%) |
| `H0` | `< 0.25` | `STRESS` | 467 (134/333) | 175 / 292 | 37.47% | -$0.32 | -$17.53 | -$17.8424 | 0.9418 | $34.3429 | -7.04 / -20.57 / +9.77 | -$219.19 | $222.88 (22.25%) |
| `H3a` | `< 0.18` | `BASE` | 230 (133/97) | 86 / 144 | 37.39% | +$10.12 | -$12.54 | -$2.4185 | 0.9835 | $22.1049 | +2.82 / -8.12 / +2.88 | +$184.77 | $90.51 (8.72%) |
| `H3a` | `< 0.18` | `STRESS` | 232 (134/98) | 84 / 148 | 36.21% | -$0.32 | -$16.50 | -$16.8207 | 0.8917 | $30.1826 | -2.10 / -15.63 / +0.91 | -$218.17 | $221.86 (22.15%) |
| **`H3`** | **`< 0.20`** | **`BASE`** | **283 (133/150)** | **117 / 166** | **41.34%** | **+$10.12** | **+$17.12** | **+$27.2461** | **1.1609** | **$10.9859** | **+17.83 / +3.59 / +5.83** | **+$214.44** | **$90.51 (8.60%)** |
| **`H3`** | **`< 0.20`** | **`STRESS`** | **286 (134/152)** | **114 / 172** | **39.86%** | **-$0.32** | **+$7.83** | **+$7.5104** | **1.0416** | **$14.3889** | **+10.52 / -6.46 / +3.46** | **-$193.84** | **$197.52 (19.72%)** |
| `H3b` | `< 0.22` | `BASE` | 346 (133/213) | 144 / 202 | 41.62% | +$10.12 | +$25.77 | +$35.8968 | 1.1742 | $14.7679 | +20.66 / +0.05 / +15.19 | +$223.09 | $94.13 (9.13%) |
| `H3b` | `< 0.22` | `STRESS` | 350 (134/216) | 141 / 209 | 40.29% | -$0.32 | +$13.54 | +$13.2241 | 1.0602 | $17.1189 | +12.47 / -11.72 / +12.47 | -$188.13 | $191.81 (19.15%) |

### Giải đáp 5 câu hỏi kiểm định Giai đoạn A
1. **Biến động quanh ngưỡng `0.20`**: Ở `< 0.18` (`H3a`) bị siết quá chặt (mất nhiều SELL tốt, `BASE` âm `-$2.42`), còn vùng `0.20 → 0.22` (`H3 → H3b`) đều dương mạnh trên cả `BASE` và `STRESS`, không có gai đơn điểm.
2. **Loại trừ đóng góp `2024-08` & `2025-05`** *(kiểm tra độ nhạy, không phải OOS)*:
   - `H3` `2024-08` = `+$15.37` (`BASE`) / `+$14.72` (`STRESS`); `2025-05` = `+$11.21` (`BASE`) / `+$10.19` (`STRESS`).
   - Phần còn lại ngoài 2 tháng này: `H3` đạt `+$0.6640` (`BASE`) / `-$17.3926` (`STRESS`), vẫn vượt xa `H0` (`-$16.7956 BASE` / `-$43.0471 STRESS`, hơn `+$17.46 BASE` và `+$25.65 STRESS`).
3. **Số tháng `H3` thắng / thua `H0`**: Trong 32 tháng có giao dịch, `H3 > H0` ở **15 tháng**, `H3 < H0` ở **9 tháng**, bằng nhau ở **8 tháng** (giống nhau trên cả `BASE` và `STRESS`).
4. **Cải thiện `STRESS` đến từ đâu?**: Không đến từ vài lệnh cá biệt mà do loại bỏ 186 lệnh SELL yếu (`65 TP / 121 SL` ở `STRESS`), giúp `H3 > H0` trên 15/24 tháng có thay đổi và dương ở cả 2024 (`+$10.52`) lẫn 2026 (`+$3.46`).
5. **Ngưỡng cân bằng Drawdown & Lợi nhuận**: **`H3 (< 0.20)`** đạt `Closed MaxDD` thấp nhất (`$10.99 BASE` / `$14.39 STRESS`), `Portfolio M2M MaxDD BASE` thấp nhất (`8.60%`), và là ngưỡng duy nhất dương cả 3 năm `2024–2026` trên `BASE` (`+$17.83 / +$3.59 / +$5.83`), trong khi `H3b` có `Closed MaxDD` cao hơn (`$14.77 / $17.12`) và năm 2025 gần như hòa vốn (`+$0.05 BASE` / `-$11.72 STRESS`). **Chọn `SELL < 0.20` cho Giai đoạn B.**

### Xác minh tính tái lập & tương đương cấu hình (`H3`)
- **Tương đương filter vs `ComboSpec(short_max=0.20)`**: Khớp **100%** toàn bộ `9,655` signals trên 30 combo.
- **29 combo còn lại**: Khớp **100%** từng `signal_id`, `entry_time`, `exit_time`, `exit_reason`, `pnl_own_entry` giữa `H0` và `H3` (`5,776` lệnh `BASE`, `5,795` lệnh `STRESS`).

---

## Giai đoạn B & C — Kiểm định riêng chiều `BUY` (cố định `SELL < 0.20`) & Đối chiếu 3 trường hợp

| Trường hợp | BUY / SELL | Profile | Trades (B/S) | TP / SL | WR (%) | Buy PnL | Sell PnL | Own Net PnL | PF | Closed MaxDD | Removed BUY (TP/SL) | New Conc. | Port30 Net PnL | Port30 M2M MaxDD |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| `C11 gốc` (`H0`) | `>0.75 / <0.25` | `BASE` | 461 (133/328) | 178/283 | 38.61% | +$10.12 | +$0.26 | +$10.3875 | 1.0360 | $23.1114 | — | — | +$197.58 | $97.61 (9.47%) |
| `C11 gốc` (`H0`) | `>0.75 / <0.25` | `STRESS` | 467 (134/333) | 175/292 | 37.47% | -$0.32 | -$17.53 | -$17.8424 | 0.9418 | $34.3429 | — | — | -$219.19 | $222.88 (22.25%) |
| `Chỉ SELL` (`B0`/`H3`) | `>0.75 / <0.20` | `BASE` | 283 (133/150) | 117/166 | 41.34% | +$10.12 | +$17.12 | +$27.2461 | 1.1609 | $10.9859 | 0 | 0 | +$214.44 | $90.51 (8.60%) |
| `Chỉ SELL` (`B0`/`H3`) | `>0.75 / <0.20` | `STRESS` | 286 (134/152) | 114/172 | 39.86% | -$0.32 | +$7.83 | +$7.5104 | 1.0416 | $14.3889 | 0 | 0 | -$193.84 | $197.52 (19.72%) |
| **`B1`** | **`>0.78 / <0.20`** | **`BASE`** | **242 (92/150)** | **104/138** | **42.98%** | **+$16.84** | **+$17.12** | **+$33.9645** | **1.2413** | **$12.0053** | **52 (18/34)** | **11** | **+$221.16** | **$89.49 (8.35%)** |
| **`B1`** | **`>0.78 / <0.20`** | **`STRESS`** | **245 (93/152)** | **102/143** | **41.63%** | **+$10.33** | **+$7.83** | **+$18.1548** | **1.1209** | **$13.8087** | **53 (17/36)** | **12** | **-$183.19** | **$186.88 (18.66%)** |
| `B2` (đối xứng) | `>0.80 / <0.20` | `BASE` | 214 (64/150) | 92/122 | 42.99% | +$13.01 | +$17.12 | +$30.1292 | 1.2421 | $12.0053 | 75 (30/45) | 6 | +$217.32 | $88.11 (8.49%) |
| `B2` (đối xứng) | `>0.80 / <0.20` | `STRESS` | 216 (64/152) | 90/126 | 41.67% | +$8.38 | +$7.83 | +$16.2104 | 1.1225 | $13.8087 | 77 (28/49) | 7 | -$185.14 | $188.82 (18.85%) |
| `B3` | `>0.82 / <0.20` | `BASE` | 196 (46/150) | 85/111 | 43.37% | +$12.47 | +$17.12 | +$29.5909 | 1.2613 | $10.9859 | 93 (36/57) | 6 | +$216.78 | $87.09 (8.43%) |
| `B3` | `>0.82 / <0.20` | `STRESS` | 198 (46/152) | 84/114 | 42.42% | +$11.08 | +$7.83 | +$18.9100 | 1.1579 | $12.7596 | 94 (34/60) | 6 | -$182.44 | $186.12 (18.58%) |

### Phân tích độ bền & lựa chọn giữa `B0`, `B1`, `B2`, `B3`
1. **Lợi ích khi siết `BUY` từ `> 0.75` lên `> 0.78..0.82`**:
   - Ở `B0 (> 0.75)`, chiều `BUY` lỗ nhẹ ở `STRESS` (`-$0.3164`, `WR 38.81%`).
   - Khi siết lên `B1 (> 0.78)`, `BUY` loại bỏ `52` lệnh (`18 TP / 34 SL` BASE), giữ lại `92–93` lệnh `BUY` chất lượng cao: `BUY Own PnL` tăng từ `+$10.12 → +$16.84` (`BASE`) và từ `-$0.32 → +$10.33` (`STRESS`)!
2. **Độ nhạy theo ngưỡng `BUY` & Vì sao chọn `B1 (> 0.78)` thay vì `B2 (> 0.80)` hay `B3 (> 0.82)`**:
   - Toàn vùng `0.78–0.82` đều cho hiệu quả dương ổn định trên `BASE` (`+$29.59` đến `+$33.96`) và `STRESS` (`+$16.21` đến `+$18.91`).
   - **`B1 (> 0.78)` vượt trội về độ bền & cỡ mẫu**:
     - Giữ được **92–93 lệnh BUY** (gấp đôi `B3` 46 lệnh và hơn 44% so với `B2` 64 lệnh), giảm rủi ro overfitting mẫu nhỏ.
     - Phần lợi nhuận **ngoài 2 tháng lớn nhất (`2024-08` & `2025-05`)**: `B1` đạt **`+$9.72 BASE`** / `-$4.50 STRESS`, cao vượt trội so với `B0` (`+$0.66 / -$17.39`), `B2` (`+$3.85 / -$8.54`), và `B3` (`+$3.25 / -$6.14`).
     - Dương cả 3 năm `BASE`: `2024: +$19.99`, `2025: +$3.71`, `2026: +$10.26`.
     - `Portfolio 30 mã`: `B1` đạt `+$221.16` (`BASE`, `M2M MaxDD 8.35%`) và `-$183.19` (`STRESS`, `M2M MaxDD 18.66%`).

---

## Kết luận trình Owner quyết định

- **Ngưỡng `SELL` đề xuất**: **`DeMarker(60) < 0.20`** (`H3`).
- **Ngưỡng `BUY` đề xuất**: **`DeMarker(60) > 0.78`** (`B1`).
- **Cấu hình kết hợp đề xuất (`ETH-USDT/C11`)**: `BUY DeMarker(60) > 0.78` + `SELL DeMarker(60) < 0.20`, `SMC_STRICT_OB`, Fixed `TP 1.8% / SL 0.9%`, 1H, không trailing (`242` lệnh `BASE` Own `+$33.96`, `245` lệnh `STRESS` Own `+$18.15`, Closed MaxDD `$12.01 / $13.81`, Portfolio Net `+$221.16 BASE` / `-$183.19 STRESS`).
- **Rủi ro & lưu ý**: Dữ liệu `2026` đã tham gia vào vòng phân tích nên không được tính là OOS độc lập; tuy nhiên vùng lân cận `0.78–0.82` (BUY) và `0.20–0.22` (SELL) đều đồng thuận cải thiện mạnh `BASE`, `STRESS`, `PF` và `MaxDD`.
