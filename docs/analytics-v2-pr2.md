# PR 2 — V2 Lifecycle Writer & Recovery

## Baseline và dependency

Parent đã được Owner duyệt: PR #55, HEAD
`d9d9b39f69afdc294e575e22c3552b9abda90d4a`.
Nhánh PR 2: `feat/v2-lifecycle-writer`; base PR là
`feat/v2-result-storage-contract`, **không phải main 71a46ce**.
Chỉ retarget về main sau khi PR #55 được merge. Không tự merge/deploy.
Hai workflow offline/container/mobile và parity thêm base branch này để CI chạy
đúng stacked PR; không chạy workflow production/backfill.

## Đường đi dữ liệu

1. Execution vẫn đọc/đặt/protect bằng các call sẵn có. `capture_entry` chỉ giữ các
   scalar fields từ confirmation response đã đọc; tối đa 32 snapshots, dedup exact.
   Không giữ credentials, headers, giá lý thuyết hay money. Không thêm request.
2. Metadata nằm trong state JSON, đi cùng **lần save_state/journal hiện có**.
   Không thêm DB transaction hoặc analytics callback trước khi đặt SL. Capture
   lỗi không raise vào execution. Nếu crash sau journal, metadata đã commit có
   thể đọc lại; nếu chưa đủ entry binding thì recovery tiếp theo mới xác nhận.
3. Cleanup giữ response đã được kiểm tra trong từng order view; trigger child
   giữ exact `triggerOrderId` của protection parent. Không đổi điều kiện cancel,
   SL size, close, TP 50% + trailing 50%, polling hoặc request guard.
4. Worker đọc `public.processed_targets WHERE target='bingx_state'` bằng pool
   riêng, **không gọi ExecutionStore/list_states hoặc lấy execution lock**.
   Đọc cả execution state terminal. Không gọi BingX, không tạo order hay sửa SL.
5. Projection thuần kiểm tra client/order/symbol/side/position identity, cộng
   cumulative executed quantity theo từng exact exit order một lần. Snapshot
   qua validator/transition guard và `write_result` của PR #55; snapshot + receipt
   commit nguyên tử. Duplicate/CAS conflict không blind overwrite.
   Payload `lifecycle_exits` giữ cumulative legs theo protection parent, không
   mất các leg đã xác nhận khi source view thiếu; parent/trigger child là cùng một
   leg. Nhiều child khác nhau hoặc quantity giảm bị từ chối. Legacy results có
   exit quantity nhưng chưa có leg proof cần PR 3 đối soát, không tự đoán mapping.

## Quy tắc bằng chứng

- OPEN: có entry query binding và positive provider-confirmed executedQty,
  symbol/side/Combo/order identity, cùng timestamp fill thực tế đủ xác minh.
- PARTIAL: có exact exit/trigger identities và 0 < closed_qty < opened_qty.
- CLOSED: entry FILLED đã xác nhận; tổng exact exit quantities bằng **chính xác**
  entry quantity; mọi phần thoát có timestamp fill; không dùng tolerance để đoán
  lượng còn lại. TP + trailing hoặc TP + SL vẫn một event_id.
- `terminal`, `cleanup=done`, hai lần flat position, SL/TP giá dự kiến hoặc sự
  vắng mặt của order không phải bằng chứng CLOSED.
- `opened_ms` là timestamp provider của fill sớm nhất đã xác nhận/ghi nhận;
  dùng filledTime/fillTime, hoặc updateTime khi FILLED/PARTIALLY_FILLED. Không dùng
  order creation time, close_time của candle, processed_at hay đồng hồ local.
  Không tự rewrite timestamp đã công bố khi lịch sử sớm hơn xuất hiện.
- Partial entry vẫn được theo dõi, nhưng không CLOSED chỉ vì exit bằng lượng
  entry khớp tạm thời. Entry chưa final hoặc timestamp/Combo/identity thiếu giữ
  UNRESOLVED; xung đột với trạng thái đã xác minh báo degraded và giữ snapshot cũ.
- Không tính PnL/fees/margin/ROI/outcome trong PR này. Giữ NULL; nếu ledger đã có
  money/proofs xác minh thì bảo toàn. Không lấy state.pnl, leverage hay strategy
  prices làm evidence. TP/SL và WIN/LOSS vẫn độc lập.
- Ownership collision kiểm tra toàn bộ durable states, không chỉ page hiện tại.
  Ambiguity không hạ CLOSED/PARTIAL: guard PR #55 từ chối và giữ lịch sử để điều tra.
- Capture không thể cứu bằng chứng chưa từng được persist trước process crash.
  State cũ/response thiếu client ID, symbol/side hoặc fill timestamps không được
  nâng thành completed trade. PR 3 phải bổ sung exact provider archive evidence.
  Truncation 32 snapshots được đánh dấu, không tự xác nhận CLOSED.

## Retry, restart, isolation

Worker chạy daemon trong cùng ứng dụng, không thêm service/port/dependency.
Pool một connection, overflow=0; pool timeout 1s, connect timeout 3s; đọc DB timeout
5s. Ledger dùng lock timeout 1s/statement timeout 5s theo PR #55. Không ghi execution
tables hoặc tạo schema. Missing migrations/DB outage báo failure và backoff 15s.

Mỗi page tối đa 100 states, keyset theo event_id, interval 5s. Hết scan quay về đầu;
restart luôn scan từ đầu, không phụ thuộc memory queue/cursor. Record lỗi không
chặn record sau; DB/schema outage dừng pass và backoff thay vì thử N connections.
Poison record và metadata thiếu được giữ nguyên để retry/đối soát, không xóa state.
CAS retry tối đa 3 lần, recompute với winner; evidence_id là hash snapshot/event,
retry đúng payload không tạo receipt mới. Commit mất ACK được nhận ra sau reload.

Trạng thái redacted ở `/engine/status.lifecycle_writer`; cảnh báo dùng EventReporter
hiện có, chỉ codes/class/counts. Notification lỗi không làm dừng worker. Shutdown
chỉ signal trước, stop trading workers/stream như cũ, join analytics tối đa 1s sau.

## Gate production — chưa được bật

Ứng dụng tạo `LifecycleWriterRuntime(..., enabled=False)` một cách tường minh.
Không có biến môi trường để bật. Disabled không tạo thread/pool/connection hoặc
provider call. Test/local integration inject enabled=True chỉ với synthetic DB.
PR 3 phải chuyển collector reconcile sang contract/revision/evidence union trước
khi review việc bật writer; legacy merge hiện có sẽ bị guard chặn nếu writer đã
sở hữu row. Không tự bật chỉ vì có credentials hay CI xanh.

PR 2 không thêm migration hoặc biến môi trường. Cần đủ **ba migration PR #55** khi
staging được Owner cho phép; không chạy migration production trong task này.
Backfill vẫn chưa được phép. Rollback image không drop ledger/receipts/state JSON;
metadata thêm là additive, engine cũ bỏ qua được. Không overlap hai trading writer.

## Verification

- Unit lifecycle: terminal-open, partial entry/exit, TP/SL/mixed/trailing,
  exact trigger identity, thiếu timestamps/Combo, no false flat closure,
  bounded redacted capture, CAS/retry, poison isolation, restart and production gate.
- Existing execution replay fake chạy trong khi analytics DB đang bị giữ treo:
  execution/protection hoàn tất, không thêm entry/close/cancel hoặc analytics I/O.
- PostgreSQL 17 cô lập, network none: OPEN → PARTIAL → CLOSED với history receipts;
  terminate backend thật giữa snapshot và receipt, rollback và restart retry;
  duplicate, missing table/recovery, global ownership và malformed state isolation.
- Chạy toàn bộ offline suites, Docker/no-network smoke, browser/PWA và strategy
  parity trên HEAD cuối trước bàn giao. Không dùng testnet/mainnet trading calls.

Không tuyên bố production acceptance: quota/deployed SHA/read keys/backfill, money
attribution và Dashboard data states còn ở các PR sau theo kế hoạch đã duyệt.
