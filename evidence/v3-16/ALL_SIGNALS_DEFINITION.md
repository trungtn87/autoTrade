# Định nghĩa Chuẩn Chế độ `ALL_SIGNALS` (V3-16 Bước 3 — Issue #102)

- **Ngày chốt:** 2026-11-15
- **Quyết định của Owner:** Từ Bước 3 trở đi, Research Lab chỉ dùng chế độ **`ALL_SIGNALS`** (mọi tín hiệu đều vào lệnh, khớp cách live đang chạy và quyết định giữ live không có cổng chặn Entry).

---

## 1. So sánh Hai Biến thể `ALL_SIGNALS` trong Codebase

| Tiêu chí | (a) Mode A — `ALL_SIGNALS` (`sim/engine.py` `_run_virtual_mode`) | (b) P0 — `ALL_SIGNALS_PORTFOLIO` (`AccountLedger` + `AllSignals`) |
| :--- | :--- | :--- |
| **Chính sách nhận lệnh** | `AllSignals()` — nhận mọi tín hiệu có nến M1 kế tiếp | `AllSignals()` — nhận mọi tín hiệu (chỉ chặn nếu thiếu ký quỹ khả dụng) |
| **Sổ tài khoản & VWAP** | Không dùng `AccountLedger`; mỗi leg tính độc lập | Dùng `AccountLedger` gộp vị thế VWAP theo `(symbol, side)` |
| **Cơ sở PnL** | 1 cột `net_pnl` (= own-entry) | 2 cột: `pnl_own_entry` (chất lượng entry riêng) và `net_pnl` (VWAP danh mục), cầu nối `basis_shift` (tổng = 0) |
| **Kiểm tra ký quỹ** | Không mô hình hóa | Có (`free_margin_at_open >= req_margin`, đòn bẩy 100x) |
| **M2M Snapshots (`snapshots.parquet`)** | **Không có** → Vốn thực M2M, Ký quỹ, DD M2M = `Không có` | **Có** (chuỗi 15 phút/M1 từ `AccountLedger`) → đầy đủ Vốn thực M2M, Ký quỹ, DD M2M |
| **Số liệu IDEAL (`0 bps`)** | `5,984` entries, `8,258` legs, Net `+410.7007`, Phí `598.3866` | `5,984` entries, `8,258` legs, Net `+410.7007`, Phí `598.3866` |
| **Số liệu BASE (`2 bps`)** | `5,984` entries, `8,258` legs, Net `+168.5995`, Phí `598.3660` (Closed DD `102.31`, không có M2M DD) | `5,984` entries, `8,258` legs, Net `+168.5995`, Phí `598.3660` (M2M DD `105.03`) |

### Kết luận đối chiếu
- Ở cấu hình vốn ban đầu `$1,000` (notional `$100`/entry, ký quỹ `$1`/entry), không có tín hiệu nào bị từ chối vì thiếu ký quỹ (`rejected_by_margin = 0`).
- Do đó **(a) và (b) khớp 100% về tập entry (`5,984`), tập leg (`8,258`), tổng phí và tổng lãi ròng (`+410.7007` ở IDEAL, `+168.5995` ở BASE)**.
- Điểm khác duy nhất nằm ở việc **(b) ghi nhận sổ tài khoản `AccountLedger` (VWAP, ký quỹ, M2M snapshots)** và tách hai cơ sở `pnl_own_entry` vs `net_pnl` (`basis_shift` tổng = 0).

---

## 2. Chốt Định nghĩa Chuẩn cho Báo cáo Lab

- **Định nghĩa chuẩn:** Chọn **(b) `ALL_SIGNALS_PORTFOLIO`** (`AllSignals` chạy qua `AccountLedger` với đầy đủ `snapshots.parquet` M2M và kiểm tra ký quỹ).
- **Lý do chốt:**
  1. **Khớp thực tế live BingX Hedge Mode:** Lệnh mở thực tế trên sàn dùng ký quỹ chung và gộp vị thế theo `(symbol, side)`.
  2. **Không che giấu rủi ro lớn nhất của `ALL_SIGNALS`:** Chỉ thông qua `AccountLedger` mới đo được **Vốn thực M2M**, **Sụt giảm M2M (`$105.03` ở BASE)**, **Ký quỹ đang dùng tối đa**, và **Mức ký quỹ tối thiểu** khi pyramiding tới 26 lệnh cùng lúc.
  3. **Bảo toàn tính nhất quán lịch sử:** Giữ nguyên 100% tập `5,984` entry / `8,258` leg và tổng Net PnL của Mode A Golden V3-13 (không sửa đổi hay ghi đè `runs/golden/v3-13/`).
