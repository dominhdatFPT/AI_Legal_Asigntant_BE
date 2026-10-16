# Contracts

Thư mục chứa các contract liên service và giữa frontend với backend.

- OpenAPI hoặc JSON Schema cho request/response.
- Event schema nếu sau này dùng message broker.
- Không đặt model database hoặc business logic vào đây.

## Thông báo qua API nội bộ

- `notification-event.example.json`: JSON event mẫu.
- `examples/send_notification_event.py`: mẫu producer gọi API, có timeout/retry giới hạn.
- `../docs/api/NOTIFICATION_INTEGRATION.md`: cách tích hợp và quy tắc chống trùng.
- Schema chuẩn nằm trong `../services/notification-service/openapi.yaml`, `components.schemas.Event`.
