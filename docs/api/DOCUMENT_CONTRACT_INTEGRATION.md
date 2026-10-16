# Document nối Contract: chốt API và dữ liệu

Cập nhật09/10: Document15 operation runtime, adapter handoff ở contracts/examples/document_handoff.py và outbox005 đã triển khai; Contract import vẫn planned. [DOCUMENT_RELEASE.md](DOCUMENT_RELEASE.md) là trạng thái hiện tại, thiết kế dưới đây là lịch sử. T6 extracted_fields=[]; PDF text+raster cùng trang giữ native text và warning, chưa merge OCR vùng ảnh.

Ngày 08/10/2026 — Hiền phụ trách. **Đây là thiết kế, chưa triển khai upload/OCR runtime.** SQL và script thiết lập Supabase đã có tại [Document database](../../services/document-service/database/README.md); trạng thái bảng/bucket trên cloud cần kiểm tra riêng.

## 1. Ai làm gì?

| Service | Trách nhiệm | Không làm |
|---|---|---|
| Document | Nhận/lưu file riêng tư, kiểm tra an toàn, đọc chữ/OCR và vị trí, trả kết quả | Không ghép dữ liệu vào hợp đồng, không quản lý nháp hay file Contract sinh ra |
| Contract | Nhận document_id, lưu snapshot, xác định ô điền, user review, nháp/preview/xuất | Không đọc DB Document, không quản lý upload mọi giấy tờ |
| Notification | Thông báo trong web theo event final của Contract | Không OCR/điền file; chưa thêm event analysis/preview |

File thiết kế:

- ERD: `services/document-service/database/erd.dbml` — dán toàn bộ vào dbdiagram.io.
- API: `services/document-service/openapi.yaml` — một file, tham chiếu local.
- Contract: `services/contract-service/openapi.yaml` và `database/erd.dbml`.

Chỉ Document `GET /documents/health` là implemented. Các API nghiệp vụ đều planned.

**Luồng CCCD chốt mới:** user upload/review một lần ở trang detail, lưu profile Document rồi tái sử dụng. Contract chỉ gợi ý 6 thông tin CCCD đã confirm; mục khác hướng dẫn nhập tay. Xem [IDENTITY_PROFILE_FILLING.md](IDENTITY_PROFILE_FILLING.md).

## 2. Địa chỉ gọi chính xác

| Mục đích | FE qua Gateway | Contract trong Docker |
|---|---|---|
| Upload | POST `/api/documents/` | Không upload thay FE trong luồng này |
| Danh sách | GET `/api/documents/` | Không cần |
| Metadata | GET `/api/documents/{document_id}` | GET `{DOCUMENT_SERVICE_URL}/api/v1/documents/{document_id}` |
| Trạng thái | GET `/api/documents/{document_id}/processing` | GET `{DOCUMENT_SERVICE_URL}/api/v1/documents/{document_id}/processing` |
| Kết quả đọc/OCR | GET `/api/documents/{document_id}/extraction` | GET `{DOCUMENT_SERVICE_URL}/api/v1/documents/{document_id}/extraction` |
| Link tải cho user | GET `/api/documents/{document_id}/download` | Không dùng để tải URL ngoài |
| Bytes file gốc an toàn | GET `/api/documents/{document_id}/content` | GET `{DOCUMENT_SERVICE_URL}/api/v1/documents/{document_id}/content` |
| Xóa mềm | DELETE `/api/documents/{document_id}` | Không tự xóa sau import |
| CCCD đã xác nhận | GET `/api/documents/identity-profile/me` | GET `{DOCUMENT_SERVICE_URL}/api/v1/documents/identity-profile/me` |
| Lưu/thay CCCD | PUT `/api/documents/identity-profile/me` | Không tự confirm thay user |
| Unlink CCCD | DELETE `/api/documents/identity-profile/me?expected_revision=N` | Không tự xóa |

`DOCUMENT_SERVICE_URL=http://document-service:8000`, **không chứa** `/api/v1`.
FE dùng `http://localhost:8080`. Collection `/documents/` bắt buộc slash cuối vì Gateway hiện có route `/api/documents/`; không trông chờ redirect khi POST multipart.

FE gửi cookie HttpOnly bằng `credentials: "include"` hoặc Bearer JWT. Contract chuyển tiếp Bearer user, hoặc chỉ cookie `access_token` đã nhận. **Không gửi refresh token, toàn bộ Cookie header, X-User-Id tự đặt hoặc INTERNAL_TOKEN vào API user.** Document xác thực JWT và kiểm tra owner lại, admin không được vượt quyền riêng tư.

Write qua cookie phải có kiểm tra Origin/CSRF allowlist FE khi triển khai. Cookie HttpOnly một mình không thay thế chống CSRF. JWT/CSRF Document chưa được gắn vào code trong lần thiết kế này.

## 3. Upload gửi gì?

`multipart/form-data`: `file`, `purpose`, `document_type`; header `Idempotency-Key` UUID và `X-Request-ID`.

| User upload | purpose | document_type |
|---|---|---|
| Mẫu hợp đồng DOCX/PDF | contract_template | contract |
| CCCD ảnh/PDF | supporting_document | identity_card |
| Giấy tờ khác DOCX/PDF/JPEG/PNG | supporting_document | other |

Phân loại là user khai báo, không có nghĩa Document đã xác minh thật/giả. Không tự suy đoán mục đích từ tên file. File không đổi mục đích/bytes sau available; upload mới tạo document_id mới.

Giới hạn: **file <=9 MiB (9437184 bytes), PDF <=100 trang**, tổng multipart <=10 MiB của Gateway. Chừa phần header/form; không tự tăng Gateway. Kiểm tra MIME từ bytes/parser; không tin đuôi hoặc Content-Type. Từ chối DOCM, PDF mã hóa/khóa/ký số, file hỏng/nguy hiểm. Strip EXIF và áp dụng orientation ảnh trước khi chốt bản available.

Chống zip bomb/phân giải quá lớn: đặt giới hạn RAM/thời gian/số entry/dung lượng giải nén/pixel ở worker trước khi triển khai, không chỉ giới hạn 9 MiB file nén. Kết quả text <=1000000 ký tự, JSON <=16 MiB; vượt giới hạn -> failed, không âm thầm cắt.

202 trả `data.document_id`, trạng thái file và job. **Chưa được hiển thị “OCR xong” khi nhận 202.**

Idempotency: server hash bytes upload + purpose/type/filename. Cùng owner/key/hash trả lại ID cũ, khác hash ->409. Retry an toàn bằng đúng key và cùng bytes/form; không tạo key mới mỗi lần mạng timeout. Replay key file đã deleted ->410; upload mới dùng key mới. File an toàn đã strip EXIF có hash riêng tại `sha256`, có thể khác hash upload.

## 4. Hai trạng thái khác nhau

| Trạng thái | Giá trị | Ý nghĩa |
|---|---|---|
| `file_status` | pending_check / available / rejected | File đã qua kiểm tra an toàn và được tải chưa? |
| Job `status` | queued / processing / completed / failed | Đọc chữ/OCR xong chưa? |

Luồng thường:

```text
Upload → pending_check + queued
Worker → kiểm tra file → available + processing
Extract/OCR → available + completed
```

- Malware/không an toàn: rejected + failed, không tải hoặc trả text.
- OCR lỗi sau khi file an toàn: available + failed; user vẫn tải file, nhưng không có kết quả dùng để import luồng mẫu.
- Contract import mẫu yêu cầu **available + completed**. FE poll processing khoảng 2–3 giây; không giữ HTTP request chờ OCR.
- GET processing trả trạng thái kể cả failed. GET extraction chưa xong ->409 `EXTRACTION_NOT_READY`; failed ->409 `EXTRACTION_FAILED`; user xem mã lỗi gốc trong processing.
- Không âm thầm trả extraction của attempt cũ nếu attempt mới chưa completed. Hiện chưa thiết kế API retry thủ công; có thể upload lại bằng key mới, worker retry lỗi tạm có giới hạn trong cùng job.

## 5. Contract lấy dữ liệu theo thứ tự nào?

Khi FE gọi `POST /api/contracts/user-templates` với `document_id` và `name`:

1. Contract dùng JWT người gọi lấy **metadata**; yêu cầu đúng owner, purpose=contract_template, MIME DOCX/PDF, không deleted và file available. UUID của metadata phải đúng ID yêu cầu.
2. Lấy **processing**; yêu cầu completed, document_id/job_id khớp metadata mới nhất.
3. Lấy **extraction**; yêu cầu schema_version=1.0, đúng document_id/job_id và source_sha256 khớp metadata. Không giả định OCR chắc chắn đúng.
4. Lấy **content** tại base URL Document đã cấu hình; stream có giới hạn, không follow redirect ngoài. Kiểm tra số bytes và SHA-256 thực tế khớp metadata/header/extraction. Stream thiếu/sai hash -> bỏ partial, không tạo analysis.
5. Copy file và JSON extraction vào **private Contract snapshot**. Sau đó transaction lưu personal template + version + analysis. 202 chỉ sau snapshot và transaction thành công; transaction fail phải dọn snapshot orphan.
6. Worker Contract đọc snapshot, không cần giữ JWT trong DB/job để gọi Document sau khi token hết hạn.

Nếu file bị xóa giữa các lời gọi hoặc job/hash khác nhau, hủy import và yêu cầu thử lại; không dùng dữ liệu ghép từ hai phiên bản. Bản available bất biến nên không có API thay bytes ở cùng ID.

Metadata API dùng `document_id` tương ứng `user_files.id` trong DB. Không trả storage_key hoặc upload/request hash.

Ví dụ metadata sẵn sàng (dữ liệu giả):

```json
{
  "data": {
    "document_id": "88888888-8888-4888-8888-888888888888",
    "owner_user_id": "11111111-1111-4111-8111-111111111111",
    "purpose": "contract_template",
    "document_type": "contract",
    "original_filename": "mau-hop-dong.pdf",
    "media_type": "application/pdf",
    "uploaded_size_bytes": 204800,
    "size_bytes": 204800,
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "file_status": "available",
    "error_code": null,
    "processing_job_id": "77777777-7777-4777-8777-777777777777",
    "processing_status": "completed",
    "created_at": "2026-10-08T02:00:00Z",
    "updated_at": "2026-10-08T02:00:04Z"
  },
  "error": null,
  "request_id": "req-metadata-demo"
}
```

Thực tế hash phải tính từ file, không dùng chuỗi demo. `/content` trả **binary**, không trả JSON base64. Các API còn lại trả `{data,error,request_id}`; DELETE 204 không có body; health là ngoại lệ đơn giản.

## 6. Chữ và vị trí PDF/DOCX

Extraction trả `schema_version`, `source_sha256`, `source_kind`, `text`, `pages`, `extracted_fields`, `warnings`, `engine_version`.

- PDF/ảnh: mỗi page có `page`, `width_pt`, `height_pt`, `rotation_degrees`, `method`, `blocks`.
- Trang bắt đầu 1, các trang liên tiếp/duy nhất. Với ảnh một trang, engine chuẩn hóa ảnh vào page 72 dpi để có kích thước pt, nhưng bbox vẫn 0..1.
- Bbox `{x,y,width,height}` chuẩn hóa 0..1, gốc trên-trái **sau crop/rotation**. Server kiểm tra x+width<=1 và y+height<=1; FE/Contract không xoay thêm lần nữa.
- `rotation_degrees` là góc nguồn đã được áp dụng vào bố cục. Preview PDF Contract phải dùng cùng canvas crop/rotation; giữ đúng page dimensions, không tự scale canvas rồi dùng box cũ.
- PDF mixed: `method=text/ocr` theo trang. Nếu cùng trang có chữ gốc + vùng scan, engine OCR bổ sung và merge/dedupe; method=ocr cho trang đã dùng OCR, không bỏ phần scan hoặc trả trùng chữ.
- DOCX: text tham khảo, pages=[]; Contract parse file gốc để xử lý paragraph/table/header/footer và locator. Không đổi tọa độ OCR thành XPath.
- Confidence null nếu engine không có score; thấp/không rõ có warning. Nếu parser/OCR không đọc được, failed rõ lỗi; trang trắng hợp lệ vẫn completed với blocks=[].

Ví dụ block (không phải ô điền hợp đồng):

```json
{"text":"Bên A:","bbox":{"x":0.1,"y":0.2,"width":0.12,"height":0.03},"confidence":null}
```

Contract dùng chữ/vị trí để gợi ý vùng trống; user xác nhận. Bbox của chữ OCR **không tự là vùng để ghi đè**.

CCCD có thể trả `extracted_fields` với key full_name/identity_number/date_of_birth/...; value là string (không làm mất số 0 đầu), confidence, source_page/source_bbox và is_review_required=true. Ngày chắc định dạng -> YYYY-MM-DD, không chắc -> giữ text + warning. Không tạo field cho dữ liệu không đọc được.

Tên field này khác tên field Contract: `full_name` không tự thành `party_a_name`. User chọn bên của mình và bindings; Contract lấy profile đã confirm, không raw OCR, rồi gợi ý qua `POST /contracts/drafts/{draft_id}/identity-suggestions`. User chấp nhận mới PATCH field_values. Chỉ 6 key CCCD whitelist; mục khác hướng dẫn nhập tay. Endpoint đã chốt thiết kế nhưng vẫn planned.

## 7. Lỗi, timeout, bảo mật và xóa

| Document / lỗi mạng | Contract import xử lý |
|---|---|
| 401 | Trả 401, yêu cầu đăng nhập lại; không bỏ kiểm tra quyền |
| 404 | Trả 404, không tiết lộ file thuộc ai |
| 409 not-ready/failed/rejected | Trả 409, FE xem processing; không nhận mẫu pending |
| MIME/purpose không phải mẫu DOCX/PDF | Contract trả 422 |
| Document 5xx/sai schema/hash/job/source ID | 502 DOCUMENT_BAD_RESPONSE |
| Không kết nối Document | 503 DOCUMENT_UNAVAILABLE |
| Hết thời gian chờ | 504 DOCUMENT_TIMEOUT |

Metadata/processing/extraction/content timeout mỗi request 5 giây theo quy tắc nhóm; tải 9 MiB chậm có thể timeout, phải báo lỗi thay vì nhận partial. GET được retry tối đa 2 lần (0.5s, 1s); không retry 4xx và không tự retry POST bằng key mới. Không log body, token, nội dung OCR, CCCD hoặc link signed. Truyền X-Request-ID, xác minh response/schema/status mọi lần gọi.

Các response dữ liệu riêng tư cần Cache-Control no-store khi triển khai. Tải attachment + nosniff; không render file lạ như HTML. Link signed tối đa 300 giây là quyền truy cập của người giữ link, không phải mỗi lượt tải đều kiểm tra login; không chia sẻ link. Delete không hứa thu hồi link đã cấp tức thì.

Xóa file: khóa file, queued/processing ->409; nguồn profile active ->409 PROFILE_SOURCE_IN_USE (unlink/thay trước); terminal không bị tham chiếu active -> soft delete. Lặp DELETE đúng chủ ->204, đọc lại ->404. Không cascade Contract; snapshot vẫn dùng được. Xóa mọi bản sao cá nhân cần API Contract riêng và chính sách retention.

## 8. Định nghĩa xong và việc chưa làm

Thiết kế được coi là xong khi:

- ERD 4 bảng parse được trên DBML, local FK đúng, Note ghi CHECK/partial index cần SQL.
- OpenAPI OAS 3.0.3 validate được, refs local hợp lệ, ví dụ đúng schema.
- Contract/Document khớp URL/ID/job/hash/giới hạn/states; import không cần JWT lưu lâu dài.
- Test thiết kế gồm owner, idempotency, nullable pending, bbox, error response và status planned.

Đã khởi tạo và kiểm tra Supabase cloud ngày 08/10/2026 trên project `srrxrnohunfyyolbfsew`: 4 bảng Document có RLS/trigger, policy Storage restrictive và bucket private `document-files` (9 MiB). Có migration, script thiết lập và test schema/policy cục bộ. Chưa làm: JWT/CSRF Document runtime, upload/worker/OCR, quota cụ thể, retention/purge, FE editor và ghép CCCD tự động. Không đánh dấu API implemented chỉ vì OpenAPI, SQL hoặc bucket đã sẵn sàng.
