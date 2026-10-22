# Lawyer Service

Dịch vụ quản lý hồ sơ năng lực luật sư, thống kê và trạng thái làm việc online/offline.

Thiết kế, luồng gọi và mã lỗi: [docs/api/LAWYER_DESIGN.md](../../docs/api/LAWYER_DESIGN.md). Contract: `openapi.yaml`.

Đã có (prefix `/api/v1`, Gateway `/api`):
- `GET /lawyers/specializations` - danh sách lĩnh vực.
- `POST /lawyers` - citizen gửi hồ sơ xin làm luật sư; `GET /lawyers/me` - xem hồ sơ của tôi;
  `PUT /lawyers/me` - gửi lại sau khi bị từ chối.
- Admin: `GET /lawyers/applications` (lọc `approval_status`, `order`, phân trang),
  `POST /lawyers/{lawyer_id}/approve`, `POST /lawyers/{lawyer_id}/reject`.
- Nội bộ (`X-Internal-Token`): `GET /internal/v1/lawyers/{user_id}/status`, `POST /internal/v1/lawyers/{user_id}/hide`.

Chưa có (`x-status: planned`): danh sách / chi tiết luật sư công khai, online/offline (WebSocket cùng Consultation),
thống kê.

```bash
cd services/lawyer-service
python -m uvicorn app.main:app --reload --port 8002 --env-file ../../.env   # cần LAWYER_DATABASE_URL, USER_SERVICE_URL, DOCUMENT_SERVICE_URL, INTERNAL_TOKEN
cd ../.. && python -m pytest services/lawyer-service/tests -q                # không cần mạng / Supabase
```

Database: `database/001_lawyer_schema.sql`, kiểm tra bằng `database/check_lawyer_db.sql` (xem `database/README.md`).

User Service vẫn sở hữu tài khoản, vai trò và trạng thái khóa/mở tài khoản. Lawyer Service chỉ lưu `user_id` để tham chiếu logic, không truy cập trực tiếp User Database.
