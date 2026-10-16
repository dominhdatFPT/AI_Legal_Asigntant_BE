# C8 — mẫu hợp đồng riêng qua Document

C8 đã có 8 API import/list/detail/delete/analysis/mapping/confirm/source-preview. Chủ luôn lấy từ JWT; admin không xem mẫu của người khác. Mẫu riêng không xuất hiện trong catalog quản trị. Không dùng AI để xác nhận vị trí hoặc tự thêm điều khoản.

## Phạm vi hiện tại

- DOCX: cần marker `{{field_name}}` trong paragraph/table/header/footer. Marker có thể bị chia thành nhiều run; renderer thay đúng span và giữ cấu trúc/font/bảng/source parts còn lại. User sửa tên/label/type/required/identity_key rồi xác nhận. Không nhận XPath tự do. Mẫu dòng chấm/ô trống chưa gắn marker, content control C2 không có marker, vị trí preview mơ hồ hoặc field động chưa được hỗ trợ; cần chuẩn bị lại nguồn. Không tự convert text OCR thành DOCX khác bố cục.
- PDF: user chọn vùng trắng theo PDF source-preview. Tọa độ chuẩn hóa0..1, gốc trên-trái của trang đã crop/rotation. Server kiểm tra trang/box/overlap và pixel; vùng có chữ, dấu, nền scan tối hoặc đường kẻ bị từ chối, không ghi đè nguồn. Nền được rasterize150dpi và bỏ actions/attachments/links; chữ nền không còn searchable và AcroForm không còn tương tác. Chữ điền mới có font tiếng Việt. **PDF chỉ xuất PDF**, không PDF-to-DOCX.
- DOCX xuất DOCX hoặc PDF; font Word và LibreOffice có thể khác. Kiểm tra chiều rộng theo vùng marker và số trang, không cam kết mọi bố cục Word giống tuyệt đối. Dữ liệu dài không vừa vùng trả failed/TEXT_OVERFLOW; không tự thu nhỏ hoặc cắt chữ. PDF preview có dải24pt riêng phía trên, không đè nhãn lên nội dung gốc; bản cuối không có dải này.

## Cấu hình và chạy

Dùng cấu hình C7: CONTRACT_DATABASE_URL, CONTRACT_SUPABASE_URL, CONTRACT_SUPABASE_SERVICE_ROLE_KEY cùng project Contract; CONTRACT_STORAGE_BUCKET mặc định contract-files. DOCUMENT_SERVICE_URL chỉ URL gốc, ví dụ http://document-service:8000. Document và Contract dùng chung cấu hình xác minh access JWT của nhóm. Không dùng INTERNAL_TOKEN/Supabase key để giả JWT user.

C8 dùng schema001+002 hiện có, **không migration mới**, không sửa DB/schema Document/User. Snapshot PDF/DOCX nằm trong bucket Contract private, MIME và giới hạn50MiB giữ nguyên; file nhập tối đa9MiB, tối đa100 trang/100 triệu pixel render. Extraction JSON giữ nguyên trong `extraction_snapshot.document_extraction`; `contract_import` chứa manifest/preview do Contract tạo. Cột lịch sử source_extraction_id lưu public job_id Document vì API không công bố ID nội bộ của bảng extraction.

Lệnh từ backend khi muốn bật thật:

```powershell
docker compose --profile contract-worker up -d --build contract-service contract-worker
```

**Import chuẩn bị source-preview đồng bộ trước202**, không phải background analysis hay OCR lại. LibreOffice mỗi lần tối đa60s; mạng Document mỗi GET5s, không tự retry. 202 có analysis needs_review sau khi private snapshots và transaction đã commit; 202 không nghĩa user đã xác nhận hoặc hợp đồng đã xuất. FE không gọi import lặp bằng key mới khi timeout; retry đúng key/body.

Generation vẫn chạy worker C7 opt-in. Không bật worker/service nhóm production trong lượt triển khai C8. Health là liveness, /ready chỉ DB; chưa readiness toàn bộ upstream/renderer/Storage.

## Test Postman

`base_url=http://localhost:8080/api/contracts` qua Gateway hoặc `http://localhost:8000/api/v1/contracts` khi gọi uvicorn trực tiếp. Header `Authorization: Bearer {{user_access_token}}`; token lấy đăng nhập User, không phải key Supabase. Cookie mutation cần Origin đúng CONTRACT_ALLOWED_ORIGINS.

1. Upload file qua Document `POST http://localhost:8080/api/documents/` multipart: file, purpose=contract_template, document_type=contract, Idempotency-Key UUID. Poll processing đến available+completed. Không gửi file trực tiếp cho Contract.
2. `POST {{base_url}}/user-templates`, Idempotency-Key UUID mới, JSON:

```json
{"document_id":"DOCUMENT-UUID","name":"Mẫu riêng của tôi"}
```

Lưu data.id làm analysis_id, data.template_id và data.template_version_id. `GET /template-analyses/{analysis_id}` hoặc `GET /user-templates/{template_id}` lấy mapping_revision/fields/warnings. DOCX có marker rõ ràng được gợi ý field; PDF fields=[] và cảnh báo chọn vùng trắng thủ công. Không tự gắn CCCD/bên A/bên B.

3. `GET /user-templates/{template_id}/source-preview` lấy link PDF300s. FE hiển thị PDF và vẽ highlight từ placements. DOCX **giữ nguyên locator/preview_boxes server**, không tự đặt tọa độ/XPath. PDF cho user chọn vùng thực sự trắng.
4. `PUT /user-templates/{template_id}/mapping`, thay toàn bộ fields với expected_revision hiện tại. Ví dụ PDF (tọa độ phải khớp file thật):

```json
{
  "expected_revision":0,
  "fields":[{
    "name":"party_a_name","label":"Họ tên bên A","type":"text",
    "required":true,"identity_key":"full_name","confidence":null,
    "placements":[{"kind":"pdf_box","page":1,"x":0.1,"y":0.3,
      "width":0.7,"height":0.1,"font_size":12,"multiline":false}]
  }]
}
```

Thành công tăng mapping_revision; stale409 REVISION_CONFLICT, box sai/có mực/chồng422 INVALID_MAPPING. Type gồm text/date/number/boolean; identity_key chỉ sáu key C6, ngày map date, key còn lại text. Không dùng profile user để tự điền bên còn lại.

5. User review rồi `POST /user-templates/{template_id}/confirm` body `{"expected_revision":1}` với revision mới nhất. Server recheck private preview/hash/placements, CAS dưới lock, atomic version published và analysis confirmed. Backend không biết user có thực sự mở PDF; FE phải có màn hình review/xác nhận.
6. `POST /drafts` với template_version_id đã confirm, title và field_values theo [C5](README_DRAFTS.md). User/status vẫn phải hoạt động để tạo nháp. Gợi ý CCCD qua [C6](README_IDENTITY_SUGGESTIONS.md), user chấp nhận rồi PATCH; không auto-save.
7. Preview/final/poll/download theo [C7](README_GENERATIONS.md). PDF source + format=docx bị chặn422 SOURCE_FORMAT_NOT_SUPPORTED ngay admission. Thiếu required final422; procedure chưa có checklist nhóm vẫn chặn422, không tạo checklist giả.

List `GET /user-templates?page=1&page_size=20`; delete `DELETE /user-templates/{template_id}`204. Xóa mềm mẫu không xóa Document, snapshot hay nháp cũ. Analysis queued/processing hoặc generation active thì409; needs_review có thể xóa. Sau xóa không tạo nháp mới, replay import key cũ410, muốn nhập mới dùng key mới. Nháp đã ghim published version vẫn thuộc chủ và có thể xuất từ snapshot cũ.

## Tính nhất quán và bảo mật

Document metadata/processing/extraction/content phải khớp owner/document/latest job/source hash/size/header và bytes; metadata được kiểm tra lại cuối. Hash/job lệch502 DOCUMENT_BAD_RESPONSE, not-ready409, purpose/MIME sai422, owner/deleted404, timeout504 và mạng503. Không follow redirect, không truy cập DB Document, không lưu/log JWT hoặc signed URL. Delete giữa hai lời gọi bị hủy nếu quan sát được; không có distributed transaction khóa Document sau recheck.

Copy source + preview private bằng key ngẫu nhiên/no-overwrite, đọc lại hash trước DB commit. Request hash document_id+name; advisory lock theo owner/key xử lý import đồng thời. Replay không gọi Document/renderer/Storage. CAS mapping/confirm tránh ghi đè đồng thời; version published/snapshot bất biến. Worker không cần token để đọc Document.

Nếu DB/upload lỗi, chỉ dọn object attempt không được version/preview tham chiếu, có lock cùng import để tránh xóa object sau commit mơ hồ. Khi DB không kiểm tra được, giữ object an toàn; orphan sweep/retention/cancel chưa tự động, cần C9 vận hành. Signed URL là capability300s, người giữ link có thể tải; không hứa thu hồi tức thì khi JWT hết hạn hoặc xóa mềm.

## Kiểm tra

Tests dùng JWT/key/nguồn giả và PostgreSQL Docker có nhãn disposable, không cloud business rows. Kiểm tra handoff hash/header/latest-job/owner/timeout/redirect; DOCX macro/external-link/field/ZIP traversal/duplicate; PDF encrypted, mapping overlap/ink/overflow; request replay, snapshot cleanup/ambiguous commit; SQL owner/CAS/race/publish/draft/job/soft delete.

QA server image rootless/read-only/network-none kiểm tra DOCX split run/table/footer/tiếng Việt/0/false và PDF hai trang/crop/rotation. Ảnh từng trang phải được kiểm tra sau lần render cuối. Chưa e2e đăng nhập/Document/User thật trên máy nhóm, chưa duyệt/import30 mẫu Drive; không công bố khả năng mọi mẫu Word/PDF được hỗ trợ.

Kết quả11/10/2026:52 test C8, hồi quy532 pass không skip (59 SQL tạm C3–C8); test lại sửa cuối64 pass/5 SQL skip, các5 SQL đã pass trong lần đầy đủ. OpenAPI0.8.0/0.4.0 và Compose validate pass; image build/smoke auth/health/no test routes pass. Snapshot Storage thật dvpqrwvfuiujvvnccpox: DOCX/PDF upload/hash/read/signed download/public deny pass, hai file giả đã xóa; không tạo business DB rows. 6 container PostgreSQL disposable đã dọn, không restart service nhóm/commit/push.
