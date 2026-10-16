# Database Contract C3

Migration: `001_contract_schema.sql`, PostgreSQL 15+ / Supabase project **Contract**. Chạy một lần bằng tài khoản quản trị, trong một transaction. Nếu schema/tên bảng đã tồn tại thì toàn bộ migration bị hủy; không dùng IF NOT EXISTS để che schema cũ. Không tự chạy migration khi API khởi động. Đã áp lên project `dvpqrwvfuiujvvnccpox` ngày 10/10/2026; chưa seed mẫu C2.

erd.dbml phản ánh schema001+002 đã test: 10 bảng,92 cột,8 FK nội bộ. Không dùng ERD đề xuất cũ làm DDL; CHECK/partial index/trigger/quyền theo SQL.

## Phạm vi

10 bảng public có prefix contract_: schema_versions, templates, template_versions, template_fields, drafts, draft_documents, generation_jobs, generation_results, analysis_jobs, notification_outbox. Function/trigger ở contract_private, không cấp cho client. ID User/Procedure/Document chỉ là UUID tham chiếu: không FK, không truy vấn database của service nhóm.

Mẫu system không có owner, personal có owner và không thể hiện công khai. Draft chỉ tạo từ version published của mẫu system hiện/không deleted hoặc mẫu personal đúng chủ. Ẩn/xóa mềm mẫu không đổi version hay nháp cũ. Template/draft không được hard-delete. Published version và field bất biến, phiên bản mới có số tăng riêng theo template (unique); C4 chịu trách nhiệm cấp số transaction-safe. Snapshot nguồn đã gán không được đổi.

Nháp ghim version/owner, còn cho phép thiếu trường required để lưu dở; required/loại giá trị/placements chi tiết phải validate ở API C4/C7/C8. JSON bị giới hạn dung lượng. Document link cùng owner với draft, không trùng và tối đa100; không chứng minh tài liệu có thật/đúng chủ trên Document — API phải gọi kiểm tra ở C5/C8.

Generation ghim revision, field_values snapshot và danh sách document_ids đã sắp xếp UUID đúng với draft khi tạo. Chỉ một job queued/processing cho một draft. Job tạo và sửa draft/link dùng cùng row lock, đã test race thật. Owner/kind/idempotency_key unique: service so request_hash để trả job cũ hoặc409, DB không tự định nghĩa HTTP replay. Retry tạo job mới/key mới sau job failed; không reset terminal job. Preview chỉ PDF. Result owner/format phải khớp job, không sửa/xóa, storage key không là URL/token. Result + chuyển completed phải cùng transaction; deferred constraint chặn completed thiếu result và result của job chưa completed.

Analysis ghim đúng template/version/owner personal, trạng thái queued→processing→needs_review→confirmed hoặc failed; confirmed đòi version published. Pipeline mapping_revision/kiểm tra placement/confidence/preview ở C8 còn chưa triển khai. Outbox chỉ giữ job ID và lịch giao, không chứa JWT hoặc nội dung hợp đồng; C9 mới thêm orchestration/delivery, chưa tự gửi thông báo.

## Quyền và cấu hình

RLS bật trên tất cả10 bảng, revoke quyền public/anon/authenticated, không có policy cho client. JWT User tự ký không phải Supabase auth.uid(); FE không gọi bảng trực tiếp. Backend dùng login PostgreSQL riêng/privileged ở môi trường triển khai. Grant cho service_role là quyền backend tin cậy; **không phải cơ chế owner-only tự động**. Mọi repository/API còn phải lấy owner từ JWT và lọc owner, admin chỉ thao tác catalog system, không được đọc mẫu/hợp đồng cá nhân. Không đổi role hoặc quyền ở User service.

Chỉ `CONTRACT_DATABASE_URL`, không fallback URL/key Document/User. PostgreSQL URI; host ngoài localhost phải sslmode=require/verify-ca/verify-full. Không cho query override host/hostaddr/service/options. Driver psycopg3, prepared statement cache tắt để phù hợp transaction pooler; timeout connect5s/statement5s/lock2s. Không log DSN/SQL exception. Kết nối Supabase thật bằng URL người phụ trách điền trong .env đã pass TLS và readiness; không khẳng định mọi chế độ pooler đều đã test.

`GET /api/v1/contracts/ready` (Gateway `/api/contracts/ready`) yêu cầu Bearer access token hoặc cookie hợp lệ. Kiểm tra chỉ đọc connection, marker version1, RLS của10 bảng và cột last_version từ002; thành công trả data={database:ready,schema_version:1}, thiếu cấu hình/DB/schema trả503 envelope an toàn. Đây không phải kiểm tra toàn bộ quyền DML, Storage, dữ liệu mẫu hoặc service nhóm. `/health` vẫn liveness, không tự kết nối DB.

Chưa tạo bucket/policy Storage hoặc cấp service key; C7 sẽ thiết kế bucket private và readiness file/worker. Không dùng key Document để truy cập Contract.

## Test local

Chỉ dùng container tạm có label xác nhận, PostgreSQL trong RAM, không publish port/network/volume. Không nạp .env thật. Cài requirements-test.txt; mỗi lần chạy cần container mới vì migration/roles tạo một lần.

```powershell
docker run --detach --rm --name codex-contract-c3-pg --label codex.c3.disposable=true --network none --tmpfs /var/lib/postgresql/data:rw -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=contract_schema_test postgres:17
$env:CONTRACT_TEST_CONTAINER='codex-contract-c3-pg'
python -m pytest tests/test_contract_database_schema.py tests/test_contract_foundation.py -q
docker stop codex-contract-c3-pg
```

Test database mặc định skip nếu không có env container tạm; CI thường chưa chạy PostgreSQL integration tự động. Kết quả C3 local: 25 test PostgreSQL thật, trong đó có concurrency; 9 test readiness/config mới; cùng hồi quy liên quan tổng228 pass. OpenAPI/Compose pass. CRUD catalog C4 đã triển khai; nháp/Storage/service nhóm C5–C9 chưa test liên thông.

## Kiểm tra Supabase thật — 10/10/2026

Đã kiểm tra project chưa có đối tượng Contract trước khi áp migration một lần. SHA256 SQL: `b8f601bdf1d5f9c37912a54145b8ae486d2e156dea26edb769240591b2db9555`.

Sau migration: 10 bảng, 91 cột, marker version1; RLS bật toàn bộ, anon/authenticated không có quyền SELECT/INSERT/UPDATE/DELETE, hai deferred completion guard bật. Hàm readiness production trả `database: ready`, `schema_version: 1`. Không thêm dữ liệu hồ sơ/hợp đồng, seed mẫu, bucket hoặc sửa database Document/User. .env chứa thông tin kết nối và được Git ignore.

Script triển khai ghim project, kiểm tra TLS và từ chối áp schema vào target đã có đối tượng Contract. Chỉ dùng `--apply` khi khởi tạo project trống. Để kiểm tra lại chỉ đọc, chạy từ backend với dependencies Contract/common_auth đã cài:

```powershell
python services/contract-service/scripts/apply_supabase_schema.py --project-ref dvpqrwvfuiujvvnccpox --verify --readiness
```

Lần chạy hồi quy offline sau triển khai: 212 test pass, gồm 9 test kiểm tra target/URI của script; một warning deprecation TestClient. Test offline không ghi dữ liệu vào Supabase. Readiness không thay thế test HTTP có JWT, API nghiệp vụ hoặc liên thông service nhóm.

## C4 — migration 002_admin_catalog.sql

Đã áp một lần lên đúng project Contract ngày 10/10/2026, SHA256 `64390b6320d40f377f39e078c46e7fdab5d5457af55e654ab80ba55309e5effb`. Thêm last_version để cấp số tăng dần, không dùng lại số của draft đã xóa; sửa guard chỉ cho xóa version draft system của mẫu chưa deleted, FK vẫn chặn version được tham chiếu. Published/source snapshot giữ bất biến. API xóa fields rồi draft version trong cùng transaction, không hard-delete template/draft hợp đồng.

Hiện tại: **10 bảng/92 cột/8 FK nội bộ**, RLS/client deny và deferred completion guard không đổi. Marker schema version vẫn1 cho nền C3; readiness C4 kiểm tra thêm cột last_version. Verify cloud chỉ đọc pass, không seed mẫu/dữ liệu/bucket. Script `--apply-admin` kiểm tra C3 trước và từ chối chạy lặp nếu C4 đã có; `--verify --readiness` dùng để kiểm tra lại.

Test C4 SQL chỉ nhận container tên/label được xác minh và port bind127.0.0.1, không nhận URL Supabase/.env. Chạy từ backend đã cài dependencies test:

```powershell
docker run --detach --rm --name codex-contract-c4-pg --label codex.c4.disposable=true --publish 127.0.0.1::5432 --tmpfs /var/lib/postgresql/data:rw -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=contract_admin_test postgres:17
$env:CONTRACT_ADMIN_TEST_CONTAINER='codex-contract-c4-pg'
python -m pytest tests/test_contract_admin.py -q
docker stop codex-contract-c4-pg
```

Container mới mỗi lần chạy: fixture tạo roles + 001 + 002. Dữ liệu test giả chỉ trong RAM và mất khi stop. Test C3 riêng tiếp tục kiểm tra schema001 để giữ hồi quy nền; không dùng nó thay kiểm tra C4.

## C5 — không migration mới

Nháp/link/revision dùng bảng C3 đã có, catalog C4 vẫn dùng last_version. C5 không tạo bucket/seed/hợp đồng thật và không đổi quyền SQL. Check cloud chỉ đọc vẫn pass (10 bảng/92 cột/RLS/marker1/readiness). Repository kiểm tra owner từ JWT và khóa draft; DB trigger giữ invariant, không tự xác minh UUID tài liệu/thủ tục ở service khác.

Test C5 opt-in chỉ container `codex-contract-c5-pg`, label `codex.c5.disposable=true`, bind127.0.0.1, database `contract_draft_test`; fixture nạp001+002 một lần. Dùng biến CONTRACT_DRAFT_TEST_CONTAINER để chạy tests/test_contract_drafts.py với DB tạm, không đặt CONTRACT_DATABASE_URL cloud cho test. Lần hồi quy đầy đủ C1–C5:360 pass, gồm10 SQL C5 +25 SQL C3 +9 SQL C4, dữ liệu giả không lên Supabase. Các container tạm được dọn sau kiểm tra.

## C6 — chỉ đọc, không migration

Gợi ý CCCD dùng snapshot read-only REPEATABLE READ của nháp/field ghim version, kiểm tra lại owner/revision sau HTTP. Không giữ transaction qua HTTP, không lưu profile/gợi ý hoặc tăng revision nháp. Không thay schema/RLS/bucket/seed trên Supabase.

SQL test C6 opt-in CONTRACT_SUGGESTION_TEST_CONTAINER=codex-contract-c6-pg, label codex.c6.disposable=true, bind127.0.0.1, database contract_suggestion_test; fixture nạp001+002 một lần. Chỉ dữ liệu giả; không dùng URI cloud. Kiểm tra gợi ý không ghi nháp, profile đổi không tự đổi nháp, nháp sửa lúc lấy profile trả409 và non-owner404 trước khi gọi Document.

## C7 — dùng schema hiện có

Job/result/outbox dùng001+002, không thêm migration. Lease180s dùng started_at, retry_count làm fencing/attempt; tối đa3 attempt. Snapshot/version bất biến, một active job/nháp; result/completed/outbox final thành công commit atomic. Cleanup khóa job và kiểm tra result trước xóa object attempt; không xóa prefix rộng. Bucket contract-files private50MiB/PDF+DOCX, config/key riêng cùng project Contract; không dùng DB URI để gọi Storage. Readiness /ready hiện vẫn chỉ DB.

SQL test C7 opt-in CONTRACT_GENERATION_TEST_CONTAINER=codex-contract-c7-pg, label codex.c7.disposable=true, bindlocalhost, database contract_generation_test. Fixture chỉ thay default created_at job thành hôm qua trong DB tạm để mô phỏng lease, giữ nguyên constraint/trigger. 7 SQL C7 pass; toàn hồi quy480 pass với54 SQL C3–C7. Không ghi cloud nháp/job/mẫu; Storage smoke chỉ PDF giả, đã xóa đúng file test. Không bật worker production hoặc sửa User/Document.
