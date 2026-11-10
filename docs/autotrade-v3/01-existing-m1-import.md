# AutoTrade V3-01 — tiếp nhận M1 đã tải

Không tải lại dữ liệu BingX/Binance. Bước này chỉ kiểm tra và lập chỉ mục Parquet hiện có trên PC local. Bộ được báo cáo ở phiên khác: BTC/ETH M1 USDT perpetual, 2023-11-01 đến 2026-11-01 UTC, khoảng 1.578.240 nến mỗi mã, Binance kết hợp BingX, đã có manifest v4 và checksum. File thực chưa được gắn trong phiên này, nên PR chưa xác nhận dữ liệu thật đã đạt Gate A.

Cài riêng: python -m pip install -r autotrade_v3/requirements-data.txt

Chạy: python -m autotrade_v3.data_backtest.accept_existing --input-root "D:\\AutoTradeV3\\raw" --output-dir "D:\\AutoTradeV3\\catalog" --source-map "D:\\AutoTradeV3\\source-map.json" --checksums "D:\\AutoTradeV3\\checksums.json"

Source map JSON: {"files":[{"path":"BTCUSDT.parquet","provider":"BINANCE","market_type":"USDT_PERP"}]}.
Bản thực phải liệt kê mọi file, với provider đúng cho từng file, không tự nhận dạng Binance thành BingX. Nếu một Parquet chứa dữ liệu nhiều sàn lẫn nhau, cần chuẩn hóa/tách theo provider trước khi dùng công cụ này.
Checksums JSON: {"BTCUSDT.parquet":"<sha256>"} cho mỗi file; lấy digest từ manifest v4 GỐC, không tự tính lại digest trên file hỏng rồi chấp nhận.

Đầu ra: catalog/research.duckdb (view v3_m1 đọc trực tiếp file Parquet), catalog/v3_m1_qa.json (coverage, duplicate, OHLCV, từng nguồn tháng). Không thay dữ liệu gốc. Gate A chỉ PASS nếu toàn bộ 2 mã đầy đủ đúng phạm vi; chưa kiểm chứng dữ liệu 3 năm thật do chưa có byte. Nếu manifest v4 có schema khác, cần adapter chuyển đổi. Chỉ cho phép nguồn USDT_PERP, UTC phút và checksum xác nhận.

Tất cả chạy local; không có API key giao dịch, không chạm Supabase V2, không gọi mạng.

## Source QA versus hybrid QA — owner-provided manifest v4 summary

Reported dataset, not mounted in this workspace:

| Metric | BTCUSDT | ETHUSDT |
|---|---:|---:|
| Hybrid M1 rows | 1,578,240 | 1,578,240 |
| Binance USD-M | 1,576,800 | 1,576,800 |
| BingX supplement | 1,440 | 1,440 |
| Hybrid missing/duplicate | 0/0 | 0/0 |

A-HYBRID: 100% contiguous minutes using the correct source per candle.
A-BINANCE-SOURCE: 70/72 monthly source partitions PASS; Binance October 2026 is only 96.774% each symbol.
BingX supplementary candles on 2026-10-28 must retain BingX source; price basis is LAST, not MARK.
GO_RESEARCH (conditional on local revalidation) means M1 backtest can proceed on hybrid data, but NOT claim BingX-specific execution parity.

Source-map items now require price_type=LAST for trusted research readiness; if missing, QA remains INCONCLUSIVE.
For a single file containing both venues, set provider=MIXED and include per-candle provider (or exchange/venue) column. When a per-candle venue contradicts a file-level venue, QA marks bad_provenance and FAIL.
The QA JSON separately exposes hybrid_gate, binance_source_gate, research_ready and each month's BingX supplementation.
Original SHA256 evidence, source map and Parquet bytes still need to be accessed together on the user's PC for actual Gate A validation.
