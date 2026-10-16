# Notification Service

Dịch vụ thông báo trong web cho citizen, luật sư và admin. Service nhận event từ các service khác, chống tạo trùng bằng `event_id`, và quản lý read/unread, mute/suppress.

Chưa làm email, mobile push hay browser push. `device_tokens` chỉ thêm khi hệ thống thật sự có ứng dụng mobile/browser push.

- `openapi.yaml`: API đọc thông báo, read/unread, mute và endpoint nhận event nội bộ; nghiệp vụ đều `planned`.
- `../../docs/api/NOTIFICATION_INTEGRATION.md`: đề xuất nhận event bằng API nội bộ, outbox, chống trùng và FE polling; chờ nhóm review.
- `../../contracts/examples/send_notification_event.py`: ví dụ producer gọi API có timeout/retry giới hạn.

## JWT đã gắn

- `GET /api/v1/notifications/health`: công khai.
- `GET /api/v1/notifications/me`: cần access token RS256; trả user_id/role từ token để test xác thực.
- API user mới đặt trong `authenticated_router`; người nhận lấy từ user.id, không từ user_id FE gửi.
- API đọc/đánh dấu/mute và API nhận event vẫn planned. Event hệ thống dùng router nội bộ riêng với X-Internal-Token, không gắn vào router JWT user.
- Cách chạy và test: `../../docs/api/JWT_DOCKER_CONTRACT_NOTIFICATION.md`.
