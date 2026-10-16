# Legal Assistant AI Backend

Backend dạng microservices cho hệ thống AI hỗ trợ tra cứu thủ tục hành chính, tài liệu pháp lý và hợp đồng.

## Service ownership

Mỗi service có boundary dữ liệu riêng:

- `user-service`: `USER_DATABASE_URL`
- `lawyer-service`: `LAWYER_DATABASE_URL`
- `procedure-service`: `PROCEDURE_DATABASE_URL`
- `rag-service`: `RAG_DATABASE_URL`
- `document-service`: `DOCUMENT_DATABASE_URL`
- `contract-service`: `CONTRACT_DATABASE_URL`
- `location-service`: `LOCATION_DATABASE_URL`
- `consultation-service`: `CONSULTATION_DATABASE_URL`
- `notification-service`: `NOTIFICATION_DATABASE_URL`
- `agent-service`: `AGENT_DATABASE_URL` nếu cần lưu state; MVP có thể stateless

Thư mục `database/` ở root chỉ chứa quy ước cấp hệ thống. Thiết kế bảng và migration phải nằm trong service sở hữu dữ liệu.

## Services

Gateway route 10 service qua `gateway/nginx.conf`. `lawyer-service`, `consultation-service` và `notification-service` đã có skeleton, health check, database boundary, OpenAPI health contract và route Gateway; nghiệp vụ CRUD, WebSocket chat và event notification sẽ được làm tiếp trong từng service.
