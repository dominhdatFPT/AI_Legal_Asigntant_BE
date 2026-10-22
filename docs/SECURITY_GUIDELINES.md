# QUY CHUẨN AN TOÀN THÔNG TIN & BẢO MẬT MÃ NGUỒN (SECURITY GUIDELINES)
**Dự án**: Trợ Lý Pháp Lý AI (Legal Assistant AI)  
**Tài liệu áp dụng cho**: Toàn bộ Backend Microservices & AI Pipeline  
**Phiên bản**: 1.0 (Ban hành sau Code Review & Remediation chiến dịch Procedure Service)

---

## 1. MỤC TIÊU & NGUYÊN TẮC CỐT LÕI

Tài liệu này thiết lập các chuẩn mực an ninh bắt buộc phải tuân thủ nghiêm ngặt trong quá trình phát triển, kiểm thử, đánh giá mã nguồn (code review) và triển khai hệ thống Backend. Bất kỳ mã nguồn nào vi phạm các nguyên tắc dưới đây đều **KHÔNG ĐƯỢC PHÉP** merge vào nhánh chính (`dev`, `staging`, `main`).

---

## 2. TÁM NGUYÊN TẮC BẢO MẬT TỐI THƯỢNG

### NGUYÊN TẮC 1: QUẢN LÝ BÍ MẬT & BIẾN MÔI TRƯỜNG (SECRETS MANAGEMENT)
* **Tuyệt đối KHÔNG hardcode**: Không lưu trữ mật khẩu DB, JWT Secret/Private Key, API Key (Gemini, Supabase, RabbitMQ...) trong mã nguồn Python, Dockerfile hoặc SQL script.
* **Quy chuẩn `.env`**:
  * Mọi repository service phải có file `.env.example` với tên biến đầy đủ nhưng giá trị rỗng hoặc giá trị mẫu an toàn.
  * File `.env` chứa bí mật thật phải nằm trong `.gitignore`.
  * Khi service load dotenv, phải ưu tiên file `.env` nội bộ của service trước khi kế thừa từ thư mục cha (`BE/.env`), tránh việc biến rỗng từ cha ghi đè cấu hình con.
* **Cơ chế tải cấu hình tập trung**: Sử dụng Pydantic Settings (`app/core/config.py`) với typing rõ ràng, có giá trị mặc định an toàn cho môi trường production.

### NGUYÊN TẮC 2: XÁC THỰC & CHẶN BACKDOOR (AUTHENTICATION INTEGRITY)
* **Chống Dev Backdoor trên Production**:
  * Header giả lập danh tính (ví dụ `X-User-Id`) **TUYỆT ĐỐI BỊ CẤM** trên môi trường `production`.
  * Chỉ cho phép fallback sang `X-User-Id` khi thỏa mãn đồng thời cả 2 điều kiện:
    1. `ENVIRONMENT != "production"`
    2. `ALLOW_DEV_AUTH == "true"` (phải bật cờ tường minh).
  * Khi kích hoạt fallback này, hệ thống bắt buộc phải ghi log `SECURITY WARNING`.
* **Xác thực JWT chuẩn qua `common_auth`**:
  * Mọi endpoint cần bảo vệ phải sử dụng dependency `get_current_user` từ thư viện `common_auth`.
  * Không sử dụng đường dẫn cứng mang tính cá nhân máy lập trình viên (ví dụ `D:/OJT_FPT/...`). Luôn định vị key động dựa trên biến môi trường `JWT_PUBLIC_KEY` hoặc `JWT_PUBLIC_KEY_PATH`.

### NGUYÊN TẮC 3: KIỂM SOÁT QUYỀN TRUY CẬP ĐỐI TƯỢNG (CHỐNG BOLA / IDOR)
* **Không tin tưởng `user_id` từ phía Client**:
  * Trong payload tạo/sửa hồ sơ, nếu client gửi kèm `user_id`, backend không được dùng giá trị đó để gán quyền.
  * Phải luôn trích xuất `user_id` từ token của người dùng đang đăng nhập: `user_id = current_user.id`.
* **Kiểm tra quyền sở hữu chặt chẽ 2 lớp (Double-layer Authorization)**:
  * **Lớp Service/API**: Trước khi trả dữ liệu chi tiết (`GET`), chỉnh sửa (`PUT`), nộp (`POST /submit`), hoặc xóa (`DELETE`), phải kiểm tra `record.user_id == current_user.id`. Nếu vi phạm, trả về `403 Forbidden` (`ApplicationPermissionError`), không thực thi logic tiếp theo.
  * **Lớp Repository**: Mọi câu lệnh SQL UPDATE/DELETE phải đính kèm điều kiện `AND user_id = :user_id`.

### NGUYÊN TẮC 4: BẢO MẬT CƠ SỞ DỮ LIỆU & RLS SUPABASE
* **Kích hoạt Row Level Security (RLS)**:
  * 100% các bảng chứa dữ liệu người dùng (`land_procedure_records`, `business_procedure_records`...) phải được bật `ALTER TABLE ... ENABLE ROW LEVEL SECURITY;`.
  * Policy phải sử dụng `auth.uid() = user_id`, tuyệt đối **KHÔNG** dùng `auth.role() = 'authenticated'` chung chung (vì sẽ cho phép người dùng A đọc/sửa dữ liệu của người dùng B).
* **Phân quyền Role an toàn**:
  * Thu hồi quyền ghi/xóa trực tiếp từ role ẩn danh: `REVOKE INSERT, UPDATE, DELETE ON ... FROM anon;`.
  * Tránh dùng `FORCE ROW LEVEL SECURITY` trên các role kết nối trực tiếp nội bộ (như `postgres` qua direct pooler) để không gây nghẽn kết nối.
* **Tính bất đẳng trong Migrations (Idempotent Scripts)**:
  * Mọi migration và seed script phải có khả năng chạy lại nhiều lần mà không sinh lỗi trùng lặp (`ON CONFLICT (col1, col2) DO NOTHING`).
  * Index chức năng trên PostgreSQL phải sử dụng hàm `IMMUTABLE` (như wrapper `f_unaccent(text)`).

### NGUYÊN TẮC 5: TOÀN VẸN TRẠNG THÁI & LOGIC NGHIỆP VỤ (STATE INTEGRITY)
* **Bảo vệ hồ sơ đã nộp**:
  * Hồ sơ ở trạng thái `submitted` hoặc trạng thái hoàn thành **KHÔNG ĐƯỢC PHÉP** chỉnh sửa hay xóa bởi người dùng (`ApplicationLockedError`).
  * Không cho phép người dùng lách luật bằng cách gửi `status: "submitted"` trong API cập nhật nháp `PUT /applications/...`. Trạng thái `submitted` chỉ được chuyển thông qua endpoint hợp lệ `/submit` sau khi đã vượt qua bước kiểm tra hợp lệ dữ liệu biểu mẫu (`ApplicationValidationError`).
* **Kiểm soát Race Condition**:
  * Các tài nguyên chia sẻ (như kết nối Database Engine) phải được đồng bộ qua khóa `threading.Lock()` (Double-checked locking).

### NGUYÊN TẮC 6: BẢO MẬT MẠNG, CORS & REQUEST TRACING
* **Cấu hình CORS an toàn**:
  * Tuyệt đối không cấu hình `allow_origins=["*"]` cùng lúc với `allow_credentials=True`. Điều này vi phạm đặc tả CORS của W3C và mở ra lỗ hổng bảo mật nghiêm trọng.
  * Luôn sử dụng danh sách whitelist cụ thể các domain hợp lệ từ cấu hình `settings.CORS_ORIGINS`.
* **Request Tracing (Khả năng giám sát & điều tra)**:
  * Mọi request đến hệ thống phải được gán một `X-Request-ID` (lấy từ header client gửi lên hoặc middleware tự động tạo mới UUIDv4).
  * Header `X-Request-ID` phải được trả về trong response header để hỗ trợ trace log khi có sự cố.

### NGUYÊN TẮC 7: TỐI ƯU HIỆU NĂNG & CHỐNG TẮC NGHẼN (PERFORMANCE HARDENING)
* **Triệt tiêu lỗi N+1 Query**:
  * Tuyệt đối không thực hiện truy vấn DB bên trong vòng lặp `for`.
  * Sử dụng kỹ thuật Batch Query (`WHERE procedure_id = ANY(:p_ids)`) để gộp các truy vấn con, giảm tải cho Database Pooler từ hàng trăm query xuống còn vài query cố định.
* **Circuit Breaker chuẩn mực**:
  * Chỉ ngắt mạch (trip circuit breaker) khi gặp lỗi hạ tầng mạng (`OperationalError`, Connection Refused, Timeout).
  * Không ngắt mạch khi gặp lỗi cú pháp dữ liệu nghiệp vụ (`DBAPIError`, DataError, IntegrityError).
* **Connection Pooling**:
  * Sử dụng HTTP Session tái sử dụng (`requests.Session()` với `HTTPAdapter`) cho các cuộc gọi REST nội bộ/Supabase để chống cạn kiệt socket cổng (Socket Exhaustion).

### NGUYÊN TẮC 8: ĐÓNG GÓI & MÔI TRƯỜNG THỰC THI (CONTAINER SECURITY)
* **Non-Root User**:
  * Dockerfile bắt buộc phải tạo và chuyển sang user không đặc quyền (`USER appuser`) trước khi chạy ứng dụng.
  * Tuyệt đối không chạy container dưới quyền `root`.
* **Healthcheck & Port**:
  * Dockerfile phải định nghĩa chỉ thị `HEALTHCHECK` kiểm tra định kỳ trạng thái endpoint `/health`.
  * Hỗ trợ cấu hình port động qua biến môi trường (ví dụ `PROCEDURE_SERVICE_PORT`).

---

## 3. CHECKLIST KIỂM TRA TRƯỚC KHI COMMIT (PRE-COMMIT SECURITY CHECKLIST)

Trước khi gửi pull request hoặc thông báo hoàn thành task, kỹ sư/AI agent phải tự kiểm tra danh sách sau:

- [ ] 1. Đã quét mã nguồn và chắc chắn **KHÔNG** còn mật khẩu, private key hay connection string hardcode?
- [ ] 2. File `.env` chứa secret đã được đưa vào `.gitignore` và `.env.example` đã được cập nhật?
- [ ] 3. Đã xác nhận cơ chế dev auth `X-User-Id` chỉ hoạt động khi có `ALLOW_DEV_AUTH=true` và bị chặn 100% trên production?
- [ ] 4. Mọi API có thao tác đọc/ghi dữ liệu người dùng đều đã xác thực và kiểm tra BOLA (`owner_id == current_user.id`)?
- [ ] 5. CORS không chứa wildcard `*` kèm credentials?
- [ ] 6. Không có câu lệnh SQL truy vấn lặp trong vòng lặp (N+1 query)?
- [ ] 7. Các bảng dữ liệu người dùng đều có RLS policy chuẩn?
- [ ] 8. Dockerfile chạy với user non-root và có HEALTHCHECK?
- [ ] 9. Toàn bộ test suite tự động (`pytest`) chạy đạt 100% PASS?

---
*Quy chuẩn này có hiệu lực ngay lập tức và áp dụng bắt buộc cho toàn bộ các service của dự án.*
