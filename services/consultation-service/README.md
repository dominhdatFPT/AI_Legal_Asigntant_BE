# Consultation Service

Dịch vụ hàng chờ tư vấn và chat text 1-1 giữa người dùng (`citizen` hoặc `lawyer`) với luật sư: gửi yêu cầu vào hàng chờ,
luật sư nhận, nhắn lại luật sư cũ, tin nhắn, đóng/hủy, report và đồng ý điều khoản. Thông báo realtime qua WebSocket.

Thiết kế, sơ đồ trạng thái, chống tranh chấp và job dọn dữ liệu: [docs/api/CONSULTATION_DESIGN.md](../../docs/api/CONSULTATION_DESIGN.md).
Contract: `openapi.yaml`. Schema: `database/001_consultation_schema.sql`.

Để sau demo: upload file, AI đánh giá report, cờ `warning` đánh dấu toxic, Agent tự đề xuất luật sư, nối Notification Service.
