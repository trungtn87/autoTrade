# Kiểm tra golden

Đã băm SHA-256 toàn bộ 149 file trong `runs/golden` trước và sau thay đổi dashboard; `Compare-Object` trả về rỗng. Kết quả: hash golden không đổi. Kiểm tra scope bắt buộc bằng `git diff 7cbe662..HEAD -- autotrade_v3/live unified_app.py autotrade_v2 dashboard runs/golden autotrade_v3/strategy_core` cũng không có output.
