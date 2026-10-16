# API Documentation

Lưu API contract, quy ước endpoint, error response và hướng dẫn gọi Gateway.

- [API Procedure Service cho Frontend](PROCEDURE_API.md): danh sách endpoint, tra cứu thủ tục, bộ lọc lĩnh vực, checklist hồ sơ, lệ phí và mock data.
- [Bàn giao FE: Document, Contract, Notification](FRONTEND_DOCUMENT_CONTRACT_NOTIFICATION.md): luồng màn hình, API, trạng thái mock/planned và tiêu chí nghiệm thu cho cả ba service.
- [Document T7/T8 hiện tại](DOCUMENT_RELEASE.md): outbox/dispatcher, handoff adapter, Docker hardening/readiness, runtime đang tắt và phụ thuộc Contract/Notification. Các ghi nhận planned/VNPT dưới đây là lịch sử.
- [Tiến độ triển khai Document](DOCUMENT_IMPLEMENTATION_TASKS.md): task nhỏ, kiểm tra theo quy ước nhóm và giới hạn chưa nghiệm thu.
- [Document CCCD hai ảnh — Task 4](DOCUMENT_IDENTITY_OCR.md): API/session/adapter theo Java mẫu đã có code/test local; probe VNPT thật báo endpoint unsupported, chưa nghiệm thu hoặc apply migration mới cloud.
- [API Contract, Notification dễ đọc](CONTRACT_NOTIFICATION_API.md): danh sách URL, dữ liệu và luồng sử dụng; hai service Hiền phụ trách.
- [Nhận sự kiện thông báo](NOTIFICATION_INTEGRATION.md): đề xuất API nội bộ, chống trùng, outbox, retry và FE polling.
- [Mẫu hợp đồng riêng của user](USER_CONTRACT_TEMPLATES.md): ERD/OpenAPI mở rộng DOCX, PDF có chữ và PDF scan, vị trí điền và xem trước.
- [Document nối Contract](DOCUMENT_CONTRACT_INTEGRATION.md): upload/processing/OCR, API lấy metadata/file/kết quả, quyền sở hữu và snapshot; mọi API mới planned.
- [CCCD lưu một lần, chỉ điền thông tin CCCD](IDENTITY_PROFILE_FILLING.md): detail user → profile Document → Contract suggestions; mục khác hướng dẫn tự nhập.
- [JWT và Docker cho Contract/Notification](JWT_DOCKER_CONTRACT_NOTIFICATION.md): phần đã gắn vào code, cấu hình key và ví dụ Postman.
- OpenAPI chi tiết: `../../services/procedure-service/openapi.yaml`, `../../services/contract-service/openapi.yaml`, `../../services/notification-service/openapi.yaml`.

Các tài liệu trên là bản thiết kế và bàn giao API cho nhóm; xem `x-status` để phân biệt API hiện có với API planned.
