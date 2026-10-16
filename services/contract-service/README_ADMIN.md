# C4 — Admin quản lý mẫu hợp đồng

Đã triển khai catalog **system** và version plain text. Không tự nhập/xuất bản 30 file Drive, không upload/render DOCX/PDF ở C4. Mẫu thử C2 vẫn cần duyệt nội dung; điền/xuất file ở C7.

## Chạy và cấu hình

Chạy từ backend với Docker Compose theo hướng dẫn JWT chung. `docker compose build contract-service` rồi `docker compose up -d user-service contract-service gateway` khi đã cấu hình đúng môi trường nhóm. Lệnh up có thể khởi động thêm phụ thuộc của Gateway. Không dùng key/password của project Document thay Contract.

Giữ `CONTRACT_DATABASE_URL` có TLS trong .env riêng của máy chạy. Hai migration 001/002 đã áp project Contract `dvpqrwvfuiujvvnccpox`; **không chạy lại**. Máy khác trỏ cùng project không cần tạo bảng lại. `/ready` yêu cầu migration C4 có cột last_version.

`USER_SERVICE_URL` là URL gốc User (Docker: `http://user-service:8000`), không thêm `/api/v1`. C4 dùng Bearer của người gọi để gọi `GET /api/v1/users/me`, API đã có trong code User. Không dùng INTERNAL_TOKEN cho /me; INTERNAL_TOKEN vẫn dành cho /status ở C5. Máy bạn chạy User phải có endpoint này và cùng cấu hình JWT. Contract chỉ mount public key; private key ký access token thuộc User.

Postman đặt `base_url=http://localhost:8080/api/contracts` khi đi Gateway; nếu tự chạy uvicorn cổng8000 thì `http://localhost:8000/api/v1/contracts`. Docker Compose không publish riêng port Contract. Header `Authorization: Bearer {{admin_access_token}}`, token lấy bằng đăng nhập tài khoản admin của User; thêm `Content-Type: application/json` khi có body.

## Luồng Postman

1. `POST {{base_url}}/template-catalog`, body dưới đây; nhận201, lưu `data.id` thành template_id. Mẫu ban đầu ẩn.

```json
{"name":"Mẫu thử quản trị","description":"Dữ liệu test, không phải mẫu đã duyệt pháp lý"}
```

2. `POST {{base_url}}/template-catalog/{{template_id}}/versions`; nhận201, lưu version_id. Server cấp số version, không gửi số từ FE.

```json
{"content":"Bên A: {{party_a_name}}","fields":[{"name":"party_a_name","label":"Họ tên bên A","type":"text","required":true,"identity_key":"full_name"}]}
```

3. `PATCH {{base_url}}/template-versions/{{version_id}}` để sửa bản nháp, body phải có đầy đủ content + fields như bước2 (thay toàn bộ, không merge fields).
4. `POST {{base_url}}/template-versions/{{version_id}}/publish`, không body; nhận200. Gọi lặp trả bản published hiện có. Publish không tự hiện mẫu.
5. `PATCH {{base_url}}/template-catalog/{{template_id}}`, body `{"is_visible":true}`. Chưa có bản published thì409.
6. `GET {{base_url}}/template-catalog?page=1&page_size=20`: user thường chỉ thấy mẫu hiện có published. Admin dùng `include_hidden=true` xem cả mẫu ẩn. `GET /template-catalog/{{template_id}}`, `GET /template-catalog/{{template_id}}/versions`, `GET /template-versions/{{version_id}}` để xem chi tiết.
7. Ẩn mẫu bằng PATCH `{"is_visible":false}`. `DELETE /template-catalog/{{template_id}}` xóa mềm, nhận204 không body; nếu đã có nháp tham chiếu thì409, hãy ẩn thay vì xóa. `DELETE /template-versions/{{version_id}}` chỉ xóa bản draft chưa tham chiếu; bản published trả409. Số version đã cấp không dùng lại.

Các bước trên tạo dữ liệu trên project bạn cấu hình: chỉ dùng tên/mẫu test được nhóm cho phép, không dùng CCCD thật. Agent chưa chạy luồng Postman ghi dữ liệu lên Supabase.

## Quy tắc và lỗi cần test

- Thiếu token401; citizen/lawyer sửa mẫu403. Admin có token cũ nhưng đã hạ quyền/khóa vẫn403; tài khoản không còn hợp lệ401. User lỗi/thiếu URL/payload sai503, không ghi DB. Kiểm tra cả admin đọc mẫu ẩn/draft.
- ID mẫu personal trả404 kể cả admin; catalog không trả nội dung cá nhân. Mẫu system đã ẩn vẫn cho chủ nháp cũ xem version ghim, không cho người khác.
- Body không nhận owner/kind/version/status/source key. Tên trống, field trùng, placeholder chưa khai báo hoặc biểu thức kiểu `{{name.upper()}}` trả422; không chạy Jinja/mã trong mẫu. Field required phải xuất hiện trong content. Tối đa500 field, tên field64 ký tự, content200000 ký tự.
- Bản published không sửa/xóa. Đổi nội dung phải tạo version mới. Tạo version đồng thời được cấp số riêng trong transaction. Cập nhật content/fields lỗi thì rollback toàn bộ.
- Body JSON lỗi400; UUID/query/body không hợp lệ422. Database lỗi503 hoặc conflict409; lỗi không lộ SQL/mật khẩu. Response `{data,error,request_id}`, X-Request-ID và no-store; DELETE204 không body.
- Cookie mutation cần Origin nằm trong CONTRACT_ALLOWED_ORIGINS. Bearer Postman không yêu cầu Origin.

## Giới hạn xác minh

CRUD đã test HTTP với PostgreSQL tạm thật, không dùng Supabase làm database test. Adapter User test mock đúng payload /me của nhóm, chưa test liên thông máy người phụ trách User. Supabase thật đã áp schema/RLS/readiness; chưa test CRUD với tài khoản admin thật, chưa triển khai worker/Storage/UI, chưa push GitLab.

Kết quả 10/10/2026: 294 test pass (57 C4 +25 PostgreSQL C3 +212 hồi quy liên quan), một warning deprecation dependency. OpenAPI/Compose validate và Docker build/smoke non-root/read-only/network-none pass. Database test tạm đã dọn.
