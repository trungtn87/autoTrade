# AutoTrade V2 — Phase A và PR 1 (03/11/2026)

## Baseline đã kiểm tra

- Fetch `origin/main`: `71a46ce2d54bdd8376da59382ff1798593b7c7e1`.
- PR #54 đã merge; ancestry hợp lệ; working tree ban đầu sạch; không có PR mở.
- CI merge commit: [41559769616](https://github.com/trungtn87/autoTrade/actions/runs/41559769616),
  cả `offline-and-container` và `mobile-pwa` thành công.
- Không có AGENTS.md trong checkout. Một app Docker, một engine, một worker.
- Không kết nối Supabase production hay runtime Nhân Hòa trong phiên này.
  Số liệu production và lỗi `missing_read_credentials` do Owner cung cấp là
  đầu vào sự cố, không phải kết quả truy vấn mới. Deployed SHA chưa xác minh.

## Luồng dữ liệu và phần còn thiếu

`20FINAL → ExecutionService → Final14Executor → BingX` lưu trạng thái vào
`public.processed_targets`, target `bingx_state`. ProtectionJournal lưu trạng thái
trước POST và khôi phục conditional order; giữ nguyên cơ chế này.

**`terminal` hiện là entry/protection đã xử lý xong, KHÔNG phải trade CLOSED.**
Recovery execution bỏ qua terminal là hành vi hiện tại; analytics phải theo dõi
vị thế đã bảo vệ cho đến khi xác minh đủ exit, độc lập với trạng thái đó.

PR #54 đã chuyển collector sang `autotrade_v2.analytics`, chạy trong lifecycle V2;
Dashboard không khởi chạy collector. Collector dùng read key riêng, GET allowlist,
admission theo execution, pacing, 429 cooldown, advisory lock, cursor/resume và
raw UPSERT. Không viết lại các phần đang đúng này.

`collect/reconcile_database` hiện đọc state + raw orders/fills rồi `db.merge`
payload kết quả. Không có writer analytics từ vòng đời execution; mất read key
thì các entry mới không thành normalized results. Mỗi intent đã được gộp quantity
và exit types, nhưng toàn bộ money còn NULL. Hàm `as_fill` mặc định trường money
thiếu thành 0; PR money phải đọc bằng chứng raw, không coi giá trị này là xác minh.
Collector merge không kiểm soát revision có thể ghi đè evidence mới của V2.

Dashboard live đọc `trade_results` từ DB, không gọi BingX khi mở trang/chọn ngày/
chuyển symbol. Có calendar chung và UTC+7; giữ giao diện. Tuy nhiên:

- `results.py` đang gán TP=WIN, SL=LOSS, sai với mixed exits và phí.
- HTTP reader chỉ đọc sync **success**, không thể hiện lần sync thất bại mới nhất.
- UI bỏ qua `theory.available`, vẫn có thể hiển thị 0 khi schema chưa có.
- Ngày không có record chưa chứng minh không giao dịch; cần coverage/reconciliation.
- Khi money chưa rõ, lịch có thể hiện 0 thắng/thua; cần trạng thái chưa xác minh.

Phân loại: migration chưa áp dụng là thiếu schema; read credentials thiếu là cấu
hình runtime theo báo cáo Owner; normalized history và money thiếu là thiếu evidence;
writer lifecycle/overwrite/TP-vs-WIN/UI state là thiếu sót code.

## PR tuần tự

1. **PR 1 hiện tại:** migration bổ sung, validator thuần và API storage transaction,
   test PostgreSQL thực cục bộ, tài liệu contract. Chưa bật writer mới.
2. **PR 2 sau khi PR 1 được duyệt:** projection từ state đã xác nhận, retry/recovery
   ngoài execution lock; audit/cảnh báo riêng; không thêm POST/SL mutation; xác minh
   từng fill/exit và phục hồi cả state execution terminal nhưng trade chưa đóng.
3. **PR 3:** adapter collector ghi cùng contract, union evidence thay vì ghi đè;
   monetary attribution từ raw fills/fees/funding/margin có liên kết chính xác;
   CLI dry-run/validation và thống kê, không backfill production.
4. **PR 4:** trạng thái DB/coverage/sync tách biệt; TP/SL từ exit_types, WIN/LOSS từ
   net PnL; calendar ngày BTC+ETH, NULL trung thực; browser regression.
5. **PR 5 nếu cần:** ma trận đủ 15 acceptance cases, parity/CI và readiness thực tế.

Không mở PR phụ thuộc kế tiếp khi PR trước chưa được duyệt. Không tự merge/deploy.

## Contract PR 1

Giữ migration #54 nguyên vẹn. Migration mới thêm `revision` (legacy = 0), receipts
và constraints. Không update/delete raw/manual/result history. Unique partial index
ngăn một entry `(symbol, entry_order_id)` thành hai CLOSED intent. Nếu dữ liệu có
duplicate cũ, migration thất bại và rollback toàn transaction; không tự xóa dòng.
Constraints `NOT VALID` giữ nguyên legacy rows; chỉ kiểm tra ghi mới. Historical
validation là bước riêng sau đối soát, không phải startup action.

`validate_result` yêu cầu đầy đủ các trường, kể cả giá trị NULL; source rõ ràng,
evidence references, số hữu hạn và quantity/time/identity hợp lệ. PnL là **ròng**,
sau actual fees và mọi funding có thể quy thuộc; thiếu completeness thì PnL NULL.
Fees lưu khoản phí ròng theo quy ước positive=cost, negative=rebate. Margin phải là
actual initial margin có evidence, không tính notional/leverage. ROI=100*net PnL/
actual margin. Outcome theo dấu PnL; exit_types là tập loại thoát, không phải outcome.
Unknown Combo không được đoán; lưu UNRESOLVED. MONEY refs chỉ là tham chiếu: adapter
phải xác minh dữ liệu provider, tính duy nhất, asset, account và tính đầy đủ trước
khi đặt `money_verified=true`. Validator không tự chứng thực exchange.

`write_result(engine, payload, source=..., evidence_id=..., expected_revision=N)`:

- Payload phải có `contract_version=1`, `verification_source` và `evidence_refs`.
- ID receipt phải gồm namespace account/symbol/provider hoặc durable V2 revision;
  dùng cùng ID khi retry đúng snapshot; snapshot thay đổi dùng ID mới.
- Snapshot và receipt commit cùng transaction, một event_id chỉ có một current row.
- Retry receipt cũ trả `(revision_của_receipt, 'unchanged')`, không ghi lại snapshot
  cũ lên current row. Đây không phải khẳng định current row vẫn ở revision đó.
- Hai writer cùng revision: một commit, writer còn lại reload/reconcile rồi retry;
  không blind overwrite. Evidence trùng ID nhưng khác nội dung/intent báo conflict.
- Không thay confirmed symbol/side/entry ID/Combo, không xóa evidence/money đã biết;
  CLOSED không trở về OPEN/PARTIAL. Phát hiện evidence mâu thuẫn có thể đánh dấu
  UNRESOLVED, vẫn giữ facts và snapshot trước trong receipts để điều tra.
- SQL trigger chặn legacy UPDATE không tăng revision khi row đã thuộc writer mới.
  **Chưa bật writer cho đến khi collector được chuyển sang contract này.** Nếu bật
  trước, collector reconciliation sẽ báo lỗi thay vì xóa evidence của V2.
- Không gọi storage API trong order/protection path; timeout/DB exceptions phải do
  worker bắt, cảnh báo và retry từ durable state. PR 1 chưa có worker này.
- Không tự tạo schema hay tự fallback SQLite. Không chứa credentials/API headers.
- Receipts lưu lịch sử snapshot; tài khoản DB owner vẫn có quyền sửa, không phải
  tamper-proof audit. Không cấp Data API/public access; RLS + revoke cả hai bảng.

## Migration, vận hành và phê duyệt

Owner chỉ áp dụng migration trong maintenance window sau staging/review:

1. `20261103010119_v2_trade_results_draft.sql` (PR #54, production chưa có bảng).
2. `20261103021723_v2_result_storage_contract.sql` (PR 1).

Không chạy migration production trong phiên này. Local test chạy PostgreSQL 17 container
`--network none`, không publish port, dữ liệu synthetic, kiểm tra kết nối qua Unix
socket. DDL có lock timeout 5s; lỗi phải abort, không giữ khóa vô hạn.
CLI 2.81.3 tạo tên migration; archive SHA256 đã kiểm tra. Changelog và docs RLS
Supabase đã đọc; thay đổi minor PG về ltree/pgcrypto không áp dụng cho schema này.
`migration list --local` cần local Supabase stack; kiểm thử ở đây dùng Postgres
isolated thay vì khởi tạo cả stack hoặc kết nối production.

Nhân Hòa: giữ `DATABASE_URL`, trade credentials và Discord hiện có; bổ sung riêng
`BINGX_READ_API_KEY`, `BINGX_READ_API_SECRET` trong mục secret/environment của app.
Key riêng chỉ cần đọc lịch sử USD-M orders/fills, income/funding, force orders,
position history; tắt trade/order, transfer và withdrawal; giới hạn IP egress của
host nếu BingX hỗ trợ. Kiểm tra scope thực tế trên BingX; không dán key vào chat,
PR, log hoặc build args. Không fallback sang trade key. PR 1 không thêm env mới.

Backfill chưa được phép chạy: đóng băng khoảng bắt đầu
`2026-10-30T00:00:00+07:00` (= `2026-10-29T17:00:00Z`) đến mốc Owner duyệt,
giữ overlap với sync 01:07 VN. PR 3 phải cung cấp CLI fixed-range dry-run thật sự,
không ghi DB, dùng read key, và validation so sánh raw/result trước/sau. Không dùng
`collect --days N` như dry-run: lệnh hiện tại **ghi DB**. Cần Owner duyệt riêng
migration, read key, quota, khoảng thời gian và báo cáo dry-run trước backfill thật.

Rollback: dừng đúng một writer, phục hồi image/SHA trước; không xóa tables, receipts,
raw/manual data, không reset revision. PR 1 chưa bật writer nên app cũ dùng legacy
revision 0. Sau khi writer được bật ở các PR tiếp theo, rollback collector legacy
có thể bị revision guard chặn; chấp nhận cảnh báo và dùng adapter tương thích,
không gỡ guard bằng cách hạ revision hoặc xóa evidence. Không overlap hai engine.

Các gate production còn mở: deployed SHA, read key, actual margin/funding evidence,
quota/IP isolation, Owner-approved migration/backfill, restart và DB/HTTP evidence
thực tế. CI xanh không phải nghiệm thu giao dịch thật.

## Bằng chứng kiểm thử PR 1

- `scripts/check-offline.py dashboard`: 113 tests passed.
- `scripts/check-offline.py autotrade_v2`: 22 commands passed; unittest gồm 10 test
  cũ và 9 test contract mới. Runner loại credentials và chặn external sockets.
- `scripts/check-analytics-postgres.py`: baseline migration + migration bổ sung,
  payload history giữ nguyên, quantity/NULL/identity constraints, RLS/revokes,
  atomic snapshot/receipt rollback, stale-writer race, duplicate evidence/closure,
  retry sau restart/lỗi DB giả lập và collector lock/disconnect đều PASS.
- `check-container-metrics.py`, `check-unified.py`, Python compile, JS syntax và
  `git diff --check`: PASS. Không sửa file strategy/execution hoặc Dashboard UI.
- Docker production dependencies/image và network-none smoke: PASS. Build thẳng
  Dockerfile gặp CA proxy của môi trường này; build local dùng bản Dockerfile tạm
  chỉ thêm BuildKit secret CA cho pip, không đổi Dockerfile trong repository.
  CI build Dockerfile gốc trên GitHub mới là bằng chứng build chuẩn.
- Chưa nghiệm thu đủ Phase F: các test lifecycle/collector/UI mới tương ứng các
  PR sau, không gọi test validator là nghiệm thu live trading. Full-history parity
  sẽ kiểm tra qua CI khi có thay đổi liên quan; không chạy giao dịch thật.
