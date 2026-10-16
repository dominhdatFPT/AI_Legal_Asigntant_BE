# Tổng quan dự án Legal Assistant AI (dành cho người mới)

> Cập nhật: 09/10/2026. Người viết: Claude, theo yêu cầu của Dũng.
> Đọc file này trước, sau đó mới đọc tài liệu chi tiết ở mục 9.
> File này mô tả trạng thái trên máy Dũng (nhánh `feature/gemini-agent`). Một số phần ở mục 6 **chưa được commit** lên GitLab.

---

## 1. Dự án này làm gì?

**Legal Assistant AI** là hệ thống web giúp người dân Việt Nam tự giải quyết các việc pháp lý thường gặp, có AI hỗ trợ và có luật sư khi cần. 
Người dùng có thể:

| Nhu cầu | Ví dụ | Service lo việc này |
|---|---|---|
| Hỏi AI về pháp luật, có trích dẫn điều luật | "Sang tên sổ đỏ cần những gì?" → AI trả lời kèm "Khoản 1 Điều 188, Luật Đất đai 2024" | agent-service + rag-service |
| Tra cứu thủ tục hành chính | Hồ sơ, lệ phí, căn cứ pháp lý của thủ tục đăng ký hộ kinh doanh | procedure-service |
| Soạn hợp đồng từ mẫu | Chọn mẫu hợp đồng thuê nhà, điền thông tin, xuất file | contract-service |
| Lưu giấy tờ cá nhân | Upload ảnh/scan giấy tờ, OCR | document-service |
| Tìm nơi nộp hồ sơ | Cơ quan tiếp nhận gần nhất, bản đồ | location-service |
| Nhờ luật sư tư vấn | Gửi yêu cầu, chat 1-1 với luật sư | consultation-service + lawyer-service |
| Nhận thông báo | "Luật sư đã nhận yêu cầu của bạn" | notification-service |
| Đăng nhập, hồ sơ, phân quyền | Đăng nhập Google, vai trò `citizen` / `lawyer` / `admin` | user-service |

Ba vai trò người dùng: **citizen** (người dân), **lawyer** (luật sư, admin phải duyệt), **admin**.

---

## 2. Kiến trúc tổng thể

```text
Frontend (cổng 3000)
   │  gọi /api/...
   ▼
Gateway nginx (localhost:8080)  ── đổi /api/<tên>/... thành /api/v1/<tên>/..., gắn X-Request-ID, CORS
   │
   ├─► user-service          ─► DB riêng (USER_DATABASE_URL)
   ├─► agent-service         ─► DB riêng (AGENT_DATABASE_URL)
   │      ├─ gọi rag-service (tìm đoạn luật)      ─► DB riêng + pgvector (RAG_DATABASE_URL)
   │      ├─ gọi procedure-service (tìm thủ tục)
   │      └─ gọi Gemini API (Google) để sinh câu trả lời
   ├─► procedure / contract / document / location / lawyer
   ├─► consultation-service  (+ WebSocket /ws/consultations/ cho chat với luật sư)
   └─► notification-service
```

Nguyên tắc quan trọng:

- **Microservices, mỗi service một database riêng** (Supabase PostgreSQL). Service chỉ đọc/ghi DB của mình; cần dữ liệu service khác thì **gọi API**, không nối khóa ngoại sang DB khác.
- **Tất cả service viết bằng Python + FastAPI**, chạy trong Docker, cổng nội bộ 8000, sau Gateway.
- **Đăng nhập**: user-service xác thực Firebase ID token rồi cấp JWT **RS256** của hệ thống (access + refresh). Các service khác tự kiểm tra JWT bằng public key qua thư viện chung `libs/common_auth`, không gọi user-service mỗi request.
- **Gọi nội bộ giữa service**: thay mặt người dùng thì chuyển tiếp JWT; gọi kiểu hệ thống thì dùng đường dẫn `/internal/v1/...` kèm header `X-Internal-Token`.
- **AI**: dùng **Gemini API** (Google) qua thư viện `google-genai`. Không tải model về máy (Gemini là model đóng); chỉ cần API key.
- **RAG** (Retrieval-Augmented Generation): văn bản luật được cắt thành đoạn, tạo embedding, lưu vào **pgvector**. Khi người dùng hỏi, rag-service tìm các đoạn liên quan, agent-service đưa các đoạn đó cho Gemini và yêu cầu chỉ trả lời dựa trên chúng, kèm trích dẫn.

---

## 3. Các service và người phụ trách

Tên người phụ trách lấy từ trường `x-owner` trong `openapi.yaml` của từng service. "Số API" là số endpoint đã thiết kế trong `openapi.yaml`; phần lớn vẫn ở trạng thái `planned` (chưa code).

| Service | Phụ trách | Làm gì | Tình trạng (09/10/2026) |
|---|---|---|---|
| user-service | Người 1 · Đạt | Tài khoản, đăng nhập Firebase + username/password, JWT, refresh token, vai trò | **Đã có code + test**: 6/13 API, có SQL schema và seed tài khoản admin |
| agent-service | Dũng | Điều phối trả lời AI, lưu lịch sử chat với AI | **Đã có code + 37 test** trên nhánh `feature/gemini-agent` (xem mục 6), chưa commit |
| rag-service | Dũng (theo `openapi.yaml`, cần nhóm xác nhận) | Lưu văn bản luật, embedding, tìm kiếm đoạn liên quan | Mới là khung: `/api/v1/rag/search` trả dữ liệu giả, `/internal/v1/rag/search` chưa có. Đã có **bản nháp** schema vector DB (mục 6.3) |
| procedure-service | (chưa điền) | Thủ tục hành chính, checklist giấy tờ, lệ phí | Có `/procedures/search` chạy với dữ liệu mẫu viết sẵn trong `main.py`, chưa có DB |
| contract-service | Hiền | Mẫu hợp đồng, phiên bản mẫu, hợp đồng cá nhân, sinh file | Có khung, JWT, API mẫu hợp đồng cần token; 25 API đã thiết kế, nghiệp vụ chủ yếu planned |
| notification-service | Hiền | Thông báo trong web, đã đọc/chưa đọc, nhận event từ service khác | Có khung + JWT; 9 API thiết kế, nhận event chưa làm |
| document-service | (chưa điền) | Giấy tờ upload, OCR | Khung; `openapi.yaml` còn chép nội dung của procedure, cần người phụ trách sửa |
| location-service | (chưa điền) | Cơ quan tiếp nhận, địa chỉ, bản đồ | Khung, chỉ có health check |
| lawyer-service | Người 1 · Đạt | Hồ sơ luật sư, thống kê, online/offline, admin duyệt | Khung + health check; 13 API thiết kế |
| consultation-service | Người 1 · Đạt | Yêu cầu tư vấn, chat 1-1 citizen–luật sư (WebSocket), report | Khung + health check; WebSocket chưa có |

Mọi service đều đã có: thư mục riêng, Dockerfile, health check `GET /api/v1/<tên>/health`, route trong Gateway, biến database riêng.

---

## 4. Cấu trúc thư mục

```text
legal-assistant-ai/
├── README.md                 # README của nhóm (không sửa tùy tiện)
├── TONG_QUAN_DU_AN.md        # file này
├── README-GEMINI.md          # chi tiết phần Gemini/agent-service + nhật ký công việc của Dũng
├── docker-compose.yml        # chạy toàn bộ hệ thống
├── docker-compose.jwt-local.yml
├── .env.example              # mẫu biến môi trường (copy thành .env)
├── requirements-test.txt     # gom requirements của mọi service để cài 1 venv chung
├── gateway/nginx.conf        # định tuyến /api/... tới từng service
├── libs/common_auth/         # thư viện chung kiểm tra JWT
├── keys/                     # cặp key JWT dev (*.pem, đã gitignore)
├── contracts/                # file JSON mẫu giao tiếp giữa service
├── scripts/                  # mint_dev_token.py (tạo token test), kiểm tra JWT
├── tests/                    # test cấp hệ thống (gateway, openapi, docker)
├── docs/                     # API_GUIDELINES.md và tài liệu khác
├── database/                 # chỉ ghi quy ước, KHÔNG có bảng dùng chung
└── services/<tên>-service/
    ├── app/
    │   ├── main.py           # tạo FastAPI app
    │   ├── api/              # router (endpoint)
    │   ├── core/             # config, database, security
    │   ├── models/           # bảng SQLAlchemy
    │   ├── schemas/          # Pydantic (dữ liệu vào/ra)
    │   ├── repositories/     # truy vấn DB
    │   └── services/         # logic nghiệp vụ, gọi service khác
    ├── database/             # file SQL tạo bảng + file kiểm tra, chạy tay trên Supabase
    ├── tests/
    ├── openapi.yaml          # hợp đồng API (x-owner, x-status)
    ├── requirements.txt
    └── Dockerfile
```

---

## 5. Quy ước cả nhóm phải theo

Chi tiết ở `docs/API_GUIDELINES.md` (bản v2, 02/10/2026). Tóm tắt:

**Đường dẫn**: FE gọi `http://localhost:8080/api/<tên>/...`; bên trong service dùng `/api/v1/<tên>/...`; API nội bộ dùng `/internal/v1/...`.

**Format kết quả** (mọi API trừ health check):

```json
{ "data": { ... }, "error": null, "request_id": "req-123" }
```

- Danh sách: `data.items` + `data.pagination {page, page_size, total, total_pages}`; `page_size` mặc định 20, tối đa 100.
- Lỗi: `data: null`, `error: {code, message, details}`; `message` tiếng Việt hiển thị được cho người dùng, không lộ SQL/stack trace.
- Mã lỗi: 401 `UNAUTHORIZED`, 403 `FORBIDDEN`, 404 `<TÀI_NGUYÊN>_NOT_FOUND`, 422 `VALIDATION_ERROR`, 502/503/504 `<SERVICE>_BAD_RESPONSE` / `_UNAVAILABLE` / `_TIMEOUT`.

**Dữ liệu**: JSON `snake_case`; `id` là UUID; thời gian ISO 8601 theo UTC; tiền là số nguyên (đồng); không lấy `user_id` từ FE làm bằng chứng sở hữu, luôn lấy từ `sub` trong JWT.

**Database**: mỗi service để file SQL trong `services/<tên>/database/` (ví dụ `001_..._schema.sql` + `check_..._db.sql`), chạy tay trong Supabase SQL Editor. Code dùng SQLAlchemy 2 (sync) + psycopg, giống user-service.

**Test**: mỗi API cần test thành công, 422, 401/403/404, và lỗi service phụ (502/503/504). CI GitLab chạy pytest cho mọi Merge Request; **CI đỏ thì không merge**.

**Git**:

- `main`: bản ổn định. `develop`: nhánh tích hợp (tài liệu gọi là `dev`, trên GitLab tên là `develop`).
- Tạo nhánh `feature/<tên-ngắn>` hoặc `fix/<tên-ngắn>` từ `develop`, xong thì tạo Merge Request vào `develop`, cần 1 người duyệt + CI xanh.
- Commit: `<loại>: <mô tả ngắn>`, loại là `feat`, `fix`, `docs`, `test`, `refactor`, `chore`.
- **Không commit** `.env`, `.venv/`, `*.pem`, key API, dữ liệu cá nhân thật.

---

## 6. Những gì đã làm được (phần agent-service và RAG, do Dũng làm cùng Claude)

Tất cả nằm trên nhánh local `feature/gemini-agent` của Dũng, **chưa commit**. Chi tiết từng bước có trong `README-GEMINI.md`.

### 6.1. Gọi Gemini

- Thêm `GEMINI_API_KEY`, `GEMINI_MODEL` vào `docker-compose.yml`, `.env.example` và `app/core/config.py`.
- `app/services/llm_client.py`: gửi lịch sử hội thoại + câu hỏi cho Gemini, trả về câu trả lời và số token.
- `scripts/try_gemini.py`: chạy thử với key thật. Đã chạy được với model **`gemini-3.8-flash`** (`gemini-2.5-flash` báo 404 với tài khoản mới). Gemini đôi lúc trả **503 quá tải**, thử lại sau là được.

### 6.2. Trả lời có trích dẫn luật

- `app/services/rag_client.py`: gọi `POST /internal/v1/rag/search` của rag-service theo hợp đồng trong `rag-service/openapi.yaml`.
- `app/services/agent.py`: lấy các đoạn luật → ghép vào system prompt dạng `[1] Khoản 1 Điều 188, Luật Đất đai 2024 (31/2024/QH15)` → Gemini trả lời chỉ dựa trên các đoạn đó, có trích dẫn, nói rõ khi không đủ căn cứ.

### 6.3. Database

- **agent-service (xong phần code)**: bảng `conversations` và `messages` (lưu metadata: id các đoạn luật đã dùng, model, số token). File `database/001_agent_schema.sql` + `check_agent_db.sql`, đã chạy thử trên PostgreSQL 16 thật. **Chưa tạo bảng trên Supabase.**
- **rag-service (bản nháp)**: bảng `documents` + `document_chunks` với `embedding vector(768)` (embedding Gemini), index HNSW cosine. Đã chạy thử trên PostgreSQL + pgvector. Bản nháp đang ở thư mục ghi chú của dự án (`notes/rag-vector-db/`), **chưa chép vào `services/rag-service/`** vì cần nhóm thống nhất chọn embedding.

### 6.4. API lịch sử chat với AI (agent-service)

Tất cả cần JWT (`Authorization: Bearer <token>`), chỉ thấy hội thoại của chính mình:

| Method | Đường dẫn (qua Gateway) | Việc |
|---|---|---|
| POST | `/api/agent/conversations` | Tạo hội thoại |
| GET | `/api/agent/conversations?page=1&page_size=20` | Danh sách hội thoại |
| GET | `/api/agent/conversations/{id}` | Chi tiết + tin nhắn |
| DELETE | `/api/agent/conversations/{id}` | Xóa hội thoại |
| POST | `/api/agent/conversations/{id}/messages` | Gửi câu hỏi → RAG → Gemini → lưu cả câu hỏi và câu trả lời |

Lỗi riêng: `CONVERSATION_NOT_FOUND` (404), `CONVERSATION_FORBIDDEN` (403), `RAG_SERVICE_ERROR` / `AI_SERVICE_ERROR` (503, không lưu gì khi lỗi). Endpoint cũ `POST /api/agent/chat` (gọi procedure-service) vẫn giữ nguyên.

### 6.5. Test

- `services/agent-service/tests/`: **37 test đạt** (trên máy Claude và laptop Windows của Dũng). Test dùng SQLite trong RAM và giả lập rag-service/Gemini, nên **không cần** Supabase hay key thật.
- Test hệ thống cũ liên quan agent (`tests/test_api.py`, `tests/test_openapi.py -k agent`): vẫn đạt.

---

## 7. Những gì cần làm tiếp

### 7.1. Việc gấp (Dũng)

1. **Đổi Gemini API key**: key cũ đã lộ trong ảnh chụp màn hình. Tạo key mới ở https://aistudio.google.com, xóa key cũ, cập nhật `.env` gốc.
2. **Commit và đẩy nhánh** `feature/gemini-agent`, rồi tạo Merge Request vào `develop`. Kiểm tra `git status` không có `.env`, `.venv/`, `*.pem`.
3. **Tạo bảng agent trên database thật**: chạy `services/agent-service/database/001_agent_schema.sql` rồi `check_agent_db.sql` (Supabase SQL Editor của DB agent, hoặc PostgreSQL tự dựng).

### 7.2. agent-service (đang chờ quyết định)

- Đổi giá trị mặc định model từ `gemini-2.5-flash` sang `gemini-3.8-flash` trong `config.py`, `docker-compose.yml`, hai file `.env.example`.
- Tự thử lại khi Gemini trả 503.
- Thêm `services/agent-service/tests` vào `.gitlab-ci.yml` (hiện CI chỉ chạy `tests`, `libs/common_auth`, `services/user-service/tests`).
- Đổi `x-status` của 5 API hội thoại trong `agent-service/openapi.yaml` từ `planned` sang `implemented`; sửa chữ "OpenAI" thành Gemini.
- Sau này: gọi thêm procedure-service / contract-service trong luồng trả lời, giới hạn tần suất gọi AI (429 `RATE_LIMITED`).

### 7.3. rag-service (cần làm để AI trả lời được bằng dữ liệu thật)

1. Nhóm chốt **model embedding** (đề xuất Gemini `gemini-embedding-001`, 768 chiều; skeleton hiện dùng thư viện `openai`).
2. Chép bản nháp schema vào `services/rag-service/database/`, tạo bảng trên DB của rag.
3. Viết luồng **nạp văn bản luật**: đọc file → cắt theo chương/điều/khoản → tạo embedding → lưu `document_chunks`.
4. Viết `POST /internal/v1/rag/search` (kiểm tra `X-Internal-Token`) trả `data.chunks[]` đúng hợp đồng.
5. Nạp bộ văn bản đầu tiên (ví dụ Luật Đất đai 2024, xem `docs/danh_sach_giay_to_dat_dai.md`).

Khi chưa có bước 4, gửi tin nhắn qua API thật sẽ trả 503 `RAG_SERVICE_ERROR`. Đây là đúng thiết kế, không phải lỗi.

### 7.4. Các service khác (theo người phụ trách)

- **user-service**: hoàn thiện 7 API còn lại.
- **procedure-service**: chuyển dữ liệu mẫu sang DB thật, điền `x-owner`.
- **contract-service, notification-service**: code các API đã thiết kế; notification nhận event qua API nội bộ, FE polling 10 giây (đang chờ nhóm review).
- **lawyer-service, consultation-service**: nghiệp vụ CRUD, duyệt luật sư, WebSocket chat.
- **document-service**: sửa `openapi.yaml` (đang chép nhầm của procedure), làm upload + OCR.
- **location-service**: thiết kế API và dữ liệu cơ quan tiếp nhận.
- Chung: API nội bộ `/internal/v1` + `X-Internal-Token` mới chỉ có trong thiết kế; `error.details` theo từng trường mới có ở agent-service.

---

## 8. Bắt đầu làm việc trên máy

Yêu cầu: Windows + VS Code (hoặc máy khác), Python, Git, Docker Desktop (nếu chạy cả hệ thống).

```powershell
# 1. Lấy code
git clone <địa chỉ GitLab của nhóm> legal-assistant-ai
cd legal-assistant-ai
git checkout develop

# 2. Tạo venv chung ở thư mục gốc và cài thư viện
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-test.txt

# 3. Tạo file .env từ mẫu rồi điền giá trị thật (hỏi nhóm, không gửi lên Git)
copy .env.example .env
```

Biến quan trọng trong `.env`: `*_DATABASE_URL` của từng service, `INTERNAL_TOKEN`, `JWT_PRIVATE_KEY_PATH`, `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-3.8-flash`. Biến mới phải được khai báo thêm trong khối `environment:` của service đó trong `docker-compose.yml`, nếu không Docker sẽ không truyền vào.

Chạy test (luôn đứng ở thư mục gốc, venv đang bật):

```powershell
pytest tests libs/common_auth services/user-service/tests   # giống CI
pytest services/agent-service/tests                          # test agent-service
```

Chạy toàn bộ hệ thống bằng Docker:

```powershell
docker compose up --build
# Gateway: http://localhost:8080
# Swagger từng service: xem cổng trong docker-compose.yml, đường dẫn /docs
```

Tạo token để thử API cần đăng nhập: `python scripts/mint_dev_token.py --role citizen` (cần key trong `keys/`, xem `docs/api/JWT_DOCKER_CONTRACT_NOTIFICATION.md`).

---

## 9. Đọc tiếp ở đâu

| Tài liệu | Nội dung |
|---|---|
| `docs/API_GUIDELINES.md` | Quy ước API, lỗi, JWT, Git, tình trạng triển khai (bắt buộc đọc) |
| `README.md` | README của nhóm, danh sách service và database |
| `README-GEMINI.md` | Chi tiết agent-service: Gemini, RAG, database, API, cấu hình, nhật ký |
| `services/<tên>/openapi.yaml` | Hợp đồng API của từng service (`x-status`: implemented / planned) |
| `services/<tên>/database/README.md` | Cách tạo bảng của service đó |
| `docs/api/CONTRACT_NOTIFICATION_API.md`, `NOTIFICATION_INTEGRATION.md` | Thiết kế contract và notification |
| `docs/api/JWT_DOCKER_CONTRACT_NOTIFICATION.md` | Cấu hình JWT với Docker |
| `docs/danh_sach_giay_to_dat_dai.md` | Danh sách giấy tờ đất đai (dữ liệu nghiệp vụ) |

---

## 10. Lưu ý bảo mật

- Không bao giờ đưa API key, mật khẩu DB, `INTERNAL_TOKEN`, file `*.pem` vào Git, chat nhóm hay ảnh chụp màn hình. Đóng tab `.env` trước khi chụp màn hình.
- Lộ key thì đổi ngay (tạo key mới, xóa key cũ).
- Không tin header `X-User-Id` do bên gọi tự đặt; danh tính luôn lấy từ JWT.
- API nội bộ không được coi là an toàn chỉ vì chạy trong Docker: luôn kiểm tra `X-Internal-Token`.
