# Cách đọc và dùng API Contract, Notification

Bổ sung 08/10/2026: thiết kế import mẫu riêng DOCX/PDF, kể cả scan, đã có trong `services/contract-service/openapi.yaml`. Luồng: upload ở Document → phân tích chỗ điền → user kiểm tra/xác nhận → nháp → preview → xuất. API mới đều `planned`, chưa triển khai.

Catalog/admin chỉ quản lý mẫu `system`; mẫu `personal` chỉ chủ truy cập. PDF xuất DOCX phải tái dựng nên có thể đổi bố cục. Xem [hướng dẫn mẫu riêng](USER_CONTRACT_TEMPLATES.md), ERD tại `services/contract-service/database/erd.dbml`. Notification không đổi; preview không phát thông báo final.

Đối chiếu code nhánh `develop`, commit `dd79b1a`, ngày 07/10/2026. Bản thiết kế để nhóm review trước khi code.
Người phụ trách bộ tài liệu: Hiền.

## 1. Đọc phần này trước

Hai file OpenAPI là bản thống nhất API giữa Backend và Frontend. Import từng file vào Postman để xem body, response và lỗi:

- `services/contract-service/openapi.yaml`
- `services/notification-service/openapi.yaml`

Trong OpenAPI:

| Từ | Nghĩa đơn giản |
|---|---|
| path | Đường dẫn gọi API |
| get/post/patch/put/delete | Cách gọi, tương ứng xem/tạo/sửa/thay thế/xóa |
| requestBody | Dữ liệu gửi lên |
| parameters | ID trên đường dẫn, bộ lọc hoặc header |
| responses | Kết quả trả về theo HTTP status |
| schema | Trường nào có, kiểu dữ liệu nào, trường nào bắt buộc |
| example | Dữ liệu mẫu để hiểu hoặc thử |
| x-status: implemented | Code đã có; health không xác nhận DB đã hoạt động |
| x-status: partial | Có bản demo nhưng chưa đủ quy ước |
| x-status: planned | Mới thiết kế, chưa gọi được chức năng này |

Hiện hai service có health check và `GET /api/contracts/me`, `GET /api/notifications/me` để kiểm tra JWT. Contract có API demo `GET /api/contracts/templates`, nay cần access token.
Đã gắn JWT RS256 qua `libs/common_auth` vào router API user của Contract/Notification. API health không cần token; API planned vẫn chưa triển khai.
Mọi API nghiệp vụ mới ở hai OpenAPI này đều planned. DB, worker sinh file và nghiệp vụ Notification chưa triển khai.
Không đánh dấu công việc “đã triển khai API” chỉ vì đã có OpenAPI.

## 2. URL, đăng nhập và kết quả

FE dùng `http://localhost:8080`. Các URL trong bảng dưới đã có prefix `/api`.

Service gọi trực tiếp trong Docker:

| Service | URL gốc |
|---|---|
| Contract | `http://contract-service:8000/api/v1` |
| Notification | `http://notification-service:8000/api/v1` |

Ví dụ FE gọi `/api/contracts/drafts/{draft_id}`, service khác gọi `/api/v1/contracts/drafts/{draft_id}`.
Thay `{draft_id}` bằng ID thật, không giữ dấu ngoặc.

API nghiệp vụ dùng JWT:

```http
Authorization: Bearer <access_token>
Content-Type: application/json
X-Request-ID: req-demo-001
```

Token lấy sau đăng nhập Firebase tại `POST /api/users/auth/firebase`, không dùng trực tiếp Firebase ID token để gọi Contract/Notification.
Backend kiểm tra access token bằng `libs/common_auth` và public key; không dùng JWT_SECRET/HS256.
Chủ dữ liệu lấy từ JWT `sub`. FE không gửi `owner_user_id` để nhận quyền.
Admin quản lý mẫu nhưng không được tự động xem giấy tờ/hợp đồng riêng của user.
Các API admin phải kiểm tra role admin tại Backend.

Kết quả thành công:

```json
{
  "data": { "id": "22222222-2222-4222-8222-222222222222" },
  "error": null,
  "request_id": "req-demo-001"
}
```

Kết quả lỗi:

```json
{
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Thiếu tên bên cho thuê.",
    "details": [{ "field": "field_values.landlord_name", "message": "Bắt buộc khi sinh file." }]
  },
  "request_id": "req-demo-001"
}
```

Danh sách: `data.items`, kèm `data.pagination`. `page=1`, `page_size=20`, tối đa 100.
Xóa thành công: 204, không gọi `response.json()` vì không có body.
404 cũng dùng khi dữ liệu không thuộc user, để không tiết lộ dữ liệu cá nhân.
202 nghĩa là đã lưu công việc vào hàng đợi, chưa có kết quả hoàn chỉnh.

## 3. Contract: mẫu, nháp, checklist và PDF/DOCX

Document giữ giấy tờ upload. Contract giữ mẫu, nháp, dữ liệu điền và file hợp đồng sinh ra.
Contract không sao chép bảng giấy tờ của Document. `document_ids` chỉ là ID tham chiếu.
Procedure giữ yêu cầu hồ sơ gốc; Contract lưu checklist theo nháp.

### Mẫu và phiên bản

| API FE | Quyền | Làm gì |
|---|---|---|
| GET /api/contracts/templates | Đăng nhập, demo | API cũ, ID CONTRACT-001, chưa phân trang/DB |
| GET /api/contracts/template-catalog | Đăng nhập | Danh sách mẫu dùng thật; admin có include_hidden |
| POST /api/contracts/template-catalog | Admin | Tạo metadata mẫu |
| GET /api/contracts/template-catalog/{template_id} | Đăng nhập | Chi tiết mẫu |
| PATCH /api/contracts/template-catalog/{template_id} | Admin | Sửa tên/mô tả hoặc ẩn/hiện |
| DELETE /api/contracts/template-catalog/{template_id} | Admin | Xóa mềm mẫu chưa được nháp tham chiếu |
| GET /api/contracts/template-catalog/{template_id}/versions | Đăng nhập | Danh sách phiên bản |
| POST /api/contracts/template-catalog/{template_id}/versions | Admin | Tạo phiên bản draft mới |
| GET /api/contracts/template-versions/{version_id} | Đăng nhập | Nội dung và fields của một phiên bản |
| PATCH /api/contracts/template-versions/{version_id} | Admin | Sửa phiên bản draft |
| POST /api/contracts/template-versions/{version_id}/publish | Admin | Xuất bản |
| DELETE /api/contracts/template-versions/{version_id} | Admin | Xóa phiên bản draft chưa có tham chiếu |

User thường chỉ thấy mẫu hiện và bản published. User vẫn đọc được phiên bản mà nháp của chính họ đã ghim dù mẫu sau này bị ẩn.
Mẫu mới khởi đầu ẩn; tạo phiên bản, publish rồi bật hiện.
Version published không được sửa/xóa; cần thay đổi thì tạo version mới.
Số version do server tự tăng; mỗi nháp giữ đúng template_version_id.

Ví dụ tạo phiên bản:

```json
{
  "content": "Bên cho thuê: {{landlord_name}}",
  "fields": [
    { "name": "landlord_name", "label": "Tên bên cho thuê", "type": "text", "required": true }
  ]
}
```

Content là plain text, placeholder là tên field. Không thực thi Python/JavaScript hoặc câu lệnh từ content.
API danh sách mới dùng template-catalog để giữ API demo hiện tại không bị mô tả sai.
FE chỉ chuyển sang API mới sau khi Backend triển khai và bàn giao.

### Nháp và checklist

| API FE | Làm gì |
|---|---|
| POST /api/contracts/drafts | Tạo nháp, 201 |
| GET /api/contracts/drafts | Danh sách nháp của tôi |
| GET /api/contracts/drafts/{draft_id} | Chi tiết nháp |
| PATCH /api/contracts/drafts/{draft_id} | Sửa dữ liệu điền/title/document_ids |
| DELETE /api/contracts/drafts/{draft_id} | Xóa mềm, 204 |
| GET /api/contracts/drafts/{draft_id}/checklist | Xem checklist đã lưu |
| POST /api/contracts/drafts/{draft_id}/checklist/refresh | Cập nhật từ Procedure và đối chiếu Document |

Ví dụ tạo nháp:

```json
{
  "template_version_id": "44444444-4444-4444-8444-444444444444",
  "title": "Hợp đồng của tôi",
  "field_values": { "landlord_name": "Người mẫu A" },
  "document_ids": []
}
```

Có thể thêm `procedure_id` nếu nháp gắn với một thủ tục; không có thì checklist rỗng.
Nháp cho phép thiếu field required để user lưu dở. Khi sinh file mới bắt buộc đủ field.
PATCH gửi field_values/document_ids là thay toàn bộ đối tượng/mảng đó; các trường không gửi giữ nguyên.
Backend kiểm tra document_ids thuộc user bằng API Document với JWT user.
Checklist attached chỉ nghĩa đã đính kèm; needs_review nghĩa cần xem lại. Không tự khẳng định hồ sơ được cơ quan chấp nhận.
Job đang queued/processing thì không sửa/xóa nháp để tránh kết quả không khớp dữ liệu.

### Sinh file

| API FE | Làm gì |
|---|---|
| POST /api/contracts/drafts/{draft_id}/generations | Tạo job sinh file, 202 |
| GET /api/contracts/drafts/{draft_id}/generations | Danh sách lần sinh |
| GET /api/contracts/generations/{generation_id} | Trạng thái job |
| GET /api/contracts/generations/{generation_id}/download | Link tải có hạn, chỉ completed |

POST gửi `{"format":"pdf"}` hoặc `{"format":"docx"}`.
Thêm header `Idempotency-Key: <UUID>` để một thao tác user không tạo nhiều job khi FE gửi lại.
Cùng key/body/user trả job cũ, cùng key khác body trả 409. Key mới cho lần sinh mới.
Sinh file dùng snapshot nháp/version tại lúc tạo job.
Checklist thiếu giấy tờ vẫn có thể sinh hợp đồng nháp; trường nội dung bắt buộc thiếu thì 422.
File sinh ra lưu ở Contract Storage và do Contract kiểm tra quyền tải.
Thành công gửi event contract.generated; thất bại gửi contract.generation_failed.

## 4. Notification: thông báo trong web cho mọi role

| API FE | Làm gì |
|---|---|
| GET /api/notifications/health | Health hiện có |
| GET /api/notifications?page=1&page_size=20 | Danh sách của người đang đăng nhập |
| GET /api/notifications/unread-count | Số chưa đọc, bỏ suppressed |
| PATCH /api/notifications/{notification_id}/read | Đánh dấu một thông báo đã đọc |
| POST /api/notifications/read-all | Đánh dấu mọi thông báo hiện có đã đọc |
| GET /api/notifications/consultation-preferences/{consultation_id} | Xem mute/suppress phòng |
| PUT /api/notifications/consultation-preferences/{consultation_id} | Gửi {"is_muted":true} hoặc false |

Mute: không popup nhưng vẫn thấy trong danh sách và vẫn tính chưa đọc.
Suppress: cảnh báo phòng do Consultation quyết định; mặc định không hiện và không tính badge.
User chỉ đổi mute của mình, không tự đổi suppress/cảnh báo phòng.
PUT/GET cấu hình phòng phải kiểm tra user là thành viên phòng.
Thông báo là lời nhắc; quyền xem hợp đồng/file/phòng vẫn do service sở hữu kiểm tra khi user bấm vào.

FE lấy thông báo mỗi 10 giây khi tab đang mở, tạm dừng khi tab ẩn.
Đây là polling, không phải push realtime. Không cần device_tokens cho cách này.
Nếu nhóm cần popup ngay tức thì, có thể bổ sung SSE/WebSocket sau; hiện chưa có endpoint đó.
Chi tiết cách tạo thông báo, event mẫu và code gọi: [NOTIFICATION_INTEGRATION.md](NOTIFICATION_INTEGRATION.md).

## 5. Những chỗ cần người phụ trách service khác xác nhận

- Document: bàn giao API xem metadata, chủ file và kết quả OCR để Contract kiểm tra `document_ids`. File OpenAPI trên `develop` hiện mang tên Procedure và chứa đường dẫn `/procedures`, chưa có contract API Document dùng được; không tự đoán endpoint.
- Procedure: xác nhận API lấy yêu cầu giấy tờ theo `procedure_id` trước khi code checklist.
- Consultation: hiện OpenAPI dùng trạng thái `waiting/in_progress/closed/cancelled`, chưa có `is_warning`, `resource_version` và API lấy warning nội bộ. Event `consultation.warning_changed` là đề xuất, chỉ tích hợp khi người phụ trách bổ sung các trường/API này.
- Notification nhận event bằng API nội bộ và FE polling là phương án chờ nhóm duyệt; chưa phải realtime Pub/Sub đã triển khai.
- Gateway đã cập nhật CORS cho `Idempotency-Key` ngày 08/10/2026; API sinh file vẫn cần triển khai trước khi FE gọi nghiệp vụ này.

Hiền chỉ phụ trách Contract và Notification. Các service khác giữ nguyên code/tài liệu của người phụ trách.

## 6. Khi nào gửi được cho nhóm?

Hai OpenAPI được viết để nhóm review và FE làm mock theo example.
Trước khi code, nhóm cần duyệt tên đường dẫn, các trường, quyền admin/chủ dữ liệu và cách nhận event.
Sau khi code xong mới đổi x-status và test endpoint thật.
Các ID mẫu là UUID giả; phải tạo bản ghi thật rồi dùng ID trả về khi test tích hợp.

## 7. Kết quả kiểm tra OpenAPI trước khi gắn JWT ngày 07/10/2026

- Hai OpenAPI Contract/Notification hợp lệ theo bộ kiểm tra OpenAPI 3.0.3: 32 operation và 174 example request/response đều qua kiểm tra schema. JSON event mẫu cũng hợp lệ.
- 20 test riêng cho Contract/Notification qua: cấu trúc spec, tham chiếu schema, health, demo templates, adapter gửi event (mock success/duplicate/retry/timeout/401/409/response sai).
- 36 test cấu hình và thư viện JWT dùng chung qua.
- Chạy bộ test OpenAPI toàn repo cùng các test trên: 72 qua, 3 lỗi đều ở `services/location-service/openapi.yaml`, dòng 34. Chuỗi description chứa dấu `: ` nhưng chưa được đặt trong dấu nháy, khiến YAML không đọc được. Người phụ trách Location cần sửa; Hiền không đổi file service đó.
- File Document của nhóm được giữ nguyên, nhưng đang mô tả Procedure nên chưa dùng làm giao diện tích hợp Document được.

Các test này chạy offline/in-process và mock HTTP, không phải test deploy, DB, Firebase thật hoặc toàn bộ Docker. API có `x-status: planned` vẫn chưa chạy được.

Sau đó đã gắn JWT vào hai service và cập nhật spec (thêm `/me`, templates cần token). Hướng dẫn chạy/test phần JWT mới: [JWT_DOCKER_CONTRACT_NOTIFICATION.md](JWT_DOCKER_CONTRACT_NOTIFICATION.md).

Sau khi cài `requirements-test.txt`, chạy lại test của hai service:

```powershell
python -m pytest tests/test_contract_notification.py tests/test_openapi.py -k "contract-service or notification-service or test_contract_notification" -q
```
