# ACCEPTANCE FINAL SUMMARY — `BTC-USDT/C14` VALIDATION & CLEAN-TREE AUDIT

**Nhánh nghiên cứu:** `feat/v3-c14-loo-h2`  
**Phán quyết tổng thể:** **`PARTIAL` (Kỹ thuật & Clean-Tree: `PASS` | Độ bền chiến lược H2b & Live Parity: `INCONCLUSIVE`)**

---

## 1. Bảng Phán quyết Từng Hạng mục

| Hạng mục Nghiệm thu | Trạng thái | Bằng chứng |
|---|---|---|
| **A. Filter-Aware Validation** | **`PASS`** | [filter_aware_validation.py](file:///d:/AutoTrade_c14/autotrade_v3/lab/filter_aware_validation.py) (`FA1..FA5` PASS `27/27`), [test_filter_aware_validation.py](file:///d:/AutoTrade_c14/autotrade_v3/tests/test_filter_aware_validation.py) (`10/10` positive/negative tests PASS), baseline regression `55/55` PASS, validator gốc không bị sửa. |
| **B. Clean-Tree Reproducibility** | **`PASS`** | Chạy trên worktree sạch `d:\AutoTrade_c14_clean` tại commit `4ed1497` (`code_dirty=False` trên `27/27` runs), tái lập `0.0000` sai lệch Net PnL cho `H0`, `LOO-19`, `H2b` trên đủ 3 modes × 3 profiles. |
| **C. An toàn Phạm vi Bảo vệ** | **`PASS`** | `0` diff tại `autotrade_v3/live`, `autotrade_v2`, `unified_app.py`, `strategy_core`, `validation`, `runs/golden`. Không merge, không deploy. |
| **D. Standard Gate `B1` & `D2` trên LOO/H2b** | **`INCONCLUSIVE` / `FAIL` (Đã giải thích)** | `B1 = INCONCLUSIVE` (chưa có frozen ref cho `20FINAL_FIXED_V1@1`); `D2 = FAIL` trên validator chuẩn chỉ vì so `5,984` tín hiệu gốc với tổng sau lọc (`8` filter-recon viols + `1` M2M `1e-4`), đã được `FA1..FA5` kiểm chứng thay thế. |
| **E. Độ bền Chiến lược `H2b`** | **`INCONCLUSIVE` (Giữ nguyên)** | Đạt `+$348.22` (`BASE`, hơn H0 `+$10.21`), nhưng thua `LOO-19` ở `STRESS` (`+$66.80` vs `+$68.62`), M2M DD cao hơn `LOO-19` (`\$62.92` vs `\$57.24`), 6 tháng cuối vẫn âm (`-$1.18`), và vùng độ nhạy Kumo `0.3%–0.7%` chưa ổn định. |

---

## 2. Trả lời 6 Câu hỏi Nghiệm thu Bắt buộc

1. **Gate D2 đã thực sự được khắc phục ở phần nào?**
   - Đã xác định 100% `D2 FAIL` trên `LOO-19`/`H2a`/`H2b` xuất phát từ việc `gate_d2` so sánh `signals.parquet` (`5,984` tín hiệu trước lọc) với `analysis.symbols.*.signals` (đã trừ `FILTERED`), cộng thêm `1e-4` làm tròn M2M ở 3 run. Đã giữ nguyên validator chuẩn và xây dựng [filter_aware_validation.py](file:///d:/AutoTrade_c14/autotrade_v3/lab/filter_aware_validation.py) kiểm tra trực tiếp `Original = Kept + Dropped`, non-target invariance, order chain và Own/VWAP conservation (`27/27 PASS`, `10/10` negative tests PASS).

2. **Các Gate A/B/C/D còn FAIL hoặc INCONCLUSIVE không?**
   - **Standard Gates:** `A1, A2, B2, B3, C1, C2, C3, C4, C5, D1` đều **`PASS` 27/27** (sau khi bổ sung `dataset_manifest.json` và chạy trên code mới). Còn `B1 = INCONCLUSIVE` (27/27, thiếu frozen ref); `D2` chuẩn vẫn `FAIL` trên 18 run có lọc (nhưng `FA5` xác nhận không có lỗi accounting nào ngoài filter-recon và `1e-4` M2M rounding) và `INCONCLUSIVE` trên 3 run `H0 STRESS`.
   - **Filter-Aware Gates (`FA1..FA5`):** **`PASS` 27/27**, `0 FAIL`, `0 INCONCLUSIVE`.

3. **Có tái lập được kết quả trên clean tree không?**
   - **Có, tái lập chính xác `0.0000` sai lệch** trên cả 27 cấu hình (`H0`, `LOO-19`, `H2b` × 3 modes × 3 profiles) với `code_dirty = False` tại commit `4ed1497`.

4. **Các kết quả H0, LOO-19, H2b có thay đổi không?**
   - **Không đổi bất kỳ con số nào.** Toàn bộ Net PnL, số lệnh, TP/SL, Own PnL, VWAP PnL và M2M DD khớp 100% với báo cáo nghiên cứu ban đầu.

5. **Có đủ cơ sở để nghiệm thu kỹ thuật nhánh nghiên cứu không?**
   - **Có — đạt nghiệm thu kỹ thuật Local Lab (`PASS`)**: code sạch, zero look-ahead được chứng minh trên cả synthetic và dữ liệu thật, tái lập clean-tree 27/27 runs, filter-aware validation PASS 27/27 cùng 55/55 unit/regression tests.

6. **Vì sao H2b vẫn chưa đủ cơ sở triển khai live (nhưng giữ để nghiên cứu tiếp)?**
   - `H2b` (`Kumo >= 0.5%`) vẫn âm nhẹ ở `STRESS` (`-$1.8234` Own PnL), có M2M DD cao hơn `LOO-19` (`\$62.92` vs `\$57.24`), 6 tháng cuối `05/2026–10/2026` âm `-$1.18`, và ngưỡng `0.5%` chưa cho thấy cao nguyên tham số ổn định (`0.6%` tụt xuống `+$331.64`). Cần kiểm chứng trên dữ liệu prospective độc lập trước khi xem xét bất kỳ thay đổi nào cho live.
