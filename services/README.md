# Backend services

Các microservice độc lập của hệ thống. Mỗi service có API, business logic, test, migration và database boundary riêng.

| Service | Phụ trách chính |
|---|---|
| `user-service` | Tài khoản, đăng nhập, hồ sơ và quyền user/admin |
| `lawyer-service` | Hồ sơ luật sư, năng lực và trạng thái online/offline |
| `agent-service` | Điều phối AI, intent/tool calling và lịch sử chat AI |
| `procedure-service` | Thủ tục, giấy tờ cần chuẩn bị, lệ phí và căn cứ pháp lý |
| `rag-service` | Index/tìm kiếm tài liệu pháp luật bằng vector |
| `document-service` | Upload file, OCR và dữ liệu giấy tờ |
| `contract-service` | Mẫu hợp đồng, nháp và sinh checklist/file |
| `location-service` | Cơ quan tiếp nhận, địa chỉ, tọa độ và bản đồ |
| `consultation-service` | Yêu cầu tư vấn và chat 1-1 giữa citizen với luật sư |
| `notification-service` | Thông báo trong web, đã đọc/chưa đọc và mute/suppress |

Không service nào được đọc trực tiếp database của service khác. Nếu cần dữ liệu khác, gọi API hoặc nhận event đã thống nhất.
