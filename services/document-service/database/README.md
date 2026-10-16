# Document database

T7: migration005 thêm document_notification_outbox + enqueue/immutable triggers, đã apply/verify đúng project09/10. Không backfill hoặc sửa file/profile/bucket. Browser không CRUD; service_role SELECT/UPDATE; dispatcher mặc định tắt. Setup scripts/setup_document_outbox.py --project-ref srrxrnohunfyyolbfsew (default chỉ đọc, --apply opt-in). [Bàn giao T7/T8](../../../docs/api/DOCUMENT_RELEASE.md). ERD hiện6 bảng, các mốc trước là lịch sử.

Luật sư (15/10/2026): [006_lawyer_card_ocr.sql](006_lawyer_card_ocr.sql) thêm bảng `document_lawyer_card_ocr` (ảnh thẻ luật sư JPEG mã hóa lại trong `safe/<owner>/lawyer-card/`, JSON OCR, RLS/backend-only). Chạy một lần trong SQL Editor của đúng project Document sau 001-005; cuối file có câu kiểm tra chỉ đọc. ERD hiện 7 bảng.

Database riêng của Document Service. Thiết kế tại [erd.dbml](erd.dbml), copy toàn bộ vào dbdiagram.io.

- `user_files`: chủ/file/mục đích upload, MIME/hash, storage private, trạng thái kiểm tra an toàn và xóa mềm.
- `document_processing_jobs`: job kiểm tra + extract/OCR, trạng thái/lỗi/thời gian.
- `document_extraction_results`: chữ, bố cục trang và dữ liệu gợi ý của job completed.
- `user_identity_profiles`: một hồ sơ CCCD current/user, đã user review ở detail; 6 trường, revision và nguồn file/result. Không sao chép sang User DB.

File binary nằm trong object storage private, không nằm trong DB. Contract quản lý nháp và file hợp đồng sinh ra; Document không lưu generated contract metadata trùng.

Task 4 bổ sung [003_identity_ocr_sessions.sql](003_identity_ocr_sessions.sql): bảng phiên hai ảnh owner/idempotency/hash, raw JSONB private và sáu field gợi ý; không sửa profile cũ hoặc FK sang User. Ngày 09/10/2026 chuyển engine local, migration không phụ thuộc VNPT. Dùng scripts/setup_identity_ocr.py guard đúng project để kiểm tra/apply bảng thiếu; không rerun 001/002. Xem DOCUMENT_IDENTITY_OCR.md về nghiệm thu.

Migration 003 đã apply đúng project Document ngày 09/10/2026; kiểm tra chỉ đọc schema/RLS/browser revoke/backend privileges pass. Không sửa dữ liệu cũ hoặc bucket. Bảng này chưa có retention/purge phiên; chốt lifecycle trước production.

Đã có SQL triển khai thiết kế; việc có bảng trên cloud phải xác nhận bằng truy vấn kiểm tra, không suy ra từ file trong repo.

Task5: [004_identity_profile_session.sql](004_identity_profile_session.sql) sau001/003, thêm source_session_id XOR với document/result, giữ profile cũ/RLS/quyền. Script setup_identity_profile.py --project-ref srrxrnohunfyyolbfsew mặc định chỉ đọc; --apply chỉ nâng cấp khi chưa có cột, không rerun nền/cắt dữ liệu. GET/PUT/DELETE profile implemented; DELETE204, GET404 giữ current_revision. Owner advisory → nguồn cũ/mới có thứ tự → profile FOR UPDATE/CAS. Session terminal/provenance bất biến; FK RESTRICT cả khi profile deleted. Unlink không purge; retention chưa có. Các mục thiết lập nền dưới đây là lịch sử001/002.

## Khởi tạo Supabase

T5 ngày09/10/2026: migration004 đã apply đúng srrxrnohunfyyolbfsew, verify chỉ đọc lại constraints/FK/triggers/RLS/browser revoke/backend privileges pass; profile cũ và bucket giữ nguyên. Không rerun004 bằng SQL Editor.

Project được chọn cho lần thiết lập này: [srrxrnohunfyyolbfsew](https://supabase.com/dashboard/project/srrxrnohunfyyolbfsew/editor).

Đã khởi tạo và kiểm tra cloud ngày 08/10/2026: cả hai migration đã chạy, 4 bảng có RLS/trigger, browser không có CRUD trực tiếp, policy Storage restrictive và bucket private `document-files` đúng giới hạn 9 MiB/MIME. Kết nối thiết lập dùng Session pooler `aws-0-ap-northeast-1.pooler.supabase.com:5432`, qua `DOCUMENT_DATABASE_POOLER_HOST`, dùng lại password đã lưu và SSL. Không chạy lại migration bằng SQL Editor; script sẽ kiểm tra và bỏ qua schema đã có. Upload/OCR/API nghiệp vụ vẫn chưa triển khai.

Trong SQL Editor của đúng project, chạy lần lượt:

1. [001_document_schema.sql](001_document_schema.sql): 4 bảng, enum, FK nội bộ, CHECK, index và trigger bảo vệ dữ liệu.
2. [002_document_storage_access.sql](002_document_storage_access.sql): policy restrictive chặn `anon`/`authenticated` truy cập bucket Document kể cả project có policy permissive rộng cho bucket khác.
3. Sau khi tạo bucket, chạy [check_document_db.sql](check_document_db.sql) để kiểm tra bảng/RLS/index/trigger/quyền/bucket.

Hai migration chỉ chạy một lần. Nếu tên schema/bảng/enum/policy đã tồn tại, transaction tương ứng fail và rollback; không xóa hoặc sửa bảng cũ để vượt qua lỗi. Kiểm tra schema hiện hữu trước khi sửa. Nếu bước 1 thành công và bước 2 lỗi, sửa nguyên nhân rồi chỉ chạy lại bước 2.

`owner_user_id` là UUID từ JWT `sub` của user-service, **không FK sang `auth.users` hoặc User DB**. RLS bật cho cả 4 bảng; không cấp browser CRUD. Backend phải xác thực JWT, kiểm tra owner và CSRF trước khi dùng kết nối Postgres/service key vốn có thể vượt RLS. Admin của ứng dụng không được đọc giấy tờ của người khác.

## Private Storage

Tạo qua Storage Dashboard hoặc Storage API, không sửa trực tiếp metadata `storage.buckets`/`storage.objects` bằng SQL:

| Thuộc tính | Giá trị |
|---|---|
| Bucket ID/name | `document-files` |
| Public | Tắt (`false`) |
| File size limit | `9437184` bytes (9 MiB) |
| Allowed MIME types | `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `image/jpeg`, `image/png` |

File mới lưu trong `quarantine/<owner_uuid>/<document_uuid>`; sau kiểm tra, bản an toàn lưu trong `safe/<owner_uuid>/<document_uuid>`. Filename chỉ để hiển thị. Chốt `storage_key`, size và hash an toàn trước khi chuyển `available`; sau đó bản available bất biến. Không cấp signed URL cho quarantine/rejected/deleted. Upload Storage dùng `upsert=false`; không ghi đè bytes của ID đã available.

Bucket limit/MIME không thay thế kiểm tra nội dung, malware, strip EXIF, PDF tối đa 100 trang hoặc giới hạn giải nén ở worker. URL signed chỉ phát sau kiểm tra JWT/owner/file, tối đa 300 giây.

## Cấu hình và script thiết lập

Điền secret vào `backend/.env` (đã gitignore), không gửi secret vào chat hoặc Frontend:

```dotenv
DOCUMENT_DATABASE_URL=<Postgres URI của đúng project từ Connect, có sslmode=require>
DOCUMENT_SUPABASE_URL=https://srrxrnohunfyyolbfsew.supabase.co
DOCUMENT_SUPABASE_SERVICE_ROLE_KEY=<backend service-role key của đúng project>
DOCUMENT_STORAGE_BUCKET=document-files
```

`DOCUMENT_SUPABASE_SERVICE_ROLE_KEY` nhận backend **Secret key** dạng `sb_secret_...` (ưu tiên) hoặc legacy `service_role` key. Copy key tại Project Settings → API Keys; tên biến là cấu hình nội bộ của service, không phải tên bắt buộc trong Dashboard. Script gửi secret mới qua header `apikey`, chỉ dùng Bearer cho legacy JWT. Tham khảo [API keys](https://supabase.com/docs/guides/getting-started/api-keys).

Backend/container chạy lâu: dùng Direct connection nếu máy hỗ trợ IPv6; nếu cần IPv4, copy **Session pooler** URI từ Connect. Password có ký tự đặc biệt cần URL-encode; không dùng API key thay database password. Script không tự suy đoán host/region/password và sẽ từ chối URI không khớp project. Không dùng URL của User Service cho Document.

Script thiết lập tự dùng `sslmode=require` nếu URI chưa có chế độ SSL; URI chủ động cấu hình chế độ SSL yếu hơn sẽ bị từ chối. Giữ `sslmode=require` trong biến môi trường khi nối runtime sau này.

Nếu đang lưu URI Direct nhưng máy chỉ có IPv4, script hỗ trợ `DOCUMENT_DATABASE_POOLER_HOST` (hoặc `--pooler-host`) lấy chính xác từ Connect → Session pooler. Script dùng lại password trong URI đã lưu, chuyển host/username/port sang Session pooler và giữ SSL; không cần đưa password vào câu lệnh. Đây là cấu hình cho script thiết lập; khi nối runtime, dùng URI Session pooler đầy đủ cho `DOCUMENT_DATABASE_URL` hoặc triển khai cùng cách chuyển host.

Từ thư mục `backend`, cài dependency thiết lập rồi kiểm tra trước khi ghi:

```powershell
python -m pip install -r services/document-service/requirements-setup.txt
python services/document-service/scripts/setup_supabase.py --project-ref srrxrnohunfyyolbfsew --create-bucket
python services/document-service/scripts/setup_supabase.py --project-ref srrxrnohunfyyolbfsew --apply --create-bucket
```

Không có `--apply`: chỉ kiểm tra kết nối/project/bucket. Có `--apply`: chạy migration nếu chưa có bảng và tạo bucket nếu thiếu; bảng hiện hữu phải qua kiểm tra RLS/quyền/trigger/policy trước khi bỏ qua migration. Bucket đã có nhưng sai cấu hình sẽ bị từ chối, không tự ghi đè. Các bước SQL và Storage là độc lập: nếu Storage lỗi sau khi SQL thành công, sửa cấu hình rồi chạy lại script. Script chỉ xác nhận một số cấu trúc quan trọng, không phải công cụ so sánh schema đầy đủ.

Runtime đã nối database/storage cho file API, T4 OCR và T5 profile; Compose truyền cấu hình Document riêng. Worker tổng quát chưa có. Không coi `/health` là kiểm tra database.

## Quy tắc transaction khi triển khai API/worker

- Upload: ghi object quarantine, rồi transaction metadata + queued job; nếu transaction lỗi, dọn object orphan qua Storage API.
- Worker: khóa `user_files` trước, rồi job. Ghi bản an toàn và update file `available` trước result. Insert result + job `completed` cùng transaction; deferred trigger chặn commit một nửa. OCR lỗi có thể giữ file `available` và job `failed`.
- Profile: owner advisory → nguồn cũ/mới có thứ tự → profile FOR UPDATE; recheck expected_revision dưới khóa, lệch409. Session completed cùng owner; legacy file latest completed/hash. INSERT chỉ chưa có row/revision0. DELETE lặp unlink cuối trả204 không update nữa; GET404 giữ revision.
- Xóa file: soft delete; trigger chặn job active hoặc nguồn profile active. Không cascade Contract. Không hard-delete profile để reset revision; chưa có quy trình purge/retention.
- Result là bất biến. Schema kiểm tra dạng mảng JSON, giới hạn text/pages/JSON/hash/source; bbox, cấu trúc từng page/field và dữ liệu review vẫn phải validate theo OpenAPI ở worker/API.
- Mọi writer tuân theo thứ tự khóa file → job/profile; retry toàn transaction khi deadlock/serialization failure, có giới hạn. Không lưu/log token, CCCD, nội dung OCR hoặc signed URL.

Nguồn tham khảo: [Postgres connections](https://supabase.com/docs/guides/database/connecting-to-postgres), [Storage access control](https://supabase.com/docs/guides/storage/security/access-control), [Storage schema chỉ đọc](https://supabase.com/docs/guides/storage/schema/design).

Luồng bàn giao: [DOCUMENT_CONTRACT_INTEGRATION.md](../../../docs/api/DOCUMENT_CONTRACT_INTEGRATION.md).
