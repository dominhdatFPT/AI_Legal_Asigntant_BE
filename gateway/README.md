# Gateway

Thư mục chứa cấu hình Nginx làm cổng vào chung và định tuyến request đến các service.

## Gọi API qua cổng 8080

- Contract: `http://localhost:8080/api/contracts/me` và `/api/contracts/templates`.
- Notification: `http://localhost:8080/api/notifications/me`.
- Gửi `Authorization: Bearer <access_token>`; health không cần token.
- `/api/...` được đổi thành `/api/v1/...` khi gửi tới service. `/internal/v1/...` không có route public.

Gateway dùng Docker DNS `127.0.0.11` và upstream `resolve`/`zone` để theo dõi IP container khi recreate; service chưa chạy không ngăn toàn Gateway nạp cấu hình. Yêu cầu Nginx >= 1.27.3; xem [tài liệu Nginx](https://nginx.org/en/docs/http/ngx_http_upstream_module.html#resolve).
Service chưa chạy vẫn không gọi được nghiệp vụ của nó (có thể trả 502); không coi đó là API đã implement.

Document mặc định cổng 8000 theo code mới; có backup 5000 để giữ container Flask cũ trên máy hoạt động. Bỏ backup khi nhóm đã cập nhật toàn bộ Document sang FastAPI.

Kiểm tra/reload không restart các service khác:

```powershell
docker compose exec -T gateway nginx -t
docker compose exec -T gateway nginx -s reload
python scripts/check_contract_notification_jwt.py --gateway-url http://localhost:8080
```

Chỉ reload sau khi `nginx -t` thành công. CORS hỗ trợ Authorization, X-Request-ID và Idempotency-Key cho FE localhost cổng 3000. Gateway tạo request_id riêng, vì vậy có thể khác header do client gửi.

Ngày 08/10/2026: đã reload Gateway đang chạy, 24 test JWT qua 8080 đạt; 8 health route đang chạy giữ HTTP 200, preflight 204, CORS trên lỗi 401 hoạt động, đường dẫn internal vẫn 404.
