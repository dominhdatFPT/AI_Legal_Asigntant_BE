# C7 — preview và xuất DOCX PDF

Đã triển khai 5 API: tạo final, tạo preview, list job, xem job và xin link tải. Mẫu system văn bản C4 dùng `{{field_name}}`: tạo native DOCX, chuyển PDF bằng LibreOffice trong Docker. Bộ mẫu Drive đã review dùng native DOCX snapshot/locator qua [luồng kho mẫu Drive](README_DRIVE_CATALOG.md), không dựng lại từ text phẳng. Không chạy Word desktop, không OCR hay tự thêm điều khoản.

**C8 đã bổ sung mẫu riêng từ snapshot:** DOCX marker rõ ràng và PDF vùng trắng đã user-confirm; xem [hướng dẫn C8](README_PERSONAL_TEMPLATES.md). API admin C4 vẫn nhận nội dung văn bản; import native system snapshot là thao tác offline được review/hash và project-pin, không upload tùy ý bằng API C4. PDF nguồn chỉ xuất PDF, không tự PDF-to-DOCX. Mẫu không thuộc luồng supported vẫn chặn422, không âm thầm dựng file từ text OCR. Mẫu xuất vẫn cần người dùng/nhóm kiểm tra nội dung pháp lý.

## Cấu hình và chạy

Backend .env máy chạy cần CONTRACT_DATABASE_URL, CONTRACT_SUPABASE_URL và CONTRACT_SUPABASE_SERVICE_ROLE_KEY **cùng project Contract**; CONTRACT_STORAGE_BUCKET mặc định contract-files. Không dùng key Document/User, không gửi secret vào chat/Git/Postman export. URI PostgreSQL không thay thế API key Storage.

Bucket phải private, giới hạn50MiB, MIME chỉ PDF/DOCX. Không cấp browser anon/authenticated quyền DML; upload/sign chỉ backend. Không tạo file trùng trên Document.

Lượt C7 đã kiểm tra bucket thật project dvpqrwvfuiujvvnccpox và smoke upload/hash/sign/download bằng PDF giả; public read bị chặn, file test đã xóa. Cập nhật 11/10: ba mẫu Drive đã đăng ký vào cloud (một published, hai draft/ẩn); không tạo nháp user/job giả. C7 dùng schema001+002, không migration mới. `/ready` hiện chỉ kiểm tra DB, chưa phải readiness renderer/Storage; API tạo job kiểm tra thêm Storage/LibreOffice trước khi lưu.

Lệnh từ backend, chỉ khi muốn bật thật:

```powershell
docker compose --profile contract-worker up -d --build contract-service contract-worker
```

Gateway/User cần chạy theo setup nhóm; User `/status` vẫn là phụ thuộc khi tạo nháp C5. Worker opt-in, không có port/Gateway/JWT/INTERNAL_TOKEN. Chạy non-root/read-only, cap-drop, giới hạn memory/pids, temp riêng. Không bật worker production trong lượt test C7 này.

Tắt worker khi không dùng:

```powershell
docker compose --profile contract-worker stop contract-worker
```

API trả202 mà worker chưa bật thì job còn queued, không có file ngay. Không xóa/sửa nháp có job active. Bật lại worker để xử lý tiếp; không sửa tay trạng thái DB. Worker crash có thể khởi động lại bằng lệnh up; chưa có API cancel/retry job.

## Postman

`base_url=http://localhost:8080/api/contracts` qua Gateway; uvicorn Contract cổng8000: `http://localhost:8000/api/v1/contracts`. Header `Authorization: Bearer {{user_access_token}}`. Access token lấy đăng nhập User, không phải key Supabase.

1. Admin chuẩn bị mẫu/version/publish theo [C4](README_ADMIN.md); user tạo nháp theo [C5](README_DRAFTS.md). Gợi ý CCCD cần review/lưu qua [C6](README_IDENTITY_SUGGESTIONS.md) trước khi xuất. Renderer chỉ dùng field_values đã lưu, không tự gọi profile/OCR.
2. `POST {{base_url}}/drafts/{{draft_id}}/preview`, header `Idempotency-Key: UUID-MOI`, **không body**. Format cố định PDF. Thiếu required vẫn được xem, warnings MISSING_REQUIRED:field; ô thiếu là dấu gạch trống. PDF có nhãn BẢN XEM TRƯỚC trên mọi trang. Nhận202, lưu data.id làm generation_id.
3. `GET {{base_url}}/generations/{{generation_id}}` mỗi2–5s. Dừng khi completed/failed; frontend nên giới hạn thời gian chờ, không poll vô hạn. failed vẫn HTTP200 và data.error_code; lỗi không chứa giá trị hợp đồng/secret.
4. completed mới `GET {{base_url}}/generations/{{generation_id}}/download`: nhận data.url và expires_at. Link dùng trong300s; hết hạn gọi lại endpoint. Link là capability tạm: ai có link cũng có thể tải trong thời hạn đó, không chia sẻ/log token query. API xin link vẫn owner-only, admin không bypass.
5. Xem/sửa/lưu nháp, rồi `POST {{base_url}}/drafts/{{draft_id}}/generations`, header Idempotency-Key UUID mới, Content-Type application/json:

```json
{"format":"pdf"}
```

Đổi thành docx nếu muốn Word. Final thiếu required/null/chuỗi trắng trả422 details.field; 0/false hợp lệ. Final nháp gắn procedure_id trả422 PROCEDURE_CHECKLIST_NOT_READY vì checklist nhóm chưa chốt, không bỏ qua kiểm tra pháp lý. Chưa hỗ trợ ghép tài liệu Document đính kèm vào output.

6. `GET {{base_url}}/drafts/{{draft_id}}/generations?page=1&page_size=20` xem lịch sử, created_at giảm dần. Job kết quả giữ version/snapshot cũ khi nháp hoặc mẫu có phiên bản mới. Muốn xuất dữ liệu đã sửa phải tạo job bằng key mới.

## Idempotency, snapshot và lỗi

- Final: cùng user/key/draft/format trả job cũ, kể cả nháp đã sửa; đổi draft/format cùng key409. Preview hash thêm values/version, thay dữ liệu rồi dùng lại key409. Key preview và final tách theo kind. Failed không chạy lại bằng key cũ: sửa nguyên nhân rồi dùng key mới.
- Owner/nháp kiểm tra trước gọi Storage/Document. JWT không vào job/worker. Khi tạo job mới, kiểm tra lại Document links bằng Bearer user, chỉ kiểm tra availability/owner; không đọc/nhúng bytes nên không hứa file đó còn tồn tại mãi sau admission.
- Enqueue khóa nháp, recheck revision sau HTTP; đổi409 DRAFT_CHANGED, một job active/nháp. Snapshot values/document_ids/revision bất biến, version published bất biến. Không giữ transaction qua HTTP/render.
- Worker claim SKIP LOCKED, dùng started_at làm lease180s và retry_count làm fencing/attempt. Crash/mạng/Storage/DB tạm lỗi được nhận lại sau lease; tối đa3 attempt, sau đó WORKER_RETRY_EXHAUSTED. Worker cũ/lease hết hạn không được commit kết quả mới. Render timeout60s; PDF tối đa100 trang, output tối đa50MiB. Required được kiểm tra trước job; dữ liệu điều khiển/word dài không thể bọc dòng có thể failed INVALID_INPUT/TEXT_OVERFLOW.
- Upload key ngẫu nhiên cho từng attempt, không overwrite; đọc lại/hash bytes trước completed. Result + completed + outbox final thành công commit cùng transaction. Preview không tạo outbox. C9 chưa dispatch Notification; outbox pending không có nghĩa đã có thông báo FE.
- Cleanup chỉ key attempt không được result tham chiếu, khóa job để tránh xóa file đã commit nhưng receipt DB bị mất. Crash đúng lúc upload/commit vẫn có thể để orphan; chưa có sweep/retention job tự động. Không xóa bucket/prefix rộng hoặc file mẫu gốc để dọn orphan.
- Chưa completed/failed thì /download409; nháp/job người khác404 kể cả admin. Bucket public/malformed/signed URL sai/khác host bị chặn, không theo redirect.

## Kết quả kiểm tra

Hồi quy C1–C7: **480 pass không skip**, gồm50 test C7 (43 offline/API/Storage mock +7 PostgreSQL tạm thật). Kiểm tra race claim, fencing, retry hết hạn, idempotency, rollback completion/outbox, owner và không xóa result hợp lệ. Một warning TestClient dependency, không test fail.

Renderer Docker đã xuất preview/final tiếng Việt mỗi bản2 trang; kiểm tra nội dung, 0/false, nhãn từng trang và đủ Điều14. Render lại DOCX bằng helper render_docx.py, xem toàn bộ ảnh trang. Sửa border xanh style Title và orphan heading bằng keepNext, render/test lại pass. OpenAPI/Compose và image build pass. Storage thật smoke bằng PDF giả đã xóa sau test; không dùng CCCD thật.

Chưa e2e với đăng nhập/User/status/Procedure thật của nhóm, chưa deploy API/worker, chưa seed30 mẫu Drive. Không tuyên bố renderer giữ bố cục file gốc; phần đó còn cần C8. Chưa commit/push.
