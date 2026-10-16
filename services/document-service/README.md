# Document Service

Cập nhật User gate09/10: upload/OCR/profile PUT kiểm tra tài khoản qua User Service, khóa403, chưa nộp hồ sơ vẫn cho chuẩn bị CCCD, upstream lỗi503. [Hợp đồng gửi người phụ trách User](README_USER_GATE.md). Endpoint status còn thiếu trong code User đã kiểm tra, nên chưa nghiệm thu live integration.

Dịch vụ tiếp nhận, lưu trữ và xử lý tài liệu người dùng tải lên.

**Bạn nhận bàn giao và muốn test API:** đọc [README_POSTMAN.md](README_POSTMAN.md) để bật service, lấy access token và thử đủ15 API Document theo thứ tự; có payload mẫu, mã lỗi và cách báo kết quả.

T7/T8: outbox005 đã apply/verify; dispatcher mặc định tắt, adapter/test handoff và Docker hardening đã có. [Bàn giao hiện tại](../../docs/api/DOCUMENT_RELEASE.md). Document thật tắt theo user; Contract import/Notification receiver và full cloud e2e chưa triển khai/nghiệm thu. Các mốc dưới đây là lịch sử.

T6 ngày09/10/2026: processing/extraction đã có API; worker opt-in ClamAV → safe → DOCX/PDF text/PaddleOCR scan, dùng schema hiện có, không migration mới. Chưa bật worker trên queue thật hoặc restart nhóm. [Hướng dẫn T6](../../docs/api/DOCUMENT_WORKER.md) là trạng thái hiện tại; các mục Task1–5 dưới đây ghi lịch sử triển khai.

Thiết kế cập nhật 08/10/2026, Hiền phụ trách Document + Contract + Notification.

T5 cập nhật09/10/2026: migration004 đã apply và verify trên đúng Supabase Document (RLS/quyền/FK/triggers), không sửa profile cũ/bucket. Code image mới chưa restart container nhóm.

- [ERD 6 bảng](database/erd.dbml): file, job, result, profile, session hai ảnh và notification outbox. [Supabase](database/README.md): nền001/002 +003/004/005 đã kiểm tra đúng project.
- [OpenAPI](openapi.yaml): cả15 operation Document đã triển khai, gồm processing/extraction T6; cần build API mới và bật worker riêng để file queued được xử lý.
- [CCCD lưu một lần ở detail user](../../docs/api/IDENTITY_PROFILE_FILLING.md): chỉ gợi ý 6 thông tin CCCD cho hợp đồng, không tự điền mục khác.
- [Hướng dẫn nối Contract](../../docs/api/DOCUMENT_CONTRACT_INTEGRATION.md): URL/data/trạng thái, hash/snapshot và mẫu gọi.

Upload mẫu riêng DOCX/PDF hoặc giấy tờ DOCX/PDF/JPEG/PNG, tối đa 9 MiB; PDF tối đa 100 trang. File available và OCR completed là hai trạng thái khác nhau. JWT chung/UUID owner/role/Origin cho cookie write; API file và phiên CCCD đã test owner/admin với SQL tạm, chưa nghiệm thu toàn luồng OCR cloud.

Đã có migration PostgreSQL, RLS, policy bucket riêng tư, script đúng project và test SQL. Task 1 có config DB TLS/pooler/Storage, lỗi không echo PII, Docker env/key public. Task 2 có upload/list/detail/job queued; worker tổng quát chưa có. Model OCR CCCD cài trong image Task 4, không dùng cho job queued của Task 6.

Task 4 chuyển Tesseract 5 pretrained vie+eng CPU local ngày 09/10/2026, không train/không gọi VNPT. POST hai ảnh/GET phiên, ảnh private/JSONB engine, owner-only/idempotency; chưa tạo profile/xác minh User. Docker cài sẵn model; luôn cần user review. Xem [DOCUMENT_IDENTITY_OCR.md](../../docs/api/DOCUMENT_IDENTITY_OCR.md) cho setup/giới hạn.

## Task 5 — profile đã review

T5 có review/sửa/thay nguồn/gỡ profile: source_session_id hoặc legacy source_document_id, sáu field, owner/CAS/CSRF; DELETE204, unlink không purge. Lượt cuối DB rỗng: **343 test liên quan pass, không skip**; Docker OCR thật → JWT/API/SQL tạm → profile pass (Storage stub/ảnh tổng hợp). Không restart nhóm, chưa CCCD thật/full cloud e2e. Xem [luồng FE](../../docs/api/IDENTITY_PROFILE_FILLING.md).

## Task 2 — upload/list/detail

Upload multipart qua `/api/documents/` (internal `/api/v1/documents/`) dùng access token và `Idempotency-Key` UUID. Field: file/purpose/document_type. Lưu private quarantine; trả pending_check/queued, không có link public hoặc dữ liệu OCR giả.

List/filter/page/page_size và detail đều owner-only; admin không tự đọc file người khác. Kiểm tra admission theo nội dung, filename/MIME/scope, PDF page/encryption/signature và DOCX ZIP/XML; worker Task 6 vẫn cần kiểm tra đầy đủ và loại EXIF trước khi available.

40 test Task 2 dùng PostgreSQL thật tạm trên localhost và Storage giả lập. Kiểm tra DB/Storage cloud chỉ đọc đã pass; chưa nghiệm thu toàn luồng upload qua API trên cloud hoặc eKYC thật. Task 3 có probe riêng cho adapter Storage bằng ảnh giả. Xem task log về bù trừ DB/Storage và việc dọn orphan cần maintenance.

## Task 3 — xem/tải/xóa mềm

`GET /api/documents/{document_id}/content` trả bytes chỉ khi available, đủ size/hash, attachment/nosniff/no-store; không đẩy response 200 trước khi kiểm tra xong Storage. Buffer có giới hạn 9 MiB, không stream không giới hạn.

`GET /api/documents/{document_id}/download` trả link signed HTTPS tối đa 300 giây theo schema, filename download encode an toàn. Chỉ owner, admin không bypass. Link đã cấp có thể còn dùng trong thời hạn; không lưu/log link. CORS Gateway expose Content-Disposition/X-Content-SHA256/X-Request-ID cho FE.

`DELETE /api/documents/{document_id}` xóa mềm dưới row lock, queued/processing/profile active trả 409; lặp lại đúng owner trả 204 không body. Không hard-delete Storage/result hay sửa Contract snapshot; chờ nhóm chốt retention.

41 test Task 3 với SQL thật tạm; probe một ảnh giả trên Storage cloud đã kiểm tra authenticated read/hash và signed download/header, rồi dọn ảnh. Worker chưa có nên file upload mới vẫn pending_check/queued, không tự đánh dấu available để demo.

```powershell
# Từ backend; mặc định không ghi cloud.
python services/document-service/scripts/check_storage_download.py
# Chỉ khi muốn tạo một ảnh giả để kiểm tra và tự dọn:
python services/document-service/scripts/check_storage_download.py --run-probe
```

## Chi tiết Task 1

- `GET /api/v1/documents/health`: liveness công khai, không chứng minh DB/Storage hoạt động.
- `GET /api/v1/documents/me`: Bearer hoặc cookie access_token, trả user_id/role từ JWT trong envelope chuẩn, không đọc hồ sơ/DB.
- DB: `DOCUMENT_DATABASE_URL`, optional `DOCUMENT_DATABASE_POOLER_HOST`; TLS mặc định require, DB/Storage cùng project.
- Storage: `DOCUMENT_SUPABASE_URL`, `DOCUMENT_SUPABASE_SERVICE_ROLE_KEY`; bucket private `document-files`, 9 MiB và MIME đúng thiết kế. sb_secret dùng apikey, không dùng như Bearer JWT; key service_role kiểu cũ vẫn hỗ trợ.
- Không fallback sang cấu hình Supabase chung; không log DSN/token/CCCD.
- Cookie write cần exact Origin trong `DOCUMENT_ALLOWED_ORIGINS` (hai origin local cổng 3000 mặc định). Bearer server-to-server không cần Origin. Route write sau phải dùng authenticated router/dependency này.

Từ `backend`, kiểm tra kết nối thật chỉ đọc, không chạy migration hoặc chỉnh bucket:

```powershell
python -m pip install -r services/document-service/requirements.txt
python services/document-service/scripts/check_runtime.py
```

Chạy local từ `services/document-service` sau khi cài `libs/common_auth`:

```powershell
python -m uvicorn app.main:app --env-file ../../.env --host 127.0.0.1 --port 8000
```

JWT_PUBLIC_KEY_PATH local phải trỏ đến key public thật. Docker chỉ mount key public vào Document, không mount private key.

Theo dõi các task và hạn chế ở [DOCUMENT_IMPLEMENTATION_TASKS.md](../../docs/api/DOCUMENT_IMPLEMENTATION_TASKS.md).
