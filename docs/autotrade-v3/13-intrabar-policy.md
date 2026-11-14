# Chính sách xử lý nhập nhằng trong nến M1 (V3-13)

Trong mô phỏng dùng dữ liệu nến OHLC M1, khi hai ngưỡng giá (ví dụ TP và SL) cùng nằm trong `[low, high]` của một nến, ta không biết thứ tự thời gian thực giữa đỉnh và đáy trong phút đó. Module `autotrade_v3/sim/ambiguity.py` chuẩn hoá bốn tình huống nhập nhằng và bốn chính sách xử lý.

## 1. Bốn tình huống nhập nhằng
1. **`TP_AND_SL` (i):** Nến chạm cả `tp_price` và `sl_price`.
2. **`SL_AND_TRAIL_ACT` (ii):** Chưa kích hoạt trailing; nến chạm cả `sl_price` và `act_price`.
3. **`TRAIL_ACT_AND_CB` (iii):** Chưa kích hoạt trailing; nến chạm `act_price` (không chạm `sl_price`), tạo đỉnh mới và đồng thời hồi về chạm `trail_stop = max(sl, peak*(1-cb))` (với BUY) hoặc `min(sl, peak*(1+cb))` (với SELL).
4. **`TRAIL_PEAK_AND_STOP` (iv):** Đang trailing; nến vừa lập đỉnh mới (`high > peak` với BUY, `low < peak` với SELL) vừa chạm stop (`old_stop` hoặc `new_stop`).

## 2. Bảng chính sách (ví dụ số cho lệnh `BUY`, `entry = 100.0`, `tp = 102.0`, `sl = 99.0`, `act = 100.5`, `cb = 0.5%`)

| Tình huống | Ví dụ nến M1 | `LEGACY` | `CONSERVATIVE` (mặc định mới) | `OPTIMISTIC` | `OHLC_PATH` |
| --- | --- | --- | --- | --- | --- |
| **(i) `TP_AND_SL`** | `O=100, H=102.5, L=98.5, C=101` (nến tăng `O->L->H->C`) | Thoát `SL_INTRABAR_AMBIGUOUS` @ `99.0` | Thoát `SL_INTRABAR_AMBIGUOUS` @ `99.0` | Thoát `TP` @ `102.0` | `L` trước `H` -> `SL_INTRABAR_AMBIGUOUS` @ `99.0` (nếu nến giảm `O->H->L->C` -> `TP` @ `102.0`) |
| **(ii) `SL_AND_TRAIL_ACT`** | `O=100, H=101.0, L=98.5, C=100.8` (nến tăng) | Thoát `SL_INTRABAR_AMBIGUOUS` @ `99.0` | Thoát `SL_INTRABAR_AMBIGUOUS` @ `99.0` | Không thoát; kích hoạt trailing `peak=101.0`, `stop=100.495` | `L` trước `H` -> `SL` @ `99.0` (nến giảm: kích hoạt @ `101.0` rồi `L` chạm `stop=100.495`) |
| **(iii) `TRAIL_ACT_AND_CB`** | `O=100, H=102.0, L=99.5, C=101.8` (nến tăng, `new_stop=101.49`) | Thoát `TRAIL` @ `101.49` | Thoát `TRAIL` @ `101.49` | Không thoát; giữ `peak=102.0`, `stop=101.49` | `L` trước `H` -> không thoát (`peak=102.0`, `stop=101.49`); nến giảm -> thoát `TRAIL` @ `101.49` |
| **(iv) `TRAIL_PEAK_AND_STOP`** | Đang có `peak=101.0, old_stop=100.495`; nến `O=100.8, H=102.0, L=100.6, C=101.9` (`new_stop=101.49`, `L > old_stop`) | Thoát `TRAIL` @ `101.49` (`new_stop`) | Thoát `TRAIL` @ `101.49` (nếu `L <= old_stop` thì thoát @ `old_stop=100.495`) | Không thoát; giữ `peak=102.0`, `stop=101.49` (chỉ thoát @ `101.49` nếu `L <= old_stop`) | `L` trước `H` và `L > old_stop` -> không thoát; nến giảm -> thoát `TRAIL` @ `101.49` |
