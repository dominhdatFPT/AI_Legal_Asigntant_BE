# Thiết kế chức năng Luật sư (Backend)

Trạng thái: **Giai đoạn 2 - đã code + test** (15/10/2026). API trong bảng mục 5 là `x-status: implemented`, trừ `GET /lawyers`, `GET /lawyers/{id}`, `GET /lawyers/me/stats` (planned).
Phạm vi: `lawyer-service` (mới), `user-service` (sửa), `document-service` (thêm OCR thẻ luật sư, file riêng, không đổi API cũ).
Contract: `services/lawyer-service/openapi.yaml`, `services/user-service/openapi.yaml`, `services/document-service/openapi.yaml`.

## 1. Quy tắc đã chốt

| Chủ đề | Quy tắc |
|---|---|
| Lĩnh vực | `land` (Đất đai), `business` (Hộ kinh doanh); một luật sư chọn nhiều lĩnh vực |
| Số thẻ | Không rỗng, 1-50 ký tự. Duy nhất toàn hệ thống trong **hồ sơ chưa ẩn + đơn `pending`**, không phân biệt hoa thường, không tính chính người đó |
| Hồ sơ "đủ" | Đã khai CCCD (User `has_citizen_profile`) + có số thẻ + ≥ 1 lĩnh vực. Chưa đủ thì chỉ dùng chức năng citizen |
| Luật sư | Vẫn dùng mọi chức năng citizen |
| Kết quả duyệt | Chưa có Notification, chỉ xem qua trạng thái đơn |
| Đổi role lên `lawyer` | **Chỉ** qua Lawyer Service (admin tạo / duyệt đơn). `PUT /users/{id}/role` với `role = lawyer` → 409 `LAWYER_ROLE_VIA_LAWYER_SERVICE` |
| Ẩn hồ sơ | Admin hạ `lawyer` → role khác hoặc xóa luật sư: User Service gọi Lawyer `hide` **trước khi lưu**; Lawyer lỗi → 502/503/504, User không đổi gì |
| Ảnh thẻ | Mọi đơn (`new`, `card_change`) bắt buộc ảnh thẻ khi gửi duyệt. Luật sư admin tạo không cần ảnh |
| Thời gian chờ | Bị từ chối → chờ `LAWYER_REAPPLY_COOLDOWN_HOURS` (mặc định 24) mới **gửi** đơn mới (tạo nháp vẫn được). Rút đơn không tính |
| Online | Có cột `is_online` (mặc định `false`), chưa có API bật/tắt (WebSocket cùng Consultation). `GET /lawyers`, `GET /lawyers/{id}` vẫn planned |

## 2. Bảng dữ liệu

### Lawyer DB (`services/lawyer-service/database/001_lawyer_schema.sql`)

| Bảng | Nội dung chính |
|---|---|
| `specializations` | `code` PK, `name`, `sort_order`. Seed `land`, `business` |
| `lawyer_profiles` | Hồ sơ **đang hiệu lực**, 1 dòng/người (`user_id` unique). `source` `admin_created \| self_applied`; `status` `incomplete \| active \| hidden`; `bar_card_number`, `card_document_id`, `card_ocr_result` jsonb, `law_firm`, `years_experience`, `bio`, `is_online`, `last_seen_at`, `hidden_at`, `hidden_reason` |
| `lawyer_profile_specializations` | `(profile_id, specialization_code)`; FK nội bộ, index theo `specialization_code` để lọc |
| `lawyer_applications` | Đơn: `type` `new \| card_change`; `status`; ảnh chụp các trường; `specialization_codes` jsonb; `card_document_id`, `ocr_result` jsonb; `applicant_full_name`; `reject_reason`, `reviewed_by`, `reviewed_at`, `submitted_at`, `withdrawn_at` |

Ràng buộc quan trọng trong SQL (lớp bảo vệ cuối, service vẫn kiểm tra trước để trả lỗi đẹp):
- `lawyer_applications_one_open_key`: mỗi `user_id` chỉ 1 đơn `draft`/`pending`.
- `lawyer_profiles_bar_card_key` (hồ sơ chưa ẩn) và `lawyer_applications_pending_bar_card_key` (đơn pending): unique `upper(bar_card_number)`. Trùng **giữa** hai bảng do service kiểm tra trong cùng transaction.
- CHECK: `active` phải có số thẻ; `hidden` ⇔ có `hidden_at` + `hidden_reason`; `rejected` ⇔ có lý do; `approved/rejected` ⇔ có người duyệt; đơn đã gửi phải có ảnh thẻ; `card_change` không mang `law_firm/bio/...`.
- RLS bật, không policy, thu hồi quyền `anon`/`authenticated` (chỉ backend đọc/ghi).

**Vì sao lĩnh vực của hồ sơ dùng bảng nối, còn của đơn dùng jsonb:** hồ sơ cần lọc `GET /lawyers?specialization=land` có index và FK tới danh mục; test chạy SQLite in-memory nên không dùng `text[]` (SQLite không có mảng). Đơn chỉ là ảnh chụp để admin xem, không lọc theo lĩnh vực, nên jsonb là đủ và đơn giản hơn.

**Vì sao tách 2 bảng hồ sơ / đơn:** đơn `card_change` đang chờ thì thông tin cũ vẫn hiệu lực; duyệt xong mới ghi đè. Lịch sử đơn (bị từ chối, đã rút) giữ nguyên để tính thời gian chờ và để admin tra cứu.

### User DB
- `007_lawyer_role.sql`: thêm `refresh_tokens.revoked_reason` (`role_changed`); đổi `created_as_lawyer = true` → `role = lawyer` (thu hồi token với lý do `role_changed`), rồi xóa cột `created_as_lawyer`.
- `008_export_lawyer_profiles.sql`: chỉ đọc, sinh **một câu INSERT** tạo hồ sơ `incomplete`/`admin_created` cho mọi tài khoản `lawyer` (đã xóa mềm → `hidden`/`deleted`), copy sang Lawyer DB. Không đọc chéo database.

### Document DB
- `006_lawyer_card_ocr.sql`: bảng `document_lawyer_card_ocr` (1 ảnh/dòng): owner, idempotency key, storage key `safe/<owner>/lawyer-card/<id>.jpg`, sha256, size, engine, raw jsonb, fields jsonb, warnings, error_code. RLS + chỉ `service_role`.

## 3. Trạng thái

### Hồ sơ luật sư (`lawyer_profiles.status`)

```text
                  admin tạo tài khoản                     duyệt đơn new
   (không có) ─────────────────────────► incomplete    (không có / hidden) ───────► active
                                             │
                    đủ: CCCD + số thẻ + ≥1 lĩnh vực (kiểm tra lười: PATCH /me, GET /me, internal status)
                                             ▼
                                           active
   incomplete / active ── User hạ role / xóa tài khoản (internal hide) ──► hidden
   hidden ── xin lại (đơn new) được duyệt ──► active (dùng lại dòng cũ, ghi đè toàn bộ)
```

Không có đường `active → incomplete`: không xóa được số thẻ, lĩnh vực phải ≥ 1, CCCD không xóa được ở User Service.

### Đơn (`lawyer_applications.status`)

```text
   draft ──submit──► pending ──approve──► approved
     │                  ├──reject (bắt buộc lý do)──► rejected
     │                  └──withdraw──► withdrawn
     └──withdraw (bỏ nháp)──► withdrawn
   draft/pending ── hồ sơ bị ẩn (internal hide) ──► withdrawn
```

- Sửa được `draft` và `pending` (sửa `pending` vẫn phải đủ điều kiện như lúc gửi).
- Tạo đơn `new`: vai trò token `citizen`, không có hồ sơ hoặc hồ sơ `hidden`.
- Tạo đơn `card_change`: vai trò token `lawyer`, hồ sơ chưa ẩn và đã có số thẻ; chỉ mang `bar_card_number` và/hoặc ảnh mới.
- Admin không bao giờ thấy `draft`.

## 4. Luồng gọi giữa các service

Mọi lời gọi nội bộ: `X-Internal-Token` + `X-Request-ID`, timeout 5 giây (OCR 60 giây), không retry thao tác ghi, chỉ retry GET tối đa 2 lần. Lỗi kết nối/timeout/5xx → 503/504/502 `<SERVICE>_*`; 4xx của service phụ là kết quả nghiệp vụ.

### 4.1 Admin tạo luật sư (cách 1)

```text
FE(admin) ─POST /lawyers/accounts─► Lawyer
  Lawyer ─POST /internal/v1/users {email,password,full_name,role:lawyer,created_by:<admin sub>}─► User
     409 EMAIL_IN_USE / EMAIL_DELETED / PHONE_IN_USE → trả nguyên cho FE
  Lawyer: INSERT lawyer_profiles (incomplete, admin_created)
     lỗi DB → Lawyer ─DELETE /internal/v1/users/{id}─► User (xóa cứng) → 503 LAWYER_DB_UNAVAILABLE
              (xóa cứng lỗi: log ERROR kèm user_id để xử lý tay, vẫn trả 503)
  → 201 {user, profile}
Luật sư đăng nhập (token role lawyer) ─PATCH /lawyers/me {bar_card_number, specialization_codes, ...}─► Lawyer
  lần đầu điền số thẻ: lưu ngay; đủ (hỏi User has_citizen_profile) → active
```

User Service chỉ cho xóa cứng tài khoản do admin tạo, chưa từng đăng nhập (không có refresh token) và chưa khai CCCD.

### 4.2 Người dùng xin làm luật sư (cách 2)

```text
FE ─POST /documents/lawyer-card-ocr (multipart file, Idempotency-Key)─► Document
   Document: chuẩn hóa ảnh (bỏ EXIF, JPEG mới) → Storage safe/... → LawyerCardOcrProvider (Tesseract local)
   → 201 {card_id, ocr_status completed|failed, fields, error_code, error_message}
     (OCR lỗi vẫn 201 + failed: FE báo "ảnh mờ, vui lòng tự nhập", người dùng nhập tay)
FE ─POST /lawyers/me/applications {type:new, ..., card_document_id:card_id}─► Lawyer   (tạo draft)
   Lawyer ─GET /internal/v1/documents/lawyer-cards/{card_id}─► Document
     owner_user_id ≠ sub → 422 (card_document_id);  lưu ocr_result vào đơn
FE ─POST /lawyers/me/applications/{id}/submit─► Lawyer
   Lawyer ─GET /internal/v1/users/{id}/status─► User   (has_citizen_profile, is_locked)
   Lawyer ─GET /internal/v1/users/{id}/citizen-profile─► User   (applicant_full_name)
   kiểm tra đủ trường, số thẻ không trùng, thời gian chờ → pending
Admin ─GET /lawyers/applications/{id}─► Lawyer
   Lawyer ─POST /internal/v1/documents/lawyer-cards/{card_id}/view-url─► Document   (link 300 giây)
   Lawyer ─GET /internal/v1/users/{id}/citizen-profile─► User
   → đơn + OCR + link ảnh + CCCD + cờ is_name_mismatch / is_card_number_mismatch
Admin ─POST /lawyers/applications/{id}/approve─► Lawyer
   Lawyer ─PUT /internal/v1/users/{id}/role {role:lawyer}─► User
       User: role = lawyer, thu hồi refresh token (revoked_reason = role_changed)
       User 404/409 → 409 APPLICANT_UNAVAILABLE;  5xx/timeout → 502/503/504, đơn vẫn pending
   Lawyer: tạo / dùng lại hồ sơ (self_applied, active), ghi đè trường từ đơn, đơn approved
   (lưu DB lỗi sau khi đã đổi role: admin bấm duyệt lại, đổi role lặp lại an toàn)
Người dùng refresh token ─POST /users/auth/refresh─► User → 401 ROLE_CHANGED_RELOGIN
   "Tài khoản của bạn đã được cập nhật quyền, vui lòng đăng nhập lại" → FE về trang đăng nhập
```

Cờ không khớp: so không phân biệt hoa thường, bỏ dấu tiếng Việt, gộp khoảng trắng; `null` khi thiếu dữ liệu để so (OCR lỗi, chưa khai CCCD).

### 4.3 Đổi số thẻ / ảnh thẻ sau khi là luật sư

Giống 4.2 nhưng `type = card_change`, không gọi User Service khi duyệt. Duyệt → ghi đè `bar_card_number` (nếu có), `card_document_id`, `card_ocr_result`. Từ chối → hồ sơ giữ nguyên. Sửa `law_firm`, `bio`, `years_experience`, lĩnh vực: `PATCH /lawyers/me`, lưu ngay.

### 4.4 Admin hạ role / xóa luật sư

```text
Admin ─PUT /users/{id}/role {role:citizen}  hoặc  DELETE /users/{id}─► User
   user hiện là lawyer → User ─POST /internal/v1/lawyers/{id}/hide {reason}─► Lawyer   (TRƯỚC khi lưu)
       lỗi → 502/503/504 LAWYER_*, không đổi gì
   Lawyer: hồ sơ hidden, is_online = false, đơn draft/pending → withdrawn (idempotent)
   User: lưu role / xóa mềm, thu hồi refresh token
   User ─close_lawyer_consultations()─► Consultation   (TODO: hiện chỉ ghi log)
```

### 4.5 Consultation Service (sau này)

`GET /internal/v1/lawyers/{user_id}/status` → `has_profile`, `profile_status`, `is_hidden`, `is_complete`, `missing_requirements`, `can_practice`. Khóa tài khoản vẫn kiểm tra ở User Service.

## 5. API mới

| Service | API | Ai gọi |
|---|---|---|
| Lawyer | `GET /lawyers/specializations` | Công khai |
| Lawyer | `GET`, `PATCH /lawyers/me` | Đăng nhập (`PATCH`: role `lawyer`) |
| Lawyer | `GET`, `POST /lawyers/me/applications`; `GET`, `PATCH /lawyers/me/applications/{id}`; `POST .../{id}/submit`, `POST .../{id}/withdraw` | Đăng nhập |
| Lawyer | `POST /lawyers/accounts` | admin |
| Lawyer | `GET /lawyers/applications`, `GET /lawyers/applications/summary`, `GET /lawyers/applications/{id}`, `POST .../{id}/approve`, `POST .../{id}/reject` | admin |
| Lawyer | `GET /internal/v1/lawyers/{user_id}/status`, `POST /internal/v1/lawyers/{user_id}/hide` | Nội bộ |
| User | `POST /internal/v1/users`, `DELETE /internal/v1/users/{id}`, `PUT /internal/v1/users/{id}/role` | Nội bộ (Lawyer) |
| User (sửa) | `POST /users` bỏ `account_type = lawyer`; `AdminUser` bỏ `created_as_lawyer`; `PUT /users/{id}/role` chặn lên `lawyer` + gọi hide; `DELETE /users/{id}` gọi hide; refresh trả `ROLE_CHANGED_RELOGIN` | |
| Document | `POST /documents/lawyer-card-ocr`, `GET /documents/lawyer-card-ocr/{card_id}` | Đăng nhập (chủ ảnh) |
| Document | `GET /internal/v1/documents/lawyer-cards/{card_id}`, `POST /internal/v1/documents/lawyer-cards/{card_id}/view-url` | Nội bộ (Lawyer) |

Đổi so với `openapi.yaml` cũ của Lawyer: `POST /lawyers` + `PUT /lawyers/me` thay bằng đơn (`/lawyers/me/applications`) + `PATCH /lawyers/me`; duyệt/từ chối chuyển từ `/lawyers/{lawyer_id}/approve` sang `/lawyers/applications/{application_id}/approve` (duyệt theo đơn, vì một luật sư có nhiều đơn); bỏ `PUT /lawyers/me/status` (online làm bằng WebSocket); bỏ `credentials` nhiều file (chỉ một ảnh thẻ).

## 6. Mã lỗi mới

| HTTP | Mã | Khi nào |
|---|---|---|
| 404 | `LAWYER_NOT_FOUND`, `APPLICATION_NOT_FOUND` | Không có hồ sơ / đơn (đơn người khác cũng 404) |
| 409 | `LAWYER_PROFILE_EXISTS` | Tạo đơn `new` khi đã có hồ sơ chưa ẩn |
| 409 | `OPEN_APPLICATION_EXISTS` | Đã có đơn `draft`/`pending` |
| 409 | `CARD_NOT_SET` | Đơn `card_change` khi hồ sơ chưa có số thẻ |
| 409 | `CARD_CHANGE_REQUIRES_REVIEW` | `PATCH /lawyers/me` đổi số thẻ đã có |
| 409 | `BAR_CARD_NUMBER_TAKEN` | Trùng số thẻ |
| 409 | `APPLICATION_NOT_EDITABLE`, `APPLICATION_NOT_DRAFT`, `APPLICATION_NOT_PENDING` | Sai trạng thái đơn |
| 409 | `CITIZEN_PROFILE_REQUIRED` | Gửi đơn khi chưa khai CCCD |
| 409 | `REAPPLY_COOLDOWN` | Gửi đơn trong thời gian chờ sau khi bị từ chối |
| 409 | `APPLICANT_UNAVAILABLE` | Duyệt khi tài khoản người gửi đã xóa / là admin |
| 403 | `LAWYER_PROFILE_INCOMPLETE` | API chỉ cho luật sư `active` (stats) |
| 503 | `LAWYER_DB_UNAVAILABLE` | Admin tạo luật sư, lưu hồ sơ lỗi (đã xóa cứng tài khoản) |
| 401 | `ROLE_CHANGED_RELOGIN` (User) | Refresh sau khi vai trò đổi |
| 409 | `LAWYER_ROLE_VIA_LAWYER_SERVICE`, `ROLE_CHANGE_NOT_ALLOWED`, `HARD_DELETE_NOT_ALLOWED` (User) | Xem contract User |
| 502/503/504 | `USER_*`, `DOCUMENT_*` (Lawyer); `LAWYER_*` (User) | Service phụ lỗi |

## 7. Cấu hình

| Biến | Service | Ghi chú |
|---|---|---|
| `LAWYER_REAPPLY_COOLDOWN_HOURS` | lawyer | **Mới**, mặc định 24 |
| `LAWYER_DATABASE_URL`, `USER_SERVICE_URL`, `DOCUMENT_SERVICE_URL`, `INTERNAL_TOKEN` | lawyer | Đã có trong `.env.example`; compose cần truyền vào `lawyer-service` |
| `LAWYER_SERVICE_URL` | user | Đã có; compose cần truyền vào `user-service` |
| `INTERNAL_TOKEN` | document | Đã dùng; thêm kiểm tra cho router `/internal/v1/documents/...` |

## 8. Quyết định cần người phụ trách khác xác nhận

- **Document (Hiền):** ảnh thẻ luật sư lưu thẳng vào `safe/` (không qua quarantine + worker quét) vì backend mã hóa lại thành JPEG mới từ pixel (không giữ byte người dùng), giống cách ảnh CCCD được chuẩn hóa. README Document hiện cấm signed URL cho `quarantine/`, nên đây là cách cấp link xem cho admin mà không phá quy tắc đó.
- **Document:** OCR lỗi trả 201 + `ocr_status = failed` (khác identity-ocr trả 422), vì ảnh vẫn dùng được để admin xem và người dùng nhập tay.
- **Consultation:** đóng cuộc tư vấn khi hạ role / xóa vẫn là TODO ở `services/user-service/app/clients/consultation_client.py` (`close_lawyer_consultations`).

## 9. Code và test

| Phần | Code | Test |
|---|---|---|
| Lawyer | `services/lawyer-service/app/` (`api/lawyers.py`, `api/internal.py`, `services/*_service.py`, `services/user_client.py`, `services/document_client.py`, `services/http_client.py`) | `pytest services/lawyer-service/tests -q` (SQLite in-memory, User/Document giả bằng `httpx.MockTransport`, có test response khớp `openapi.yaml`) |
| User | `app/api/internal_users.py`, `app/services/admin_user_service.py`, `app/clients/lawyer_client.py`, `app/services/auth_service.py` (refresh) | `services/user-service/tests/test_lawyer_integration.py`, `test_admin_users.py` |
| Document | `app/api/lawyer_card_ocr.py`, `app/api/internal_lawyer_cards.py`, `app/repositories/lawyer_cards.py`, `app/services/lawyer_card_{images,ocr,parser}.py` | `tests/test_document_lawyer_card.py` (test SQL thật cần `DOCUMENT_TEST_DATABASE_URL`, test Tesseract thật cần cài `tesseract`) |

Đổi engine OCR: viết class mới có `engine` + `read_card(image, *, deadline) -> (fields, raw)` (interface
`LawyerCardOcrProvider` trong `lawyer_card_ocr.py`), rồi thay `LocalLawyerCardOcr()` trong `get_lawyer_cards()`.

## 10. TODO / ngoài phạm vi

- `GET /lawyers`, `GET /lawyers/{id}` (cần User `POST /internal/v1/users/search`, đang planned), `GET /lawyers/me/stats`, bật/tắt online: để sau.
- Notification khi duyệt / từ chối: chưa có service.
- Khôi phục (`restore`) tài khoản luật sư đã xóa: vai trò vẫn `lawyer` nhưng hồ sơ `hidden` → không hành nghề được (`can_practice = false`); admin hạ role rồi người đó xin lại. Chưa tự động hóa.
- Khóa tài khoản luật sư không ẩn hồ sơ; Consultation kiểm tra khóa qua User Service.
