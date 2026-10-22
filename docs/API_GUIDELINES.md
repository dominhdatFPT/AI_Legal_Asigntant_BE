# Quy tắc API và làm việc giữa các service (v2)

Bản quy ước chung để cả nhóm viết và gọi API giống nhau. Backend áp dụng cho service mình phụ trách; Frontend dùng để gọi API và đọc kết quả.

- Phiên bản: **v2 — 02/10/2026** (thay bản v1). Mọi thay đổi file này đi qua Merge Request vào `dev` và cần ít nhất 1 người trong nhóm duyệt.
- Chữ **"hiện có"** mô tả code đang chạy. Những gì không ghi "hiện có" là **quy ước bắt buộc khi làm mới**, chưa chắc đã được triển khai. Xem tình trạng ở [mục 15](#15-tình-trạng-triển-khai).

---

## 0. Các điểm nhóm cần chốt

Những điểm dưới đây ảnh hưởng nhiều service. **Chưa chốt thì chưa code phần liên quan.**

| # | Vấn đề | Đề xuất |
|---|---|---|
| 1 | Chat với luật sư (phòng chờ, box chat riêng, inbox luật sư) thuộc service nào? | Đã chốt: **consultation-service** |
| 2 | Lịch sử chat với AI thuộc service nào? | Đã chốt: **agent-service** (nơi sinh ra câu trả lời), dùng `AGENT_DATABASE_URL` |
| 3 | Luật sư đánh giá câu trả lời AI (đúng/sai/thiếu) thuộc service nào? | **consultation-service** (luật sư chỉ đánh giá trong cuộc tư vấn mình phụ trách) |
| 4 | Code dùng chung (format kết quả, xử lý lỗi, kiểm tra JWT) đặt ở đâu? | Hiện có `libs/common_auth` cho JWT và lỗi xác thực; Docker dùng `additional_contexts`. Format nghiệp vụ dùng chung vẫn cần chốt |
| 5 | Đăng nhập: tự cấp JWT trong user-service hay dùng Supabase Auth? | Hiện user-service xác thực Firebase ID token rồi cấp access token RS256 và refresh token của hệ thống |

---

## 1. Service và quyền sở hữu dữ liệu

| Service | Framework | Cổng nội bộ | Prefix nội bộ | Prefix cho FE | Biến database | Dữ liệu phụ trách |
|---|---|---|---|---|---|---|
| user | FastAPI | 8000 | `/api/v1/users` | `/api/users` | `USER_DATABASE_URL` | Tài khoản, đăng nhập, hồ sơ và vai trò (`citizen`, `lawyer`, `admin`) |
| lawyer | FastAPI | 8000 | `/api/v1/lawyers` | `/api/lawyers` | `LAWYER_DATABASE_URL` | Hồ sơ năng lực luật sư, thống kê, trạng thái online/offline; admin duyệt luật sư |
| procedure | FastAPI | 8000 | `/api/v1/procedures` | `/api/procedures` | `PROCEDURE_DATABASE_URL` | Thủ tục, checklist giấy tờ, lệ phí, căn cứ pháp lý của thủ tục |
| contract | FastAPI | 8000 | `/api/v1/contracts` | `/api/contracts` | `CONTRACT_DATABASE_URL` | Mẫu hợp đồng, phiên bản mẫu, hợp đồng cá nhân |
| document | FastAPI | 8000 | `/api/v1/documents` | `/api/documents` | `DOCUMENT_DATABASE_URL` | Giấy tờ upload, OCR, thông tin file |
| rag | FastAPI | 8000 | `/api/v1/rag` | `/api/rag` | `RAG_DATABASE_URL` | Tài liệu kiến thức, trạng thái index, tìm kiếm, nguồn trích dẫn |
| location | FastAPI | 8000 | `/api/v1/locations` | `/api/locations` | `LOCATION_DATABASE_URL` | Cơ quan tiếp nhận, địa chỉ, tọa độ, tìm nơi nộp |
| agent | FastAPI | 8000 | `/api/v1/agent` | `/api/agent` | `AGENT_DATABASE_URL` | Điều phối các service để trả lời; `agent_runs` và lịch sử chat AI |
| consultation | FastAPI | 8000 | `/api/v1/consultations` | `/api/consultations` | `CONSULTATION_DATABASE_URL` | Yêu cầu tư vấn, box chat citizen – luật sư, report, soft delete và cờ warning |
| notification | FastAPI | 8000 | `/api/v1/notifications` | `/api/notifications` | `NOTIFICATION_DATABASE_URL` | Thông báo trong web, read/unread, mute/suppress và nhận event từ service khác |

Quy tắc:

- Mỗi service có **database, tài khoản kết nối và migration riêng**, đặt trong `services/<tên>-service/database/` và `alembic/`.
- Service chỉ đọc/ghi database của mình. Cần dữ liệu service khác thì **gọi API** của service đó.
- Không tạo khóa ngoại sang database service khác. Ví dụ Contract lưu `owner_user_id` để biết hợp đồng của ai, nhưng không có foreign key sang User.
- ID tham chiếu **không tự chứng minh** dữ liệu tồn tại hay user có quyền. Phải kiểm tra qua API hoặc qua thông tin đăng nhập đã xác thực.
- Dữ liệu thuộc service nào thì service đó sửa. Ví dụ khóa tài khoản phải gọi User Service.
- Xóa dữ liệu được tham chiếu bởi service khác thì dùng **xóa mềm** (`status`, `deleted_at`), vì không có cascade giữa các database.

---

## 2. Địa chỉ và đường dẫn

```text
Frontend  →  http://localhost:8080/api/procedures/search          (qua Gateway)
Gateway   →  http://procedure-service:8000/api/v1/procedures/search
Agent     →  http://procedure-service:8000/api/v1/procedures/search  (gọi thẳng trong mạng Docker)
```

- **Frontend chỉ gọi Gateway** (`http://localhost:8080` khi chạy local). Địa chỉ Backend cấu hình **một chỗ** trong FE (ví dụ `API_BASE_URL`), không rải trong từng màn hình.
- Gateway đổi `/api/<tài-nguyên>/...` thành `/api/v1/<tài-nguyên>/...` (hiện có trong `gateway/nginx.conf`).
- Service gọi nhau bằng URL lấy từ biến môi trường: `PROCEDURE_SERVICE_URL`, `USER_SERVICE_URL`, `DOCUMENT_SERVICE_URL`… Tên như `procedure-service` chỉ dùng được trong mạng Docker, không dùng trong trình duyệt.
- **API chỉ dành cho service khác gọi** đặt dưới `/internal/v1/<tài-nguyên>/...`. Gateway không chuyển tiếp `/internal/...`, nên FE không gọi được (xem [mục 7](#7-đăng-nhập-và-phân-quyền)).
- `.env.example` chứa tên biến và giá trị mẫu, được push lên Git. `.env` chứa giá trị thật, **không push**.

---

## 3. Đặt tên URL và dùng HTTP method

- Tài nguyên dùng **danh từ số nhiều**, chữ thường, nối bằng gạch ngang: `/procedures`, `/contract-templates`.
- Lồng tối đa 2 cấp: `/consultations/{id}/messages`.
- Hành động không phải CRUD dùng `POST /<tài-nguyên>/{id}/<hành-động>`: `POST /consultations/{id}/accept`, `POST /lawyers/{id}/approve`.
- Tìm kiếm phức tạp dùng `POST /<tài-nguyên>/search` (hiện có ở procedure, rag).

| Method | Dùng để | Thành công trả |
|---|---|---|
| GET | Lấy dữ liệu, không thay đổi gì | 200 |
| POST | Tạo mới hoặc xử lý | 201 khi tạo mới, 200 khi chỉ xử lý |
| PATCH | Sửa một phần | 200 |
| PUT | Thay thế toàn bộ | 200 |
| DELETE | Xóa | 204, không có body |

---

## 4. Format kết quả

### Thành công

Một đối tượng:

```json
{
  "data": { "id": "6f1c...", "code": "HKD-001", "name": "Đăng ký thành lập hộ kinh doanh" },
  "error": null,
  "request_id": "req-123"
}
```

Danh sách (có phân trang):

```json
{
  "data": {
    "items": [],
    "pagination": { "page": 1, "page_size": 20, "total": 0, "total_pages": 0 }
  },
  "error": null,
  "request_id": "req-123"
}
```

### Lỗi

```json
{
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Dữ liệu gửi lên không hợp lệ.",
    "details": [
      { "field": "query", "message": "Phải có 1–2000 ký tự, không chỉ chứa khoảng trắng." }
    ]
  },
  "request_id": "req-123"
}
```

- `data`: dữ liệu FE dùng. Danh sách luôn nằm trong `data.items`.
- `error`: `null` khi thành công. Khi lỗi có `code` (cố định, FE dùng để xử lý), `message` (tiếng Việt, hiển thị được cho người dùng) và `details` (tùy chọn, lỗi theo từng trường để FE tô đỏ ô nhập).
- `request_id`: mã của lần gọi. Service cũng trả mã này trong header `X-Request-ID`. Khi báo lỗi cho Backend, gửi kèm mã này.
- **Ngoại lệ:** health check trả dạng đơn giản `{"status":"ok","service":"user-service"}`; status `204` không có body.
- **Không** để lộ stack trace, câu SQL hay tên bảng trong `message`.

---

## 5. Mã lỗi và HTTP status

| HTTP | `error.code` | Khi nào |
|---|---|---|
| 400 | `BAD_REQUEST` | Body không đọc được (JSON hỏng) |
| 401 | `UNAUTHORIZED` | Thiếu token, token sai hoặc hết hạn |
| 403 | `FORBIDDEN` | Đã đăng nhập nhưng không đủ quyền |
| 404 | `NOT_FOUND` hoặc `<TÀI_NGUYÊN>_NOT_FOUND` | Không tìm thấy (VD: `PROCEDURE_NOT_FOUND`) |
| 409 | `CONFLICT` hoặc mã riêng | Trùng dữ liệu, sai trạng thái (VD: `CONSULTATION_ALREADY_ACCEPTED`) |
| 413 | `FILE_TOO_LARGE` | File vượt giới hạn |
| 415 | `UNSUPPORTED_FILE_TYPE` | Loại file không cho phép |
| 422 | `VALIDATION_ERROR` | Dữ liệu gửi sai, kèm `details` |
| 429 | `RATE_LIMITED` | Gọi quá nhiều lần (áp dụng cho API gọi AI) |
| 500 | `INTERNAL_ERROR` | Lỗi Backend không lường trước |
| 502 | `<SERVICE>_BAD_RESPONSE` | Service phụ trả lỗi 5xx hoặc dữ liệu sai cấu trúc |
| 503 | `<SERVICE>_UNAVAILABLE` | Không kết nối được service phụ |
| 504 | `<SERVICE>_TIMEOUT` | Service phụ quá thời gian chờ |

`<SERVICE>` là tên service phụ viết hoa, ví dụ `PROCEDURE_TIMEOUT` (hiện có trong agent-service). Mã mới thêm vào bảng này cùng với code.

---

## 6. Quy ước dữ liệu

- Tên trường JSON dùng `snake_case`: `user_id`, `created_at`. Trường boolean bắt đầu bằng `is_` / `has_`: `is_read`.
- Bản ghi mới dùng **UUID** làm `id`. Mã nghiệp vụ (mã thủ tục…) lưu riêng trong `code`. Dữ liệu mẫu hiện có vẫn dùng ID `HKD-001`.
- Thời gian dùng ISO 8601 kèm múi giờ, lưu và trả theo UTC: `2026-10-02T02:00:00Z` (FE chấp nhận cả dạng `+00:00`). FE tự đổi sang giờ Việt Nam khi hiển thị.
- Tiền dùng **số nguyên đơn vị đồng** (`fee: 100000`), không dùng số thực.
- Giá trị enum viết thường, `snake_case`: `"status": "in_progress"`.
- Trường không có giá trị trả `null`, không bỏ khỏi kết quả.
- Danh sách rỗng trả `items: []` với status 200. Lấy chi tiết một ID không tồn tại trả `404`.
- **Phân trang:** `page` (mặc định 1), `page_size` (mặc định 20, tối đa 100). Sắp xếp dùng `sort` và `order` (`asc` | `desc`). Endpoint phải ghi rõ trường nào được sắp xếp.

---

## 7. Đăng nhập và phân quyền

> Chưa triển khai. Đây là quy ước để mọi service làm giống nhau.

### Frontend → Backend

- User Service cấp **JWT** sau khi đăng nhập Firebase (Google hoặc email/mật khẩu): FE gửi Firebase ID token lên `POST /api/users/auth/firebase`, nhận `access_token` + `refresh_token` của hệ thống. FE gửi token trong header:
  ```javascript
  headers: { "Content-Type": "application/json", "Authorization": `Bearer ${accessToken}` }
  ```
- Token chứa `sub` (user id), `role` (`citizen` | `lawyer` | `admin`), `exp`. Token ký bằng **RS256**: User Service ký bằng private key, mọi service khác tự kiểm tra bằng public key qua module `libs/common_auth`, **không gọi User Service ở mỗi request**.
- Vai trò trong token là vai trò lúc đăng nhập. Khi admin duyệt hoặc khóa luật sư, người dùng phải đăng nhập lại. Thao tác nhạy cảm của luật sư (nhận yêu cầu, gửi tin) kiểm tra thêm trạng thái luật sư qua User Service để luật sư bị khóa mất quyền ngay.
- **Không** dùng `user_id` FE tự gửi làm bằng chứng sở hữu. Chủ sở hữu luôn lấy từ `sub` trong token.
- Service nhận request tự kiểm tra quyền trên dữ liệu của mình (ví dụ hợp đồng có đúng của người đang gọi không).

### Service → Service

| Trường hợp | Gửi kèm | Bên nhận kiểm tra |
|---|---|---|
| Gọi **thay mặt người dùng** (VD: Agent lấy hợp đồng của user) | `Authorization: Bearer <JWT của user>` + `X-Request-ID` | JWT như request từ FE |
| Gọi **của hệ thống** (đồng bộ, job nền, gửi thông báo) | `X-Internal-Token: <INTERNAL_TOKEN>` + `X-Request-ID`, chỉ gọi đường dẫn `/internal/v1/...` | So khớp `INTERNAL_TOKEN` |

- **Không** tin header kiểu `X-User-Id` do bên gọi tự đặt.
- API nội bộ **không được mặc định an toàn** chỉ vì chạy trong Docker.
- Không đưa token, mật khẩu, file key `*.pem`, `INTERNAL_TOKEN` vào Git.

---

## 8. Khi service gọi service khác

- Thống nhất URL, dữ liệu gửi và kết quả trả **trước khi** tích hợp. Có file mẫu trong `contracts/` và test so khớp (như `contracts/chat.example.json` hiện có).
- Luôn kiểm tra HTTP status và cấu trúc dữ liệu nhận về.
- **Timeout** mỗi lần gọi:

  | Loại lời gọi | Timeout |
  |---|---|
  | API thường | 5 giây (hiện có ở agent) |
  | Gọi LLM, tạo embedding | 60 giây |
  | OCR, AI kiểm tra giấy tờ | 60 giây |
  | Gateway chờ `/api/agent/chat` | 120 giây |

- **X-Request-ID:** Gateway tự tạo (hiện có). Service nhận mã, truyền tiếp khi gọi service khác, ghi vào log và trả lại trong response. Gọi trực tiếp không có mã thì service tự tạo mã mới.
- **Lỗi từ service phụ:**
  - Không kết nối được → `503 <SERVICE>_UNAVAILABLE`.
  - Quá thời gian → `504 <SERVICE>_TIMEOUT`.
  - Service phụ trả 5xx hoặc dữ liệu sai cấu trúc → `502 <SERVICE>_BAD_RESPONSE`.
  - Service phụ trả 4xx (404, 409, 422) là **kết quả nghiệp vụ**: xử lý theo contract, không đổi thành 500/502.
  - Không bao giờ trả thành công kèm dữ liệu tự đoán. Ví dụ Agent gọi Procedure bị timeout thì báo lỗi rõ ràng cho FE.
- **Retry:** chỉ retry request `GET` hoặc API được ghi rõ là gọi lại an toàn, tối đa 2 lần, chờ 0,5 giây rồi 1 giây. **Không** tự retry thao tác tạo hợp đồng, upload, gửi tin nhắn.
- **Log:** ghi tên service, thao tác, status, thời gian xử lý, `request_id`. **Không** log token, mật khẩu, nội dung hợp đồng, giấy tờ cá nhân, nội dung chat.

---

## 9. Upload file

- Gửi bằng `multipart/form-data`, trường tên `file`.
- Dung lượng tối đa **10 MB** (Gateway hiện có `client_max_body_size 10m`). Vượt quá trả `413 FILE_TOO_LARGE`.
- Loại cho phép: `pdf`, `jpg`, `png`, `docx`. Kiểm tra theo **nội dung file**, không chỉ theo đuôi. Sai loại trả `415 UNSUPPORTED_FILE_TYPE`.
- File lưu ở Storage, database chỉ lưu `storage_key`. Tải về bằng link có hạn ngắn, không để link công khai vĩnh viễn.
- Ảnh giấy tờ xóa thông tin EXIF (vị trí GPS…) trước khi lưu.

---

## 10. WebSocket (chat với luật sư)

> Gateway đã chuẩn bị route WebSocket. Endpoint realtime trong Consultation Service chưa triển khai.

- Đường dẫn: FE kết nối `ws://localhost:8080/ws/consultations/...`, Gateway chuyển tới `/ws/v1/consultations/...` của consultation-service. Gateway cần cấu hình thêm header `Upgrade` / `Connection` cho WebSocket.
- Kênh:
  - `/ws/consultations/lobby`: phòng chờ, chỉ luật sư đã duyệt; nhận câu hỏi mới và câu hỏi đã có người nhận.
  - `/ws/consultations/{id}`: box chat riêng, chỉ 2 người trong cuộc.
- **Xác thực:** trình duyệt không gắn được header cho WebSocket, nên tin nhắn **đầu tiên** sau khi kết nối phải là:
  ```json
  { "type": "auth", "data": { "token": "<JWT>" } }
  ```
  Không gửi trong 5 giây thì server đóng kết nối.
- Format mọi tin nhắn: `{ "type": "...", "data": { ... } }`. Ví dụ `type`: `consultation.created`, `consultation.accepted`, `message.created`, `error`.
- Mã đóng kết nối: `4401` chưa xác thực / token sai, `4403` không có quyền vào kênh, `4404` không tìm thấy cuộc trò chuyện.
- **Gửi tin nhắn bằng REST** (`POST /api/consultations/{id}/messages`) để có validation và format lỗi chuẩn. WebSocket chỉ dùng để **nhận** tin realtime.
- Mất kết nối thì FE tự kết nối lại (chờ 1, 2, 4… tối đa 30 giây), sau đó gọi REST lấy tin nhắn bị lỡ: `GET /api/consultations/{id}/messages?after=<id tin cuối>`.

---

## 11. CORS

- Chỉ cấu hình CORS ở **Gateway**, không cấu hình trong từng service.
- Địa chỉ FE được phép (hiện có): `http://localhost:3000`, `http://127.0.0.1:3000`. FE dùng cổng khác hoặc deploy thì báo Backend thêm vào.
- Method được phép: `GET, POST, PUT, PATCH, DELETE, OPTIONS` (đã cấu hình ở Gateway).
- Header được phép: `Content-Type`, `Authorization`, `X-Request-ID`.

---

## 12. API contract và bàn giao API

### API contract (`openapi.yaml`)

- Mỗi service có **một file** `services/<tên>-service/openapi.yaml` (OpenAPI 3.0.3). Mẫu tham khảo: `services/procedure-service/openapi.yaml`.
- **Viết contract trước, code sau.** API mới hoặc thay đổi API: sửa `openapi.yaml` trong Merge Request, nhóm duyệt xong mới code. FE dựa vào contract để làm song song.
- Mỗi API ghi `x-status`: `implemented` (đã chạy đúng contract), `partial` (đã có nhưng chưa đủ), `planned` (mới thiết kế).
- Mỗi API có `example` cho kết quả thành công và các lỗi chính.
- Kiểm thử bằng **Postman**: Import file `openapi.yaml` để tạo collection, đặt biến `baseUrl`. Không commit collection sinh ra từ file; chỉ commit `openapi.yaml`.

### Ghi chú bàn giao

Ngoài contract, mỗi API mới ghi thêm theo mẫu trong `docs/api/<SERVICE>_API.md` (ví dụ `USER_API.md`, `CONTRACT_API.md`):

```text
Tên API:        Tìm thủ tục
Service:        Procedure
Người phụ trách: ...
URL cho FE:     POST /api/procedures/search
URL nội bộ:     POST /api/v1/procedures/search
Cần đăng nhập:  Không | Có (vai trò: citizen / lawyer / admin)
Header:         Content-Type: application/json [, Authorization: Bearer <token>]
Dữ liệu gửi:    {"query": "đăng ký hộ kinh doanh"}
Điều kiện:      query có 1–2000 ký tự, không chỉ chứa khoảng trắng
Phân trang:     Không | page (mặc định 1), page_size (mặc định 20, tối đa 100)
Kết quả:        data.items chứa danh sách thủ tục
Lỗi thường gặp: 422 VALIDATION_ERROR khi query rỗng hoặc quá dài
Contract:       services/procedure-service/openapi.yaml (operationId: searchProcedures)
Trạng thái:     Đã làm / Đang làm / Chưa làm
```

- **Không tự đổi** tên trường hoặc đường dẫn đã thống nhất. Cần đổi thì báo người đang dùng API trước, cập nhật tài liệu và file mẫu cùng lúc với code.
- API chưa viết thì chưa gọi được, dù đã ghi trong tài liệu (xem `x-status`).

---

## 13. Test

Mỗi API trước khi bàn giao phải có test cho:

1. Trường hợp thành công.
2. Dữ liệu gửi sai → `422 VALIDATION_ERROR`.
3. API cần đăng nhập: không có token → `401`, sai vai trò hoặc không phải chủ dữ liệu → `403` / `404`.
4. API gọi service khác: service phụ lỗi kết nối / timeout / trả sai → `503` / `504` / `502` (như test agent hiện có).
5. Nếu có file mẫu trong `contracts/`: kết quả khớp đúng file mẫu.

CI trên GitLab (`.gitlab-ci.yml`, hiện có) chạy `pytest tests` cho mọi Merge Request. **CI đỏ thì không merge.**

---

## 14. Quy trình Git

- Nhánh:
  - `main`: bản ổn định để demo / nộp. Chỉ nhận Merge Request từ `dev`.
  - `dev`: nhánh tích hợp. Mọi nhánh tính năng merge vào đây.
  - `feature/<tên-ngắn>`, `fix/<tên-ngắn>`: tạo từ `dev`, mỗi nhánh một việc.
- Không push thẳng vào `main` hoặc `dev`. Tạo Merge Request, cần **ít nhất 1 người duyệt** và **CI xanh**.
- Commit message: `<loại>: <mô tả ngắn>`, loại gồm `feat`, `fix`, `docs`, `test`, `refactor`, `chore`. Ví dụ: `feat: thêm API tìm thủ tục`.
- Trước khi tạo Merge Request: `git pull origin dev` để cập nhật, chạy test ở máy.
- Không commit `.env`, file `.venv/`, `__pycache__/`, dữ liệu cá nhân thật.

---

## 15. Tình trạng triển khai

| Hạng mục | Tình trạng |
|---|---|
| Gateway: chuyển `/api/...` → `/api/v1/...`, tạo `X-Request-ID`, CORS cổng 3000, `GET /` | Hiện có |
| Format `{data, error, request_id}` | Hiện có ở agent, procedure, rag, contract; mỗi service tự viết |
| `error.details` theo từng trường | Chưa có |
| Code xác thực dùng chung | Hiện có `libs/common_auth` (RS256, kiểm tra token và role); chưa có module dùng chung cho mọi response nghiệp vụ |
| Agent gọi Procedure với timeout 5 giây, trả 502/503/504 | Hiện có, có test |
| CORS cho `PUT/PATCH/DELETE` | Hiện có ở Gateway |
| Đăng nhập Firebase, JWT, vai trò | User Service và `libs/common_auth` đã có code/test. Contract/Notification đã gắn handler lỗi và router JWT: health công khai, /me kiểm tra token; templates Contract cần token. API nghiệp vụ planned chưa có code |
| API nội bộ `/internal/v1` + `X-Internal-Token` | Chưa có |
| Database và migration từng service | Đã có database boundary và thư mục migration cho 10 service; chưa có schema/migration thật |
| Upload file | Chưa có |
| Lawyer, Consultation, Notification Service | Đã có skeleton, health check, Gateway route và OpenAPI health contract; nghiệp vụ chưa làm |
| WebSocket chat trong Consultation Service | Gateway route đã chuẩn bị; endpoint realtime chưa có |
| CI chạy pytest trên GitLab | Hiện có |
| `openapi.yaml` | Xem file trong từng service; `x-status` phân biệt hiện có/planned. Contract và Notification đã đối chiếu develop dd79b1a; file Document trên nhánh này còn nội dung Procedure cần người phụ trách sửa |

## 16. Bản thiết kế Contract và Notification

- Hướng dẫn dễ đọc: [docs/api/CONTRACT_NOTIFICATION_API.md](api/CONTRACT_NOTIFICATION_API.md).
- Tích hợp thông báo: [docs/api/NOTIFICATION_INTEGRATION.md](api/NOTIFICATION_INTEGRATION.md).
- Đề xuất để nhóm review: nhận event bằng API nội bộ, FE polling 10 giây. Chưa triển khai endpoint nhận event/outbox/polling.
- API sinh file dự kiến dùng `Idempotency-Key`. Gateway đã cho phép header này trong CORS ngày 08/10/2026; endpoint sinh file vẫn planned.
- Document/Procedure/Consultation là phụ thuộc API; Hiền không chỉnh nghiệp vụ các service này. Xem mục 5 trong tài liệu trên để biết những gì cần người phụ trách xác nhận.
