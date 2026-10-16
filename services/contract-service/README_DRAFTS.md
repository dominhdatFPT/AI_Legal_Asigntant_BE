# C5 — chọn mẫu và lưu nháp cá nhân

Đã có 5 API CRUD nháp, dùng catalog/version C4. Không sinh file hay tự copy dữ liệu CCCD ở C5. Owner lấy từ JWT; admin không được xem/sửa/xóa nháp người khác.

## Phụ thuộc nhóm cần chạy thật

| Service | API Contract gọi | Xác thực | Tình trạng repo develop d33a6c7 |
|---|---|---|---|
| User | GET /internal/v1/users/{JWT-sub}/status trước tạo nháp | X-Internal-Token = INTERNAL_TOKEN | Chưa có route local; adapter theo API người phụ trách đã gửi |
| Document | GET /api/v1/documents/{document_id} khi đính kèm | Bearer của người gọi | Đã có code owner-only |
| Procedure | GET /api/v1/procedures/{procedure_id} khi chọn thủ tục | Công khai; không gửi JWT/internal token | Chưa có route detail local; OpenAPI planned |

Không tự thêm endpoint User/Procedure hoặc dùng DB của service khác. Thiếu endpoint/cấu hình/lỗi/payload sai trả503, không tự cho phép. 404 chuẩn PROCEDURE_NOT_FOUND/DOCUMENT_NOT_FOUND mới coi là ID không có; route404 chung là service chưa sẵn sàng (503). Chọn procedure_id phải là UUID, không dùng mã demo HKD-001.

Config .env máy chạy: CONTRACT_DATABASE_URL có TLS, USER_SERVICE_URL (URL gốc), INTERNAL_TOKEN đúng với User. DOCUMENT_SERVICE_URL/PROCEDURE_SERVICE_URL chỉ cần khi có tham chiếu tương ứng. Trong Docker dùng hostname service, không dùng localhost để gọi container khác. Cookie được chuyển sang Bearer khi gọi Document, không chuyển tiếp toàn bộ cookie. Token/password không vào Git/log/nháp.

C5 không cần migration mới: schema001+002 trên project Contract đã đủ. Không tạo thêm bảng/seed/dữ liệu hợp đồng thật trong lần triển khai C5.

## Luồng Postman cho bạn test

`base_url=http://localhost:8080/api/contracts` qua Gateway; nếu tự chạy uvicorn Contract cổng8000: `http://localhost:8000/api/v1/contracts`. Mỗi API có header `Authorization: Bearer {{user_access_token}}`. Token lấy bằng đăng nhập User, không tự ký bằng private key trong Contract.

1. Admin chuẩn bị mẫu theo README_ADMIN.md: tạo metadata → version → publish → is_visible=true. Mẫu Drive không được tự seed/xuất bản.
2. User `GET {{base_url}}/template-catalog`, chọn mẫu; `GET /template-catalog/{{template_id}}/versions`, lấy một version published; `GET /template-versions/{{version_id}}` xem content/fields.
3. `POST {{base_url}}/drafts` với body dưới đây; nhận201, lưu `data.id` làm draft_id. User phải có /status trả can_create_dossier=true và block_reason=null. ACCOUNT_LOCKED/PROFILE_NOT_SUBMITTED trả403 với block_message; User lỗi503, không có nháp được tạo.

```json
{"template_version_id":"THAY-BANG-UUID-VERSION","title":"Nháp hợp đồng của tôi","field_values":{},"document_ids":[]}
```

Không gửi owner_user_id/user_id. `procedure_id` bỏ qua hoặc null thì không gọi Procedure. Nếu có UUID thủ tục thì phải kiểm tra được detail; không dùng ID giả để né lỗi service.

4. `GET {{base_url}}/drafts?page=1&page_size=20` và `GET {{base_url}}/drafts/{{draft_id}}` đọc nháp. Kết quả chỉ thuộc JWT sub, danh sách updated_at giảm dần.
5. `PATCH {{base_url}}/drafts/{{draft_id}}`, ví dụ dưới đây dùng field đã khai báo ở mẫu thử README_ADMIN (đổi tên key nếu mẫu khác):

```json
{"title":"Nháp đã sửa","field_values":{"party_a_name":"Người dùng thử"}}
```

Field_values gửi lên **thay toàn bộ map**, không merge; document_ids tương tự. Key không gửi giữ nguyên. `{ "field_values": {} }` hoặc `{ "document_ids": [] }` xóa giá trị/liên kết tương ứng. Không đổi template_version_id, procedure_id hoặc owner qua PATCH.

6. Upload giấy tờ vào Document bằng user này, chờ file_status=available; PATCH `{"document_ids":["UUID-TAI-LIEU"]}`. Contract kiểm tra từng Document detail, ID và owner phải khớp. Pending_check/rejected trả409; tài liệu của người khác/đã xóa trả404; service lỗi503, nháp không bị sửa. Không gọi Document khi PATCH không gửi document_ids.
7. `DELETE {{base_url}}/drafts/{{draft_id}}` trả204 không body, xóa mềm. Không xóa tài liệu gốc Document. Nháp đã xóa trả404 khi đọc/sửa/xóa lại.

Response `{data,error,request_id}`, X-Request-ID/no-store; POST201, GET/PATCH200, DELETE204. Payload/UUID/query không hợp lệ422, JSON hỏng400. Chi tiết lỗi field chỉ ghi tên trường, không echo giá trị riêng tư.

## Quy tắc lưu dở và concurrency

- Cho phép thiếu required; null hoặc chưa có key là chưa điền. Text phải là string, date là YYYY-MM-DD đúng ngày lịch, number là số (không nhận bool/chuỗi số), boolean chỉ true/false. Không nhận field ngoài version, object/array, NaN/Infinity. Tối đa500 key/512KiB JSON, 100 document ID duy nhất; title tối đa200 ký tự.
- Nháp ghim version published. System phải đang hiện/chưa deleted khi tạo; personal phải đúng chủ, published và có analysis confirmed đúng version/owner (C8 mới có luồng import/confirm). Không công bố mẫu personal qua catalog admin.
- Ẩn mẫu hoặc tạo version mới không đổi nháp cũ. Published fields bất biến, sửa nháp cũ vẫn dùng type của version đã ghim.
- Update/delete dùng row lock của draft và chặn generation queued/processing409. Content/link/revision cùng transaction, lỗi rollback tất cả. Kiểm tra lại điều kiện lúc ghi sau preflight/upstream; không giữ transaction SQL khi gọi HTTP.
- PATCH cùng key theo contract hiện tại: request commit sau thay toàn bộ key, **chưa có CAS/If-Match**. FE nên tránh gửi hai lần sửa đồng thời; các key không gửi không bị ghi đè.
- User /status chỉ kiểm tra **tạo nháp**, đúng yêu cầu nhóm. Đọc/sửa/xóa dùng JWT còn hạn + owner; không gọi /status lần nữa, không hứa khóa tài khoản thu hồi ngay mọi lần đọc/sửa với JWT cũ. Nếu nhóm muốn chặn ngay mọi thao tác, cần chốt mở rộng guard với User, không tự suy từ can_create_dossier.
- Kiểm tra Document lúc lưu không phải transaction liên service: file có thể bị xóa sau đó. C7/C8 phải kiểm tra lại lúc sử dụng; đính kèm không có nghĩa hợp lệ pháp lý.

## Những phần chưa có

Gợi ý CCCD [C6](README_IDENTITY_SUGGESTIONS.md) và preview/xuất mẫu văn bản [C7](README_GENERATIONS.md) đã triển khai. Checklist/refresh, renderer file gốc/import mẫu riêng C8, Notification C9 vẫn chưa làm. Final nháp gắn procedure bị chặn422 khi checklist chưa chốt, không trả checklist giả. CRUD/adapter đã test với PostgreSQL tạm và HTTP mock, chưa e2e User/Procedure thật. Xem tiến độ C1–C9 trước khi bàn giao.

Kết quả 10/10/2026: 66 test C5 pass (56 offline/API/adapter +10 PostgreSQL thật trong container tạm), tổng hồi quy C1–C5 360 pass; một warning deprecation TestClient. OpenAPI0.5.0/Compose validate, Docker build/smoke non-root/read-only/network-none pass. Supabase verify/readiness chỉ đọc pass. Database test tạm được dọn, service nhóm không bị restart.
