# Document gọi User Service trước thao tác ghi

Ngày 09/10/2026. Hợp đồng tối thiểu theo tin nhắn của người phụ trách User; đã implement phía Document, không tự sửa User/Contract. **Endpoint status chưa được tìm thấy trong code User local hoặc origin/develop eefe634 lúc kiểm tra**; citizen-profile đã có trên develop. Bạn phụ trách User cần triển khai/push/deploy status đúng hợp đồng này để test thật. Khi endpoint thiếu404, Document trả503, không bỏ kiểm tra.

## Quyết định nghiệp vụ

| Response status của User | Document upload file / OCR CCCD / PUT profile |
|---|---|
| can_create_dossier=true, block_reason=null hoặc không có |Cho tiếp tục|
| can_create_dossier=false, block_reason=PROFILE_NOT_SUBMITTED |Cho tiếp tục chuẩn bị giấy tờ, OCR và review profile; tránh vòng lặp chưa có hồ sơ thì không OCR được|
| block_reason=ACCOUNT_LOCKED |403 ACCOUNT_LOCKED, message lấy từ block_message; khóa được ưu tiên kể cả can_create_dossier=true bất nhất|
| User timeout/mạng/lỗi HTTP/response sai/thiếu token/config |503 USER_SERVICE_UNAVAILABLE, không ghi file/chạy OCR/lưu profile|
| false nhưng reason thiếu/không hỗ trợ hoặc trạng thái mâu thuẫn khác |503, không tự suy đoán được phép|

can_create_dossier=false vì chưa nộp hồ sơ vẫn phải bị chặn ở **service tạo hồ sơ nghiệp vụ/hợp đồng**. Upload Document chỉ lưu file, không tạo hồ sơ/hợp đồng. Rule đã chốt ở Document không thay guard của Contract hoặc service khác.

Các API được gắn guard:

- POST /api/documents/ (internal /api/v1/documents/).
- POST /api/documents/identity-ocr.
- PUT /api/documents/identity-profile/me.

GET/health/me/content/download và DELETE/unlink giữ JWT/owner/CSRF/điều kiện xóa hiện có, không gọi status. Worker xử lý job đã nhận không gọi User hoặc cần internal token. Không hủy job cũ khi tài khoản vừa khóa; thao tác ghi/replay mới sẽ kiểm tra User lại, không cache quyết định allow.

## Cách Document gọi

```http
GET {USER_SERVICE_URL}/internal/v1/users/{user_id}/status
X-Internal-Token: {INTERNAL_TOKEN}
X-Request-ID: <request-id của request Document>
```

user_id **chỉ lấy từ JWT đã verify bằng common_auth**, không body/form/query/X-User-Id. Không forward Bearer/cookie của user hoặc đọc DB User. Một request5s, không retry/poll/cache allow; không follow redirect hoặc lấy proxy từ env; response JSON tối đa16KiB. Chỉ chấp nhận can_create_dossier kiểu boolean thật, không "true"/0/1. JSON duplicate key hoặc quá sâu bị chặn.

Ví dụ response **mô phỏng** đúng envelope nhóm cho User implement/test:

```json
{
  "data": {
    "can_create_dossier": false,
    "block_reason": "PROFILE_NOT_SUBMITTED",
    "block_message": "Bạn chưa nộp hồ sơ cá nhân."
  },
  "error": null,
  "request_id": "user-status-test"
}
```

Nếu khóa, User cần trả block_reason=ACCOUNT_LOCKED và block_message không rỗng, tối đa2000 ký tự. Document trả mã/message đó trong envelope lỗi của chính mình, không trả raw upstream body/traceback/token. FE hiển thị message như plain text/escaped text, không innerHTML.

## Cấu hình và bật code mới

backend/.env giữ USER_SERVICE_URL gốc (ví dụ http://user-service:8000, không /api/v1) và INTERNAL_TOKEN **giống User Service**. Compose đã bổ sung hai biến vào document-service, không chỉ contract-service. INTERNAL_TOKEN không được commit/Git/chat/FE/Postman Environment. Postman gọi Document bằng access token User cấp; không có header internal ở request FE.

http nội bộ chỉ chấp nhận user-service/localhost/127.0.0.1; host triển khai khác cần HTTPS. Không có switch bỏ guard khi lỗi. Đã có httpx trong requirements, không thêm requests hoặc dependency trùng.

Code đã build/test nhưng Document thật đang tắt theo user. Sau khi User status và token/config được nhóm cung cấp đúng, từ backend:

```powershell
docker compose build document-service
docker compose up -d --no-deps document-service
```

Muốn xử lý file queued thì bật worker/antivirus theo README_POSTMAN.md. Không cần migration mới hoặc restart service khác cho bước này. check_release.py hiện kiểm tra nền Document DB/Storage/schema, **không xác nhận User status endpoint đã hoạt động**.

## Citizen-profile không tự thay OCR/profile Document

GET {USER_SERVICE_URL}/internal/v1/users/{user_id}/citizen-profile là endpoint lấy thông tin cá nhân đã lưu của User. Chưa gọi endpoint này trong thay đổi guard; không cần để xác định quyền, không auto-copy/ghi đè OCR hoặc field user đã review.

Profile Document sáu field khác Citizen Profile của User. PUT profile Document **không tự làm User chuyển thành đã nộp hồ sơ**. FE vẫn cần luồng nộp hồ sơ/User API do người phụ trách User triển khai; không tự gọi API ghi User thay user hoặc gán OCR thành xác minh KYC.

## Test và gửi lại cho bạn phụ trách User

Kết quả cuối: **471 test pass**, gồm 47 case User gate; build API/worker, Compose và OpenAPI pass. Chưa test User status thật: endpoint chưa có trong code đã kiểm tra và INTERNAL_TOKEN chưa được điền trong `.env` tại thời điểm kiểm tra. Không tự bật Document thật trong lượt này.

- Active/complete: thao tác Document hợp lệ đi tiếp.
- Active/PROFILE_NOT_SUBMITTED: upload202, OCR201 nếu ảnh đọc được, profile200 nếu nguồn/revision hợp lệ.
- Locked với access token cũ còn hạn: cả ba thao tác403 ACCOUNT_LOCKED/message đúng; không side effect DB/Storage/OCR.
- Endpoint thiếu404, internal token sai401, timeout/503, redirect hoặc JSON sai: Document503, không side effect.
- Body/header tự gửi user_id của B: URL User vẫn là ID JWT của A; body thừa vẫn bị validation Document chặn.
- Không JWT hoặc cookie sai Origin: auth/CSRF chặn trước khi gọi User.
- Upload replay cùng key nhưng user vừa khóa:403, không trả success cached để vượt gate.

HTTP upstream trong bài test là mock theo hợp đồng trong tin nhắn, không phải chứng minh API User thật đã chạy. Các smoke native dùng adapter User giả được khai báo rõ trong script, chỉ dành DB/ảnh tổng hợp. Không dùng dependency override đó khi deploy API thật.

Bàn giao: [README_POSTMAN.md](README_POSTMAN.md), [README](README.md), source app/services/user_status.py và app/core/security.py. Kết quả hồi quy cuối được ghi trong docs/api/DOCUMENT_IMPLEMENTATION_TASKS.md.
