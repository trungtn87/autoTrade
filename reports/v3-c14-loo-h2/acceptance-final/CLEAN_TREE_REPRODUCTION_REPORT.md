# CLEAN-TREE REPRODUCTION REPORT (`H0`, `LOO-19`, `H2b`)

**Worktree sạch:** `d:\AutoTrade_c14_clean`  
**Git SHA:** `4ed1497c5d1f2886c52564012e8b407861975833`  
**`code_dirty`:** **`False` (27/27 runs)**  
**Dataset Hash:** `ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628`  
**Signal Cache:** Đã xóa cache cũ trước khi chạy; toàn bộ tín hiệu được tính mới từ dữ liệu M1/M15 trên commit sạch (`H0__ALL_SIGNALS__IDEAL` = `COMPUTED`, 26 run sau dùng chính cache vừa tạo).  
**Bảng Manifest & Hashes:** [RUN_MANIFESTS_AND_HASHES.csv](file:///d:/AutoTrade_c14/reports/v3-c14-loo-h2/acceptance-final/RUN_MANIFESTS_AND_HASHES.csv)

---

## 1. Đối chiếu Kết quả Tái lập Clean-Tree vs Nghiên cứu Trước đó

| Variant | Mode | Profile | Prior Net PnL | Clean-Tree Net PnL | Sai lệch | Entries | C14 Trades (TP/SL) | M2M DD |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `H0` | `BINGX_PORTFOLIO` | `IDEAL` | +\$516.3583 | **+\$516.3583** | `0.0000` | 4,150 | 294 (91/203) | \$56.7360 |
| `H0` | `BINGX_PORTFOLIO` | `BASE` | +\$338.0100 | **+\$338.0100** | `0.0000` | 4,157 | 294 (89/205) | \$65.7158 |
| `H0` | `BINGX_PORTFOLIO` | `STRESS` | +\$57.2547 | **+\$57.2547** | `0.0000` | 4,168 | 298 (87/211) | \$81.9412 |
| `LOO-19` | `BINGX_PORTFOLIO` | `IDEAL` | +\$499.0916 | **+\$499.0916** | `0.0000` | 3,856 | 0 (0/0) | \$48.6193 |
| `LOO-19` | `BINGX_PORTFOLIO` | `BASE` | +\$331.9564 | **+\$331.9564** | `0.0000` | 3,863 | 0 (0/0) | \$57.2396 |
| `LOO-19` | `BINGX_PORTFOLIO` | `STRESS` | +\$68.6206 | **+\$68.6206** | `0.0000` | 3,870 | 0 (0/0) | \$76.3592 |
| `H2b` | `BINGX_PORTFOLIO` | `IDEAL` | +\$525.0876 | **+\$525.0876** | `0.0000` | 4,076 | 220 (73/147) | \$54.0186 |
| `H2b` | `BINGX_PORTFOLIO` | `BASE` | +\$348.2177 | **+\$348.2177** | `0.0000` | 4,083 | 220 (71/149) | \$62.9187 |
| `H2b` | `BINGX_PORTFOLIO` | `STRESS` | +\$66.7973 | **+\$66.7973** | `0.0000` | 4,091 | 221 (67/154) | \$81.8147 |

*(Toàn bộ 18 cấu hình `ALL_SIGNALS` và `ONE_TRADE_PER_COMBO` cũng tái lập chính xác tuyệt đối `0.0000` sai lệch, chi tiết trong `RUN_MANIFESTS_AND_HASHES.csv`.)*

---

## 2. Kiểm tra Đặc biệt `H2b` (Mục V)

- **`H2b BASE` (`ONE_TRADE_PER_COMBO` / `BINGX_PORTFOLIO`):** `220` giao dịch C14 (`71` TP / `149` SL), Own PnL C14 = `+$16.2613`, VWAP PnL C14 = `-$0.3972`, Portfolio Net PnL = `+$348.2177` (vs LOO-19 `+$331.9564`). **KHỚP 100%.**
- **`H2b STRESS`:** Portfolio Net PnL = `+$66.7973` (vs LOO-19 `+$68.6206`), H2b C14 Own PnL = `-$1.8234`. **KHỚP 100%.**
- **Ghi chú về `trades_content_sha256`:** Hash CSV chuẩn hóa mới trong `RUN_MANIFESTS_AND_HASHES.csv` khác chuỗi hash trong `AUDIT_36_RUNS_MATRIX_WITH_HASH.csv` do khác phương pháp tuần tự hóa cột/định dạng chuỗi, nhưng toàn bộ giá trị kinh tế (Net PnL, Fees, Entries, TP/SL, M2M DD) đều tái lập tuyệt đối `0.0000`.
