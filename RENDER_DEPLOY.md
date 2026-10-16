# Deploy backend lên Render

Render không chạy được `docker-compose.yml`, nên mỗi service là một Web Service riêng, FE chỉ gọi vào **gateway**.
Toàn bộ cấu hình nằm trong `render.yaml` (Blueprint), tạo một lần là có đủ gateway + 9 service.

## File thêm cho Render (bản local không đổi)

| File | Dùng cho |
|---|---|
| `render.yaml` | Blueprint: khai báo mọi service, env, health check |
| `services/<tên>/Dockerfile.render` | Build từ **gốc repo** (thay cho `COPY --from=libs` mà Render không hỗ trợ), chạy theo cổng `$PORT` |
| `gateway/Dockerfile.render` + `gateway/render/nginx.conf.template` | Gateway nginx proxy tới `https://<service>.onrender.com` |

## Các bước

1. **Xóa service cũ** `AI_Legal_Asigntant_BE` trên Render (đang fail, cấu hình sai Dockerfile).
2. Commit + push các file trên lên nhánh `main` của repo GitHub.
3. Render Dashboard → **New → Blueprint** → chọn repo → Render đọc `render.yaml`.
4. Điền các biến Render hỏi (`sync: false`), xem bảng bên dưới. Biến nào chưa có thì để trống, thêm sau.
5. **Thêm secret file JWT** (không khai báo được trong `render.yaml`):
   - Environment Groups → `ailegal-shared` → Secret Files → thêm `jwt_public.pem` (nội dung public key).
   - `ailegal-user-service` → Environment → Secret Files → thêm `jwt_private.pem` (nội dung private key). **Chỉ** user-service có private key.
   - Render đặt file ở `/etc/secrets/<tên file>`, khớp `JWT_PUBLIC_KEY_PATH` / `JWT_PRIVATE_KEY_PATH`.
   - Cách khác nếu không dùng secret file: đặt biến `JWT_PUBLIC_KEY` (và `JWT_PRIVATE_KEY` cho user-service) với nội dung key, xuống dòng viết `\n`. Code ưu tiên biến này trước đường dẫn file.
6. **Kiểm tra URL service**: mở từng service, xem URL thật. Nếu khác `https://ailegal-<tên>.onrender.com` (do tên bị trùng), sửa biến `*_SERVICE_URL` trong group `ailegal-shared`. Không để `/` ở cuối.
7. **FE**: đặt base URL API = URL của `ailegal-gateway` (vd. `https://ailegal-gateway.onrender.com`). Gọi kèm cookie: `credentials: "include"` / `withCredentials: true`.
8. **DB Supabase**: chạy đủ file `services/<tên>/database/NNN_*.sql` trong SQL Editor của từng DB, rồi chạy `check_*_db.sql` để kiểm tra. Ví dụ cột `refresh_tokens.revoked_reason` của user-service nằm trong `007_lawyer_role.sql`.
9. Sau khi đổi env hoặc secret file: Manual Deploy → **Deploy latest commit** (hoặc Restart) để service nhận giá trị mới.

## Biến môi trường

**Group `ailegal-shared`** (gắn vào mọi service, đã khai báo sẵn): `INTERNAL_TOKEN` (Render tự sinh), `JWT_PUBLIC_KEY_PATH`, toàn bộ `*_SERVICE_URL`.

**Biến từng service cần điền** (còn lại đã có giá trị mặc định trong `render.yaml`):

| Service | Cần điền |
|---|---|
| gateway | `FRONTEND_ORIGIN` = origin FE, vd. `https://ten-app.vercel.app` |
| user-service | `USER_DATABASE_URL`, `FIREBASE_PROJECT_ID` (+ secret file `jwt_private.pem`) |
| lawyer-service | `LAWYER_DATABASE_URL` |
| consultation-service | `CONSULTATION_DATABASE_URL` |
| agent-service | `AGENT_DATABASE_URL` (MVP có thể trống), `GEMINI_API_KEY`, `GROQ_API_KEY` (nếu dùng) |
| rag-service | `RAG_DATABASE_URL`, `GEMINI_API_KEY` |
| procedure-service | `PROCEDURE_DATABASE_URL` |
| location-service | `LOCATION_DATABASE_URL` |
| contract-service | `CONTRACT_DATABASE_URL`, `CONTRACT_SUPABASE_URL`, `CONTRACT_SUPABASE_SERVICE_ROLE_KEY`, `CONTRACT_ALLOWED_ORIGINS` (= origin FE) |
| notification-service | `NOTIFICATION_DATABASE_URL` |

**Chuỗi `*_DATABASE_URL`**: lấy ở Supabase → Connect → **Session pooler** (IPv4). Không dùng Direct connection (chỉ IPv6, Render không kết nối được). Dán đúng dạng đang chạy được ở `.env` local (user-service tự đổi `postgresql://` sang `postgresql+psycopg://`).

**Cookie** (user-service, đã đặt sẵn): `AUTH_COOKIE_SECURE=true`, `AUTH_COOKIE_SAMESITE=none` vì FE và gateway khác domain.

## Giới hạn của gói free cần biết

- **Ngủ sau 15 phút không có request**, lần gọi đầu mất khoảng 50 giây. Gateway đã để timeout 75–90s, nhưng service gọi service khác dùng timeout 5s → nếu service bị gọi đang ngủ sẽ ra 504. Trước khi demo, mở `/api/<x>/health` qua gateway cho từng service để đánh thức hết.
- **750 giờ chạy/tháng cho cả workspace**, chia chung cho mọi service free. 10 service chạy liên tục sẽ vượt; service ngủ thì không tính giờ.
- **document-service** (torch + VietOCR + Tesseract) cần ≥ 2 GB RAM, gói free 512 MB không chạy được. Khối này đang comment trong `render.yaml`, muốn bật thì cần gói trả phí. Khi tắt, các chức năng upload/OCR CCCD và hồ sơ luật sư (gọi Document) sẽ lỗi.
- **contract-service** có LibreOffice, 512 MB có thể thiếu khi xuất file lớn.
