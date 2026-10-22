# TỔNG HỢP RABBITMQ TRONG LEGAL ASSISTANT AI

> **Cập nhật:** 19/10/2026 (sửa lỗi bảo mật và ổn định, gỡ các worker giả lập)
> **Broker:** `rabbitmq-core-events` (AMQP `5672`, Management UI `15672`, chỉ bind `127.0.0.1`)
> **Code dùng chung:** `libs/common_auth/common_auth/events.py` (publisher/consumer) và `revocation.py` (thu hồi token)

---

## 1. Các event đang chạy thật

Tất cả event đi qua topic exchange `legal.events.topic`. Mỗi event có dạng
`{"event_id", "event_type", "timestamp", "payload"}`.

| Routing key | Ai phát | Phát ở đâu | Ai nhận |
| :--- | :--- | :--- | :--- |
| `user.logged_out` | user-service | `app/api/auth.py` (logout) qua `app/core/rabbitmq.py` | Mọi service dùng `common_auth` (thu hồi token theo `jti`); `logout_worker` (audit log) |
| `user.blocked` | user-service | `app/api/admin_users.py` (lock) qua `app/core/rabbitmq_admin.py` | Mọi service dùng `common_auth` (thu hồi mọi token phát hành trước lúc khoá); notification-service |
| `procedure.application.submitted` | procedure-service | `app/api/procedures.py` (submit) qua `BackgroundTasks` | notification-service |
| `document.ocr.completed`, `document.processing.failed` | document-service | `app/worker.py` (worker OCR/ClamAV thật, sau khi DB commit) | notification-service |
| `contract.generated`, `contract.generation.failed` | contract-service | `app/services/generation_worker.py` (chỉ job `final`) | notification-service |
| `lawyer.approved` | lawyer-service | `app/core/rabbitmq_lawyer.py` (chưa có luồng duyệt nào gọi) | notification-service (chỉ thông báo) |

### Queue

| Queue | Loại | Consumer |
| :--- | :--- | :--- |
| `q.notification.events` | Bền vững, có DLX, `x-max-length` 100 000 | `notification-worker` (bind `document.#`, `contract.#`, `procedure.#`, `lawyer.#`, `user.blocked`) |
| `q.audit.user_logout` | Bền vững, có DLX | `py -m app.services.logout_worker` (user-service, tuỳ chọn) |
| Queue tạm (server đặt tên, `exclusive`) | Mỗi process API một queue | `common_auth.revocation` (nghe `user.logged_out`, `user.blocked`) |
| `q.dead_letter` | Bền vững, `x-max-length` 10 000 | Không ai tiêu thụ: nơi xem các message xử lý lỗi |

---

## 2. Thu hồi token (logout và khoá tài khoản)

1. Logout: user-service thu hồi refresh token trong DB, đưa `jti` vào danh sách thu hồi của chính nó, rồi phát `user.logged_out`.
2. Khoá tài khoản: user-service phát `user.blocked`.
3. Mỗi process dùng `common_auth` (agent, user, lawyer, procedure, rag, contract, document, location, consultation, notification) tự chạy một thread nghe hai event trên. `decode_access_token` từ chối token có `jti` đã thu hồi, hoặc token của user bị khoá có `iat` ≤ lúc khoá. Đăng nhập lại sau khi mở khoá vẫn dùng được.
4. User Service tắt kiểm tra khoá (`revocation.CHECK_BLOCKED = False`) vì nó tự đọc trạng thái khoá trong DB và trả 403 kèm lý do.

**Giới hạn:** danh sách nằm trong RAM. Process khởi động lại hoặc mất kết nối broker thì bỏ lỡ event trong lúc đó, nên token cũ còn dùng được tối đa 15 phút (`ACCESS_TOKEN_TTL`). Cần chặt hơn thì chuyển kho sang Redis.

---

## 3. Chạy

```powershell
# Docker: broker + notification-worker chạy cùng docker compose up
docker compose up -d

# Worker audit đăng xuất (tuỳ chọn, chạy local):
cd services/user-service
py -m app.services.logout_worker
```

Worker OCR thật (`docker-compose.document-worker.yml`) và worker render hợp đồng thật (`--profile contract-worker`) tự phát event hoàn tất.

---

## 4. Bảo mật và khả năng chống sập

1. **Không có mật khẩu mặc định.** Code chỉ đọc `RABBITMQ_URL`. Docker Compose bắt buộc có `RABBITMQ_USER`/`RABBITMQ_PASSWORD` trong `.env` (thiếu là báo lỗi ngay). Log luôn che phần `user:password` của URL.
2. **Không consumer nào đổi quyền dựa trên event.** Ai có quyền ghi lên broker đều phát được event, nên đổi role phải đi qua API admin. (Consumer `lawyer_approval_consumer` cũ đã bị gỡ vì cho phép leo thang đặc quyền.)
3. **Không mất message lỗi.** Handler ném lỗi thì message bị reject sang `q.dead_letter`, không bị ack âm thầm. Notification lưu theo `event_id`, nên broker giao lại cùng event cũng không tạo thông báo trùng.
4. **Publisher không làm chậm API.** Kết nối được mở khi cần, có timeout 3 giây, và luôn đóng kết nối cũ khi lỗi (không rò rỉ kết nối). Sau một lần lỗi, publisher nghỉ 10 giây không thử lại, nên request không phải chờ timeout khi broker sập. Broker sập thì API vẫn trả kết quả bình thường.
5. **Broker không phình vô hạn.** Mọi queue bền vững có `x-max-length`; dữ liệu broker nằm trong volume `rabbitmq-data` với `hostname` cố định.
6. **Payload tối thiểu.** Event chỉ mang id, không mang nội dung tài liệu hay dữ liệu hồ sơ cá nhân. Riêng `user.logged_out` có `client_ip`/`user_agent` để phục vụ audit.

### Giới hạn còn lại
- Event hoàn tất của document và contract được phát **sau** khi DB commit. Worker chết đúng giữa hai bước thì mất thông báo (job vẫn đúng). Cần chắc chắn thì phát từ bảng outbox có sẵn (`document_notification_outbox`, `contract_notification_outbox`).
- Tất cả service vẫn dùng chung một tài khoản broker. Nên tách user theo service và giới hạn quyền ghi (`rabbitmqctl set_permissions`).

---

## 5. Đã gỡ (giả lập, không có chỗ gửi task, hoặc không an toàn)

`document_rabbitmq_worker.py`, `contract_render_worker.py`, `dossier_worker.py`, `rag_indexing_worker.py`, `chat_moderation_worker.py`, các module `rabbitmq_document/contract/rag/consultation.py`, `lawyer_approval_consumer.py`, và các queue `q.document.processing`, `q.contract.rendering`, `q.procedure.dossier_generator`, `q.rag.indexing`, `q.consultation.moderation`, `q.auth.token_blacklist`, `q.consultation.user_cleanup`, `q.notification.user_cleanup`, `q.auth.lawyer_approval`, cùng fanout `legal.chat.fanout`.
