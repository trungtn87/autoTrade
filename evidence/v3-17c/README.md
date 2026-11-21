# Bằng chứng V3-17c: Bộ thu dữ liệu BingX & Supabase chỉ-đọc cục bộ

## Phạm vi triển khai
- Module thu dữ liệu: `autotrade_v3/collector_v3/`
- Script đăng ký lịch Windows Task Scheduler: `scripts/register_collector_task.ps1`
- Kiểm thử: `autotrade_v3/tests/test_collector_v3.py`
- Kho dữ liệu cục bộ (ngoài Git): `D:\AutoTrade\data\bingx\`

## Lệnh vận hành
- Chạy thu dữ liệu: `python -m autotrade_v3.collector_v3 run`
- Chạy thử không ghi đĩa: `python -m autotrade_v3.collector_v3 run --dry-run`
- Chạy một nguồn cụ thể: `python -m autotrade_v3.collector_v3 run --only market_candles_1m`
- Kiểm tra toàn vẹn SHA-256: `python -m autotrade_v3.collector_v3 verify`
- Đăng ký lịch chạy mỗi giờ (:20) + khi đăng nhập + chạy bù: `powershell -ExecutionPolicy Bypass -File scripts/register_collector_task.ps1`
- Hủy lịch: `powershell -ExecutionPolicy Bypass -File scripts/register_collector_task.ps1 -Unregister`

## Nguyên tắc an toàn & dữ liệu
- Không sử dụng khóa giao dịch BingX; chỉ gọi GET công khai tới `open-api.bingx.com`.
- Nguồn tài khoản & sự kiện live (`autotrade_analytics.*`, `autotrade_v3.*`) đọc chỉ-đọc từ Supabase qua `SUPABASE_DB_URL` / `DATABASE_URL` với `SET TRANSACTION READ ONLY` và bộ lọc chỉ cho phép `SELECT`.
- Bỏ qua bid/ask (`market/spread`) theo quyết định ngày 15/11 của Owner.
- Bảo toàn ID dạng chuỗi (`str`), số tiền/giá/khối lượng dạng `Decimal` chính xác, raw JSON không ghi đè, trạng thái/cursor ghi nguyên tử kèm khóa chống chạy chồng, manifest + SHA-256 + `coverage.parquet`.
