# Tests

T7/T8: test_document_outbox.py (atomic/immutable/owner/retry/concurrency/crash/response), test_document_handoff.py (HTTP/JWT/SQL/hash/state/owner), test_document_release.py (rootless/read-only/key mount/child allowlist/Session5432). DB chỉ localhost document_schema_test; Storage/Notification mock. Native Docker rootless/offline và full ClamAV/local SQL smoke pass, không cloud e2e. Số nghiệm thu cuối: DOCUMENT_IMPLEMENTATION_TASKS.md; bàn giao: DOCUMENT_RELEASE.md.

## Task6 worker

`../../../tests/test_document_worker.py`: SQL tạm, scanner fail-closed, source hash/safe publication, atomic completed/result, recovery/fencing/race, retry budget, latest attempt, owner/HTTP, crop/rotation, EXIF/trailing bytes/active content, output/input limits, child timeout/no secrets và retry safe object. Không DSN Supabase/model download trong unit tests; test PDF trực tiếp skip nếu chưa có pypdfium2.

`Dockerfile.worker` riêng với pinned CPU PaddleOCR/model cache; `check_document_worker.py` kiểm tra DOCX/PDF text/scan/mixed/ảnh tổng hợp không mạng. --run-local-test dùng model + ClamAV thật + signed JWT/HTTP/SQL disposable, Storage vẫn stub; dữ liệu tổng hợp dọn cùng DB test. Xem DOCUMENT_WORKER.md.

Nghiệm thu cuối: **379 test liên quan pass, không skip**, gồm36 T6; image/API/model/ClamAV native smoke/hash/owner/EICAR rejection pass, read-only/tmpfs/cap-drop pass. Không phải kiểm thử cloud hoặc tài liệu thật.

## Task5 profile

`../../../tests/test_document_identity_profile.py`: review sáu field, source_session + legacy migration, owner/CAS/CSRF, concurrent PUT/DELETE/source-delete, JSON/date/ID/error privacy, immutable session, unlink/recreate, Swagger/setup verifier. Lượt cuối DB rỗng: **343 test liên quan pass, không skip**.

Image check_local_ocr_api.py --run-local-test --include-profile: actual OCR → user sửa → JWT/API/SQL tạm/profile/CAS/unlink/recreate, Storage stub/ảnh tổng hợp. Không .env/cloud/CCCD thật. Profile/session giả giữ ở DB disposable vì cấm hard-delete/reset profile; dọn bằng xóa container test, không dùng DB nhóm.

## Task 3 download/delete runtime

`../../../tests/test_document_download_delete.py`: 41 test SQL thật tạm/mocked Storage cho owner/admin, available/OCR failed/pending/rejected, active job/profile, soft-delete/repeat/concurrency, byte size/hash/MIME, signed URL host/path/query, CSRF và binary headers. Thiếu DOCUMENT_TEST_DATABASE_URL thì test SQL skip, không coi là kiểm tra DB thật.

```powershell
python -m pytest tests/test_document_download_delete.py -q
python services/document-service/scripts/check_storage_download.py
```

`check_storage_download.py --run-probe` là opt-in ghi đúng một PNG giả private, đọc/hash/ký link rồi xóa object trong finally; không ghi DB/ảnh CCCD, không log key/link. Không có flag thì không ghi cloud. Không chạy probe mỗi CI.

## Task 2 upload runtime

`../../../tests/test_document_upload.py`: 40 test admission/HTTP/Storage và SQL owner/pagination/idempotency/concurrency/rollback/commit không rõ kết quả. SQL chỉ nhận localhost với DB document_schema_test; thiếu DOCUMENT_TEST_DATABASE_URL thì các test SQL skip, không được tính là nghiệm thu SQL thật.

```powershell
$env:DOCUMENT_TEST_DATABASE_URL='postgresql://postgres:<local-test-password>@127.0.0.1:<local-port>/document_schema_test'
python -m pytest tests/test_document_upload.py -q
```

Đây phải là database tạm riêng; fixture chỉ tạo schema nếu chưa có, dữ liệu giả lưu trong DB tạm đến khi container bị xóa. Không đặt URL Supabase vào biến test này.

## Task 1 runtime foundation

`../../../tests/test_document_foundation.py`: JWT chung, Bearer/cookie, role/UUID, Origin cho cookie mutation, no-store/request_id, lỗi không echo input, config TLS/project/pooler và adapter DB/Storage. Test offline dùng key sinh trong RAM và mock transport/connection; không gọi VNPT hay ghi cloud.

Từ `backend`, sau khi cài requirements-test.txt:

```powershell
python -m pytest tests/test_document_foundation.py tests/test_document_contract_design.py -q
python services/document-service/scripts/check_runtime.py
```

Script thứ hai kiểm tra DB/Storage thật chỉ đọc, cần cấu hình Document trong `.env`. Test offline pass không đồng nghĩa cloud đã kiểm tra thành công.

Test thiết kế ở `../../../tests/test_document_contract_design.py`: URL Gateway, trạng thái planned, local refs, owner/idempotency/limits, binary content, ERD và ví dụ JSON. Đây không phải test upload/OCR thực tế.

Chạy từ backend: `python -m pytest tests/test_document_contract_design.py tests/test_openapi.py -q`.
Các test schema examples dùng `openapi-schema-validator` (skip khi chưa cài); kiểm tra chuẩn OAS đầy đủ dùng `openapi-spec-validator`. Bộ kiểm tra được dùng từ môi trường tạm, chưa thêm vào dependencies runtime của Document.

## Kiểm tra trạng thái tài khoản User

`../../../tests/test_document_user_status.py` kiểm tra ba API ghi qua User status: JWT subject, internal header, ACCOUNT_LOCKED, PROFILE_NOT_SUBMITTED, timeout/lỗi cấu hình/JSON, chặn trước side effect và replay sau khi khóa. HTTP User là mock; SQL upload dùng PostgreSQL tạm. Không xác nhận endpoint User thật đã deploy. Xem [README_USER_GATE.md](../README_USER_GATE.md) để bàn giao và cấu hình.

```powershell
python -m pytest tests/test_document_user_status.py -q
```

## Test PostgreSQL schema và OCR

Task 4 hiện dùng Tesseract 5 pretrained vie+eng CPU local. `../../../tests/test_document_local_ocr.py`: parser Việt/Anh, model/config guards, không đoán số/ngày, timeout kill/reap child thật, busy/error, API/SQL/replay và schema lỗi nhóm. identity_images kiểm tra ảnh; identity_ocr giữ test session/ownership/RLS/concurrency và adapter VNPT legacy (không còn runtime). Chạy: `python -m pytest tests/test_document_local_ocr.py tests/test_document_identity_ocr.py tests/test_document_identity_images.py -q`.

`scripts/check_local_ocr.py` trong image dùng model thật/ảnh tổng hợp, chạy Docker --network none; không DB/Storage. `scripts/check_local_ocr_api.py --run-local-test` dùng model thật + signed JWT trong RAM + HTTP ASGI + PostgreSQL tạm đã khởi tạo; Storage stub, không phải kiểm thử cloud end-to-end. Chỉ nhận DOCUMENT_TEST_DATABASE_URL localhost/host.docker.internal, DB document_schema_test, không query override; dọn đúng session giả của nó. Không dùng CCCD thật.

`test_database_schema.py` chạy migration thật và kiểm tra CHECK/unique, atomic result+completed, hash, bất biến, CCCD owner/revision/xóa mềm và browser privileges. Chỉ chấp nhận host localhost và database tên `document_schema_test`; không dùng Supabase thật.

Cần PostgreSQL 15+ tạm, database `document_schema_test` **mới/rỗng** và role `anon`, `authenticated`, `service_role` (`BYPASSRLS`) để mô phỏng Supabase. Mỗi lần chạy dùng database rỗng mới vì migration chỉ chạy một lần; dữ liệu từng test rollback.

```powershell
python -m pip install -r services/document-service/requirements-setup.txt
$env:DOCUMENT_TEST_DATABASE_URL='postgresql://postgres:<local-test-password>@127.0.0.1:<local-port>/document_schema_test'
python -m pytest services/document-service/tests/test_database_schema.py -q
```

Không có biến môi trường thì test database skip. Đây là test database, không phải test upload/OCR/API hoặc Storage cloud.
