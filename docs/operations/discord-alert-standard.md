# AutoTrade V2 — Quy chuẩn cảnh báo Discord (09/11/2026)

## Mục tiêu
Người đọc trên điện thoại trả lời được trong vài giây: **điều gì xảy ra, lệnh có đang mở không, SL/TP đã xác minh chưa, cần làm gì, tra cứu ở đâu**. Không yêu cầu đọc mã lỗi Python hoặc chuỗi ID dài.

## Định dạng bắt buộc
1. **Tiêu đề + mức độ** (tiếng Việt, một dòng): `✅ ĐÃ ĐỐI SOÁT`, `⚠️ CẦN KIỂM TRA`, `🚨 VỊ THẾ CHƯA AN TOÀN`, `⛔ ENTRY TẠM CHẶN`.
2. **Ngữ cảnh**: `BTC-USDT · C14 · SELL` nếu có dữ liệu. Sử dụng nhãn combo hiển thị (104 => C14), không in C104.
3. **Trạng thái sự thật**: phân biệt `đã khớp`, `chưa khớp`, `chưa xác minh`; tuyệt đối không đồng nhất `chưa xác minh SL` với `không tồn tại SL`.
4. **Nguyên nhân tiếng Việt**: tối đa 4 dòng nguy cơ, loại bỏ event ID dài và exception.
5. **Cần làm**: một bước ngắn, khả thi, không tự động giả định có quyền đóng tài khoản.
6. **Giờ VN + mã tra cứu**: `L3.EXEC.EXIT_CLEANUP_RISK`; chi tiết kỹ thuật giữ trong `system_events` hoặc `discord_trade_outbox`.

## Phân nhóm / kênh
| Loại | Kênh | Mức độ | Tần suất |
| --- | --- | --- | --- |
| Tín hiệu Entry/TP/SL hoàn tất | `trade-btc` / `trade-eth` | ✅ | Mỗi sự kiện kết thúc, có batch |
| Entry khớp nhưng bảo vệ chưa xác minh | `trade-btc` / `trade-eth` và cảnh báo hệ thống phù hợp | 🚨 | Mỗi sự kiện, khử trùng lặp theo event |
| Đối soát vị thế gộp, cleanup-risk giống nhau | `ket_qua` | 🚨/⛔ | 1 lần/60 phút cho symbol + tập loại nguy cơ; thay đổi tập nguy cơ báo ngay |
| Candle stale | `ket_qua` | ⚠️ | Không báo lần thử đầu, nhóm 60 phút; recovery fail riêng 30 phút |
| Force Close chưa rõ kết quả, sự cố đóng vị thế mới | `ket_qua` | 🚨 | Báo ngay, KHÔNG bị cooldown 60 phút |
| Force Close đã xác minh | `ket_qua` | ⚠️ | Báo ngay |
| Sự cố không phân loại | `ket_qua` | ⚠️/🚨 theo severity | Giữ mã tra cứu, không in exception |

Không chỉnh sửa BingX hay trạng thái database trong tầng formatter.

## Tình huống đã biết — BTC C14 09/11/2026
Mẫu khi Entry đã khớp nhưng phản hồi SL chưa xác minh:

> 🚨 ENTRY ĐÃ KHỚP · BẢO VỆ CHƯA HOÀN TẤT  
> BTC-USDT · C14 · SELL  
> Entry dự kiến: 75129.2 | TP dự kiến: 73626.616 | SL dự kiến: 75630.06133333  
> Trạng thái: Entry đã khớp nhưng trạng thái SL/TP chưa được xác minh đầy đủ.  
> Cần làm: Kiểm tra vị thế, SL, TP trên BingX; không đặt Entry hoặc SL trùng.  
> Mã tra cứu: protection_sl_reconcile_pending

Mẫu khi phát hiện SL chưa xác minh và có lệnh thoát chưa nhận diện:

> 🚨 VỊ THẾ CHƯA ĐƯỢC XÁC MINH BẢO VỆ  
> BTC-USDT · C14  
> Vấn đề: Hệ thống chưa xác minh đầy đủ SL/TP; không có nghĩa BingX chắc chắn thiếu SL.  
> • Chưa xác minh được Stop Loss (SL)  
> • Chưa xác minh được Take Profit (TP)  
> • Có lệnh thoát chưa xác định được combo sở hữu  
> Cần làm: Kiểm tra SL/TP trên BingX. Không hủy lệnh thoát chưa rõ chủ sở hữu.  
> Mã tra cứu: L3.EXEC.EXIT_CLEANUP_RISK

## Bất biến an toàn
- **Chỉ** mô tả trạng thái đã có trong event payload; không gọi API BingX để "cải thiện" nội dung cảnh báo.
- Không tự thêm Entry, TP/SL hay đóng vị thế từ tầng Discord.
- Không bao giờ nhận ownership từ symbol + side + giá + khối lượng; cần WAL acknowledged orderId và xác minh nhận dạng độc lập.
- Không xuất API key, webhook, token hay raw error chứa dữ liệu nhạy cảm ra Discord.
- Đổi tên cảnh báo không đổi event_key hoặc lịch sử audit; database giữ log máy để điều tra.
- Đóng khẩn cấp **không** dùng tần suất cảnh báo nhóm để chặn thông báo mới.

## Nghiệm thu
- C14 Entry FILLED + SL POST unclear => không xuất hiện câu "Đặt lệnh thất bại" đơn thuần.
- C104 backend => người dùng thấy C14.
- 2 tín hiệu khác event ID nhưng cùng symbol/risk => 1 cảnh báo nhóm; risk khác báo ngay.
- Test chỉ dùng fake exchange, không gửi lệnh thật.
