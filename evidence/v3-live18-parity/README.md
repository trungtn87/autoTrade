# Signal parity: V3_LIVE_18_H1_V2 (live) vs V3_18_COMBO_H1_BASELINE_V1 (lab)

- Baseline: `runs/v3_18_baseline_v2` trên máy Owner (git lab `51234e35`), run `RUN_20261116_112645_..._ALL_SIGNALS_20231101_20261101`.
  - `strategy_registry_18.json` sha256 `5f47aec3dc1a712574767c2f54dd06925e9f0a9352abaec282df20e9629c5857`
  - `signals.csv` sha256 `e8fc3e0914b36a8a5c7766e84ad7b959e9e4d0b069605aa0e0e3e2ad8d2a19f0` (6.814 tín hiệu)
- Dữ liệu: `data/m1_raw` (Binance M1 + 1 ngày BingX), resample M15 bằng lab `DataLayer`, 2023-11-01 → 2026-11-01, 105.216 nến M15/symbol.
- Script: `scripts/v3_live18_parity.py` (offline, không gọi BingX/DB).

## Kết quả

| Chế độ | Ý nghĩa | Kết quả |
|---|---|---|
| `batch` | Gọi engine live 1 lần trên toàn lịch sử (cùng cách lab tính) | **6.814/6.814 khớp**, 0 thiếu, 0 thừa, 0 trùng; giá entry lệch 0, TP/SL lệch ≤ 1.5e-11 (làm tròn float); 18/18 combo khớp từng dòng — `parity_batch.json` |
| `rolling` | Gọi đúng `V18Engine.calculate` như live (cửa sổ 3.400 nến M15 mới nhất) tại mọi nến M15 đóng | xem `parity_rolling.json` |

C1-ETH H1_BOTH_1: 358 tín hiệu (BUY 213, SELL 145), khớp đúng P3 `P3_H1_BUY_SELL_RESULTS.csv`.

## Giới hạn (không phải PASS)

- Exit của baseline: 17/18 combo dùng 50% TP + 50% trailing; live dùng FIXED_TP_SL. Kết quả PnL baseline **không** áp dụng cho live (sai lệch chưa nghiệm thu).
- Số +$143.96 là chế độ BINGX_PORTFOLIO (bỏ 2.556 tín hiệu do giới hạn đồng thời, validation FAILED/D2); live vào mọi tín hiệu.
- `rolling` khác `batch` chỉ do cửa sổ 3.400 nến của live (EMA/SMC khởi tạo lại ở đầu cửa sổ) — thuộc tính sẵn có của engine live 20FINAL.
