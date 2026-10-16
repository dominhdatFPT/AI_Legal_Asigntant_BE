# JWT và Docker cho Contract / Notification

## 1. Đã làm gì?

Hai service dùng thư viện `libs/common_auth` của Đạt, không tự viết bộ JWT khác.

- Đã gọi `add_auth_error_handler(app)` để lỗi token đúng format nhóm.
- Có `authenticated_router` với `Depends(get_current_user)`: API đặt vào router này mặc định cần access token.
- Health đặt ngoài router, không cần token.
- Contract `/templates` đã được bảo vệ. Đây là thay đổi so với bản demo cũ: FE/Postman phải gửi token.
- Thêm `/me` ở mỗi service để kiểm tra việc gắn JWT. Nó chỉ đọc user_id/role từ token, không truy vấn DB, không trả hồ sơ cá nhân hay danh sách thông báo.
- Chưa code nháp, sinh file, nhận event, DB thông báo hay popup. OpenAPI vẫn ghi các API này là planned.

| API qua Gateway | Cần token? | Hiện có |
|---|---|---|
| GET /api/contracts/health | Không | Health |
| GET /api/notifications/health | Không | Health |
| GET /api/contracts/me | Có | Kiểm tra JWT |
| GET /api/notifications/me | Có | Kiểm tra JWT |
| GET /api/contracts/templates | Có | Danh sách mẫu demo |

## 2. Token và key là gì?

User Service dùng private key để ký access token RS256. Hai service này chỉ dùng public key để kiểm tra chữ ký.

Không đưa private key vào Contract/Notification, không đưa key/token thật lên Git.
Token phải đúng issuer `legal-assistant-user-service`, type `access`, còn hạn và có sub/role.
`sub` là user id; `role` là citizen/lawyer/admin. Token không hợp lệ không được gọi API user.

Trong Docker, Compose đã đặt `JWT_PUBLIC_KEY_PATH=/run/keys/jwt_public.pem`; Dockerfile đã cài `libs/common_auth`.
Compose của hai service đã đổi sang mount riêng file public key read-only, không mount cả thư mục chứa private key. Thiếu file public key thì Docker báo lỗi, không tự tạo thư mục giả thay file.
Health có thể chạy ngay cả khi chưa có key, nhưng API cần token chỉ kiểm tra được khi có public key đúng.

## 3. Chạy Docker

Trong `.env` ở gốc backend, đặt thư mục key nhóm cung cấp:

```dotenv
JWT_KEYS_DIR=C:/duong-dan-thu-muc-key-cua-nhom
```

Đây là đường dẫn mẫu, phải thay bằng thư mục thật chứa `jwt_public.pem`. Nếu chạy User Service thì thư mục đó cần thêm private key của User Service.
Trên máy Hiền đã giải nén keys.zip vào `.keys/keys`, đặt `JWT_KEYS_DIR=./.keys/keys` và hai đường dẫn key local tuyệt đối trong `.env`. `.env` và `.keys/` đều được Git bỏ qua.
Không đưa mật khẩu database chung trong bản cấu hình được gửi vào môi trường test: các DATABASE_URL hiện để trống, phần JWT không cần DB.

Tại gốc backend:

```powershell
docker compose build contract-service notification-service
docker compose up -d contract-service notification-service
docker compose ps
```

Để test hai service mà không cần Gateway/toàn hệ thống (đang dùng cách này trên máy Hiền):

```powershell
docker compose -f docker-compose.yml -f docker-compose.jwt-local.yml up -d --build --no-deps --wait contract-service notification-service
```

Override này chỉ mở `127.0.0.1:8006` cho Contract và `127.0.0.1:8009` cho Notification, không mở ra mạng ngoài. Đây là cách test trực tiếp tùy chọn; Gateway 8080 nay đã cập nhật và dùng được cho hai service.

Hai service không mở cổng ra host. Muốn gọi từ Postman qua `localhost:8080`, Gateway phải chạy cùng các service mà nó phụ thuộc:

```powershell
docker compose up -d --build
```

Lệnh chạy toàn stack chỉ dùng khi đã có cấu hình cần thiết của nhóm, gồm DB/Firebase của User Service. Không có cấu hình đó thì chỉ build/test hai service trước.

Ngày 08/10/2026 đã build/chạy Docker thực tế thành công; cả hai container healthy. Đã thêm `libs/.dockerignore` để Docker không lấy cache test gây lỗi Access denied.

## 4. Test bằng Postman

Lấy access token từ đăng nhập User Service. Trong môi trường dev, có thể dùng script Đạt đã viết:

```powershell
python scripts/mint_dev_token.py --role citizen --key-file "C:/duong-dan-key/jwt_private.pem"
```

Chạy tại gốc backend sau khi cài requirements-test.txt. Key phải cùng cặp với public key service đang dùng. Script này chỉ dành cho dev; không đưa private key production cho người dùng.

Postman: GET `http://localhost:8080/api/contracts/me`, chọn Authorization → Bearer Token → dán access token.
Làm tương tự với `/api/notifications/me` và `/api/contracts/templates`.

Gateway 8080 đã cập nhật ngày 08/10/2026: hiện dùng được URL Postman bên trên. Nếu muốn bỏ qua Gateway để debug, dùng URL trực tiếp:

- GET `http://127.0.0.1:8006/api/v1/contracts/me`
- GET `http://127.0.0.1:8009/api/v1/notifications/me`
- GET `http://127.0.0.1:8006/api/v1/contracts/templates`

Vẫn chọn Bearer Token như trên. Health tương ứng là `/api/v1/contracts/health` và `/api/v1/notifications/health`, không cần token.

Test tự động HTTP thật (chạy tại gốc backend, sau khi đã cài requirements-test.txt):

```powershell
python scripts/check_contract_notification_jwt.py
```

Test cùng 24 trường hợp qua Gateway:

```powershell
python scripts/check_contract_notification_jwt.py --gateway-url http://localhost:8080
```

Script đọc private key local để ký token dev trong RAM, không in/lưu token, không gửi private key vào container, không ghi DB. Không chạy `try_jwt.py` trong ZIP vì script đó dùng role `user` khác quy ước nhóm.

- Token đúng: 200.
- Không có token / sai chữ ký / sai issuer / token không phải access: 401, UNAUTHORIZED.
- Hết hạn: 401, TOKEN_EXPIRED.
- Token đúng nhưng sai role ở API có `require_role`: 403, FORBIDDEN.

## 5. Thêm API mới thế nào?

API user: thêm vào `authenticated_router` trước dòng `app.include_router(authenticated_router)`.
API admin: thêm dependency kiểm tra role, bên cạnh JWT đã có trên router:

```python
from common_auth.fastapi_dep import require_role

@authenticated_router.post("/duong-dan-admin", dependencies=[Depends(require_role("admin"))])
def admin_action():
    ...  # viết nghiệp vụ thật; đây chỉ là mẫu cách gắn quyền
```

Đăng nhập không có nghĩa được xem dữ liệu của người khác. API hợp đồng cá nhân phải lọc owner_id theo user.id; Notification phải lọc recipient_user_id theo user.id khi triển khai DB.
JWT phản ánh quyền lúc cấp token, không tự xác nhận tài khoản vừa bị khóa ngay sau đó.
API event hệ thống sẽ dùng router riêng với X-Internal-Token; không dùng token của user và chưa triển khai trong bước này.

## 6. Chạy test offline

```powershell
python -m pip install -r requirements-test.txt
python -m pytest tests/test_contract_notification.py tests/test_config.py libs/common_auth -q
```

Key test tự sinh trong RAM, không cần xin key thật, không ghi key/token vào repo.
Test kiểm tra health công khai, token hợp lệ, hết hạn, sai chữ ký/issuer/type, lỗi chuẩn, templates cần JWT, và cách gắn require_role admin trên router.
Test admin dùng endpoint tạm chỉ trong test, không tạo thêm API admin giả trong service.

Kết quả lần này: 74 test ở lệnh trên đạt; thêm 27 test API hồi quy đạt (không chạy test health User Service trong môi trường này). Hai OpenAPI sau cập nhật hợp lệ, 34 operation và 179 example request/response qua kiểm tra schema.

Ngày 08/10/2026: chạy lại 74 test offline đạt; thêm 24 test HTTP thật trên hai container đạt (health, 3 role, thiếu/sai/hết hạn token, sai issuer/type/signature, bỏ qua user_id giả từ query, templates cần JWT). Kết quả này chỉ xác nhận tích hợp JWT và API hiện có, không xác nhận các nghiệp vụ planned hoặc đăng nhập Firebase/DB thật.

Sau cập nhật Gateway 8080: 24 test JWT qua Gateway cũng đạt; 78 test offline gồm regression Gateway đạt. 8 route health đang chạy giữ 200, CORS preflight 204 và lỗi JWT 401 có header CORS; /internal/v1 vẫn không được public. Lawyer/Consultation trong stack này chưa chạy, không tự khởi động trong lần cập nhật Gateway.
