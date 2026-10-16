# Contract Service

Dịch vụ quản lý mẫu hợp đồng và dữ liệu cần điền vào hợp đồng.

C1–C8 cập nhật11/10/2026: auth/catalog/nháp/identity/generation và import/mapping mẫu riêng đã có code/test. Xem [tiến độ](../../docs/api/CONTRACT_IMPLEMENTATION_TASKS.md), [C4](README_ADMIN.md), [C5/phụ thuộc nhóm](README_DRAFTS.md), [C6](README_IDENTITY_SUGGESTIONS.md), [C7](README_GENERATIONS.md) và [Postman C8/phạm vi file nguồn](README_PERSONAL_TEMPLATES.md). C8 hỗ trợ DOCX marker rõ ràng hoặc PDF vùng trắng user-confirm, không tự nhận diện mọi ô trống/convert PDF sang Word. Bộ ba hợp đồng Drive có luồng native snapshot riêng: xem [kho mẫu Drive](README_DRIVE_CATALOG.md). Hai mẫu đất đai còn blocker nội dung/bố cục, không coi 30 file là mẫu published.

C3+C4: đã áp [hai migration database](database/README.md) lên Supabase Contract `dvpqrwvfuiujvvnccpox`: 10 bảng/92 cột, RLS và readiness DB kiểm tra thật pass. C5–C8 không thêm migration. Storage private đã smoke thật; worker opt-in, chưa bật production trong lượt này. Bộ Drive mới đã đăng ký ba mẫu (thuê published, hai mẫu đất đai draft/ẩn); mẫu thử C2 cũ không được seed riêng.

- `openapi.yaml`0.8.1 / bản tách0.4.0: catalog/version/publish, nháp, identity-suggestions, generation và8 API personal `implemented` trong phạm vi đã công bố; version thêm tùy chọn `form_options` cho FE dựng dropdown. Demo `/contracts/templates` vẫn `partial`; checklist/Notification C9 vẫn `planned`.
- Hướng dẫn chạy và Postman: [admin/catalog](README_ADMIN.md), [nháp](README_DRAFTS.md), [gợi ý hồ sơ](README_IDENTITY_SUGGESTIONS.md), [xuất Word/PDF](README_GENERATIONS.md), [mẫu riêng](README_PERSONAL_TEMPLATES.md), [mẫu Drive](README_DRIVE_CATALOG.md).
- `openapi-user-templates.yaml`: thiết kế import mẫu riêng DOCX/PDF, phân tích chỗ điền,
  user xác nhận mapping và PDF preview; bản chính `openapi.yaml` đã chứa toàn bộ API này.
- `database/erd.dbml`: schema C3+C4 đã test (10 bảng/92 cột/8 FK nội bộ); SQL là nguồn chính cho CHECK/trigger/index/RLS.
- Hướng dẫn mẫu user: `../../docs/api/USER_CONTRACT_TEMPLATES.md`.

## JWT đã gắn

- `GET /api/v1/contracts/health`: công khai, Docker healthcheck không cần token.
- `GET /api/v1/contracts/me`: cần access token RS256; trả user_id/role từ token để test xác thực, không phải API hồ sơ.
- `GET /api/v1/contracts/ready`: cần access token; chỉ đọc database/schema, cấu hình chưa có trả503. Health vẫn200 không có nghĩa DB đã sẵn sàng.
- `GET /api/v1/contracts/templates`: nay cần access token; vẫn là dữ liệu demo, chưa có DB.
- Catalog router C4 dùng `catalog_user`/`catalog_admin`: JWT + kiểm tra admin còn hoạt động/role hiện tại qua User `/api/v1/users/me`, kể cả đọc version draft/metadata ẩn. Không lưu/log Bearer. User lỗi/thiếu cấu hình chặn503 trước thao tác DB; không yêu cầu admin có citizen-profile. Router nháp C5 dùng dossier_creator trước tạo và contract_user cho đọc/sửa/xóa; repository lọc owner lấy từ JWT, không nhận owner FE, admin không bypass. /status chưa có trong User source local, phải có service nhóm chạy thật; không bỏ guard khi thiếu.
- Cookie mutation chỉ chấp nhận exact Origin trong CONTRACT_ALLOWED_ORIGINS; Bearer được ưu tiên theo common_auth. CORS chỉ ở Gateway. Body/header X-Request-ID nhất quán, no-store; lỗi không echo input/PII/secret.
- Cách chạy và test: `../../docs/api/JWT_DOCKER_CONTRACT_NOTIFICATION.md`.
- Event khi sinh file xong/lỗi: `../../docs/api/NOTIFICATION_INTEGRATION.md`.

## Phạm vi nhánh bàn giao

Nhánh `HIEN/contract_service` chỉ bổ sung code chạy, SQL/schema, OpenAPI, cấu hình Docker và hướng dẫn sử dụng. Test mới, script QA/chuyển đổi mẫu, ảnh render, dữ liệu thử và bản DOCX chuẩn bị được giữ local. Kết quả kiểm thử đã thực hiện được ghi trong README; không đồng nghĩa pipeline GitLab đã đạt. Catalog Drive dùng snapshot đã đăng ký trên Supabase Contract, không cần file Word local để API/worker chạy.
