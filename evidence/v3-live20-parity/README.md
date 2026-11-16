# Đối chiếu tín hiệu: V3_LIVE_20_POSITIVE_FIXED_V1 (live) và AUDIT_20_POSITIVE_FIXED_TPSL_20261116 (lab)

## Nguồn

- Audit: `D:\AutoTrade\runs\lab\audits\AUDIT_20_POSITIVE_FIXED_TPSL_20261116\` (máy Owner).
- Run dùng để đối chiếu: run tái lập `RUN_20261116_132654_20FINAL_CROSS_SYMBOL_30_BINGX_PORTFOLIO_20231101_20261101`. Audit ghi nhận run này khớp run gốc `RUN_20261116_125134_...` với sai lệch trade 0; Claude không có run gốc nên không tự kiểm lại điểm này.
- Hash đã kiểm (sha256):
  - `signals.csv`: `c876551c0ab664977993d32b31d6f563ab7b0f5baea6a7b633bb5c2800a37ae3` (10.356 tín hiệu, 30 combo-symbol).
  - `signals.parquet`: `1ba90229727d20c050b686843cd8467b435c88cc0ec8b27a40cf00a063eeafbf`, khớp `ARTIFACTS.json`.
  - `signal_decisions.parquet`: `16acf6ebdd8b64bef99edef5a910481f4e60de1471be679ce95d1715ea1a0966`, khớp `ARTIFACTS.json`.
  - `run_manifest.json`: `ccd41ba99a616b5f8e44b3a5b94f94feae01c16288ecc80bb879052d4ef8b734`.
  - Script `scripts/run_20final_cross_symbol_30.py` (local, không có trên GitHub): `1f369dc140c653b274878d7d42265e74e1126772eef604174a62e716015e2210`.
- Lab `v3_lab` HEAD `5c471903090f174c349f062c0de8b555cadd2895` có trên GitHub. Tham số `final20/params/20final_v1.json` của BTC-C9 và ETH-C6 khớp registry.
- Dữ liệu và script dùng lại từ đợt 18 mã: `data/m1_raw` resample M15 bằng lab `DataLayer`, 2023-11-01 đến 2026-11-01; `scripts/v3_live18_parity.py --registry V3_LIVE_20_POSITIVE_FIXED_V1`. Lab signals nằm ngoài 20 mã bị loại trước khi so.

## Kết quả batch (`parity_batch.json`)

| Nhóm | Combo | Kết quả | Phân loại |
|---|---|---|---|
| FINAL14 + 2 mã mới | BTC 1, 2, 4, 6, 7, **9**, 10, 11; ETH 3, 4, **6**, 7, 10 | Khớp 100% từng tín hiệu (symbol, combo, side, close timestamp); entry lệch 0, TP/SL lệch tối đa 1,5e-11 | PASS |
| ETH-C1 H1_BOTH_1 | ETH 1 | 358/358 khớp | PASS |
| BTC-C1 BASELINE | BTC 1 | 245/245 khớp (audit cũng dùng BASELINE cho BTC-C1) | PASS |
| NEW6 | BTC 101, 106; ETH 101, 102, 103, 105 | Live là tập con của lab: 2.198 live đều khớp, 0 tín hiệu chỉ có ở live; lab có thêm 2.407 | EXPECTED (luật vị thế ảo) |

Tổng: 4.558 tín hiệu live đều khớp lab, 0 live-only, 0 trùng, không lệch timeframe.

## NEW6: vị thế ảo của live so với quyết định vào lệnh của audit (`new6_vs_audit_decisions.json`)

Audit chạy chế độ BINGX_PORTFOLIO: mỗi combo-symbol chỉ giữ một lệnh mở và lệnh đóng theo TP/SL trên nến M1. Live NEW6 giữ luật vị thế ảo của 20FINAL, đúng quyết định Owner tại PR #129. Kết quả trên 4.605 tín hiệu NEW6 của lab:

- 2.169 tín hiệu audit vào lệnh và live cũng phát.
- 2.373 tín hiệu audit bỏ do đang có lệnh mở, live cũng không phát.
- **34** tín hiệu audit vào lệnh nhưng live không phát (BTC101 4, ETH101 8, ETH103 2, ETH105 20).
- **29** tín hiệu audit bỏ nhưng live phát (BTC101 5, BTC106 1, ETH101 7, ETH105 16).

Phân loại: **EXPECTED** về mặt semantics. Vị thế ảo của live kết thúc theo luật 20FINAL, không theo đường giá TP/SL M1 của lab, nên khoảng 1,4% số lệnh NEW6 khác nhau. Đây không phải lỗi code. **Hệ quả: PnL audit của 6 mã NEW6 là UNVERIFIED cho live**, vì tập lệnh không trùng hoàn toàn. Không tuyên bố parity đầy đủ.

## Giới hạn

- `rolling` (gọi đúng `V18Engine.calculate` trên cửa sổ 3.400 nến tại mọi nến M15) **chưa chạy cho registry 20**. Lần chạy 18 mã trước đó bị dừng giữa chừng do container khởi động lại. Kết luận ở trên là parity thuật toán (batch). Phần lệch do cắt cửa sổ 3.400 nến là thuộc tính sẵn có của engine live 20FINAL: **UNVERIFIED**.
- `run_manifest.json` của run audit ghi `git_sha 51234e35…` và `code_dirty: true`, trong khi REPRODUCIBILITY ghi worktree `5c47190`. Script thêm cả hai thư mục vào `sys.path`. Nguồn chính xác của code lab: **UNVERIFIED**. Tín hiệu của 14 mã không thuộc NEW6 vẫn khớp tuyệt đối với live.
- `validation.json` của run: `overall_status FAILED`. Gate C2 crash (`float + NoneType`), các gate A/B/C1/C3 INCONCLUSIVE vì thiếu `dataset_manifest.json`. Đây là phần việc của Lab.
- Net PnL +$148,56 là của portfolio 30 mã (BINGX_PORTFOLIO, bỏ 3.707 tín hiệu do giới hạn đồng thời), **không** phải lợi nhuận kỳ vọng của live 20 mã. Live vào mọi tín hiệu.
