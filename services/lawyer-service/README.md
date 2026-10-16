# Lawyer Service

Dịch vụ quản lý hồ sơ năng lực luật sư, thống kê và trạng thái làm việc online/offline.

Thiết kế, luồng gọi và mã lỗi: [docs/api/LAWYER_DESIGN.md](../../docs/api/LAWYER_DESIGN.md). Contract: `openapi.yaml`.

Đã có: lĩnh vực, hồ sơ của tôi (`GET/PATCH /lawyers/me`), đơn xin làm luật sư / đổi thẻ (`/lawyers/me/applications`),
admin tạo tài khoản luật sư, danh sách / đếm / chi tiết / duyệt / từ chối đơn, API nội bộ trạng thái và ẩn hồ sơ.
Chưa có: danh sách luật sư công khai, online/offline (WebSocket cùng Consultation), thống kê.

```bash
cd services/lawyer-service
python -m uvicorn app.main:app --reload --port 8002 --env-file ../../.env   # cần LAWYER_DATABASE_URL, USER_SERVICE_URL, DOCUMENT_SERVICE_URL, INTERNAL_TOKEN
cd ../.. && python -m pytest services/lawyer-service/tests -q                # không cần mạng / Supabase
```

Database: `database/001_lawyer_schema.sql`, kiểm tra bằng `database/check_lawyer_db.sql` (xem `database/README.md`).

User Service vẫn sở hữu tài khoản, vai trò và trạng thái khóa/mở tài khoản. Lawyer Service chỉ lưu `user_id` để tham chiếu logic, không truy cập trực tiếp User Database.
