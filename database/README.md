# Database registry

Đây chỉ là nơi ghi chú các quy ước database cấp hệ thống, không phải database dùng chung.

Mỗi service sở hữu database và migration riêng trong thư mục của service:

- `services/user-service/database/`
- `services/lawyer-service/database/`
- `services/procedure-service/database/`
- `services/rag-service/database/`
- `services/document-service/database/`
- `services/contract-service/database/`
- `services/location-service/database/`
- `services/consultation-service/database/`
- `services/notification-service/database/`

Không đặt bảng hoặc migration dùng chung ở thư mục này. Các service giao tiếp qua API/event, không đọc trực tiếp database của nhau.
