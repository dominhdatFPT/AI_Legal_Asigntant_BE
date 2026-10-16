# Ví dụ gọi API liên service

`send_notification_event.py` là mẫu producer gửi event tới Notification bằng API nội bộ.
Chạy trong mạng Docker của backend hoặc đặt NOTIFICATION_SERVICE_URL thành URL private truy cập được.

Mẫu JSON: `../notification-event.example.json`.
Quy tắc: `../../docs/api/NOTIFICATION_INTEGRATION.md`.

Mẫu chưa có outbox/DB và endpoint Notification hiện chưa triển khai. Chạy trực tiếp sẽ gửi HTTP thật; các token lấy từ môi trường.
