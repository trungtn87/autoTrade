# Signal parity: V3_LIVE_18_H1_V2 (live) vs V3_18_COMBO_H1_BASELINE_V1 (lab)

- Baseline: `runs/v3_18_baseline_v2` trên máy Owner (git lab `51234e35`), run `RUN_20261116_112645_..._ALL_SIGNALS_20231101_20261101`.
  - `strategy_registry_18.json` sha256 `5f47aec3dc1a712574767c2f54dd06925e9f0a9352abaec282df20e9629c5857`
  - `signals.csv` sha256 `e8fc3e0914b36a8a5c7766e84ad7b959e9e4d0b069605aa0e0e3e2ad8d2a19f0` (6.814 tín hiệu)
- Dữ liệu: `data/m1_raw` (Binance M1 + 1 ngày BingX), resample M15 bằng lab `DataLayer`, 2023-11-01 → 2026-11-01, 105.216 nến M15/symbol.
- Script: `scripts/v3_live18_parity.py` (offline, không gọi BingX/DB).

## Kết quả

| Chế độ | Ý nghĩa | Kết quả |
|---|---|---|
| `batch` | Gọi engine live 1 lần trên toàn lịch sử | 12 combo FINAL14 + C1-ETH H1_BOTH_1 (13/18): **khớp 100% từng dòng** (2.407… xem bảng per_combo). 6 combo NEW6: live là **tập con** của lab (0 tín hiệu thừa); lab có thêm 2.407 tín hiệu do lab phát ở mọi nến 1H còn live giữ luật "vị thế ảo" của 20FINAL (Owner chốt 2026-11-16). Tổng 4.407 live khớp, 0 live-only, 0 trùng; entry lệch 0, TP/SL ≤ 1.5e-11 — `parity_batch.json` |
| `rolling` | Gọi đúng `V18Engine.calculate` như live (cửa sổ 3.400 nến M15 mới nhất) tại mọi nến M15 đóng | xem `parity_rolling.json` |

C1-ETH H1_BOTH_1: 358 tín hiệu (BUY 213, SELL 145), khớp đúng P3 `P3_H1_BUY_SELL_RESULTS.csv`.

Lần chạy đầu với chế độ NEW6 "every_bar" (bỏ theo quyết định Owner) đạt 6.814/6.814, chứng minh phần lệch NEW6 chỉ do luật vị thế ảo.

NEW6 bị lab-only (live không phát): BTC C11(101) 585, BTC C16(106) 4, ETH C11(101) 751, ETH C12(102) 3, ETH C13(103) 43, ETH C15(105) 1.020.

## Giới hạn (không phải PASS)

- Parity 18/18 so với baseline hiện tại **chưa đạt** cho 6 combo NEW6; cần baseline lab chạy lại với FIXED_TP_SL + luật NEW6 của live (việc của Lab, PR #124).

- Exit của baseline: 17/18 combo dùng 50% TP + 50% trailing; live dùng FIXED_TP_SL. Kết quả PnL baseline **không** áp dụng cho live (sai lệch chưa nghiệm thu).
- Số +$143.96 là chế độ BINGX_PORTFOLIO (bỏ 2.556 tín hiệu do giới hạn đồng thời, validation FAILED/D2); live vào mọi tín hiệu.
- `rolling` khác `batch` chỉ do cửa sổ 3.400 nến của live (EMA/SMC khởi tạo lại ở đầu cửa sổ) — thuộc tính sẵn có của engine live 20FINAL.
