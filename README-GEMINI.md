# agent-service: trợ lý pháp lý AI (Gemini + RAG)

Tài liệu tổng quan về agent-service trong hệ thống legal-assistant-ai: service làm gì, gồm những phần nào, cấu hình và chạy ra sao. `README.md` của nhóm giữ nguyên, file này chỉ nói về agent-service và phần tích hợp Gemini.

## 1. Tổng quan hệ thống

Backend gồm nhiều microservice FastAPI, mỗi service có database riêng (Supabase PostgreSQL), giao tiếp với nhau qua HTTP. Frontend gọi qua Gateway (nginx, cổng 8080), Gateway chuyển `/api/agent/...` tới agent-service.

agent-service nhận câu hỏi của người dùng, lấy trích đoạn văn bản pháp luật từ rag-service, rồi nhờ Gemini viết câu trả lời có trích dẫn điều luật. Lịch sử hội thoại được lưu trong database riêng của agent.

```
Frontend ──► Gateway (/api/agent/...) ──► agent-service
                                            │ 1. kiểm tra JWT (khóa công khai của user-service)
                                            │ 2. lấy lịch sử hội thoại ──► Database agent (Supabase)
                                            │ 3. tìm trích đoạn luật    ──► rag-service /internal/v1/rag/search
                                            │ 4. tạo câu trả lời        ──► Gemini API (Google)
                                            │ 5. lưu câu hỏi + câu trả lời ──► Database agent
                                            ▼
                                       trả về cho người dùng
```

Vì sao gọi Gemini qua API: Gemini là model đóng, Google không công bố trọng số nên không tải về chạy local được. agent-service gọi **Gemini API** bằng API key qua SDK [`google-genai`](https://pypi.org/project/google-genai/). Nếu sau này cần model mở chạy local thì có thể cân nhắc **Gemma**, nhưng cần GPU và chất lượng thấp hơn.

## 2. Các thành phần trong agent-service

| File | Vai trò |
|---|---|
| `app/main.py` | Tạo app FastAPI, xử lý lỗi chung, đăng ký router |
| `app/api/deps.py`, `app/core/auth.py` | Xác thực JWT, lấy người dùng đang đăng nhập |
| `app/api/v1/conversations.py` | 5 endpoint hội thoại |
| `app/services/agent.py` | Điều phối: RAG → ghép prompt có trích dẫn → Gemini |
| `app/services/rag_client.py` | Gọi rag-service (kèm `X-Internal-Token`), ném `RAGError` khi lỗi |
| `app/services/llm_client.py` | Gọi Gemini (async), ném `LLMError` khi lỗi |
| `app/core/config.py` | Đọc biến môi trường |
| `app/core/database.py` | Kết nối database (SQLAlchemy + psycopg) |
| `app/models/`, `app/repositories/`, `app/schemas/` | Bảng, truy vấn, dữ liệu vào/ra của API |
| `database/001_agent_schema.sql`, `database/check_agent_db.sql` | Tạo bảng và kiểm tra bảng trên Supabase |
| `scripts/try_gemini.py` | Gọi thử Gemini với key trong `.env` |
| `tests/` | Test tự động (không cần mạng, database hay Gemini thật) |
| `openapi.yaml` | Hợp đồng API của agent-service |

Các đường dẫn trên tính từ `services/agent-service/`.

## 3. Database

Database riêng của agent (`AGENT_DATABASE_URL`):

- `conversations`: mỗi phiên trò chuyện của một người dùng (`user_id` lấy từ JWT, không nối khóa ngoại sang user-service).
- `messages`: tin nhắn trong hội thoại, `role` là `user` hoặc `assistant`; tin nhắn `assistant` có `metadata` (`rag_chunk_ids`, `model`, `tokens_used`). Xóa hội thoại thì tin nhắn bị xóa theo.

Tạo bảng: Supabase (project của agent) → SQL Editor → chạy `database/001_agent_schema.sql`, rồi `database/check_agent_db.sql` (cột `ket_qua` phải toàn `OK`). Chạy lại nhiều lần không sao.

Vector DB (pgvector, bảng `documents`, `document_chunks`) **thuộc rag-service**, không nằm trong database của agent.

## 4. API

Đường dẫn khi chạy riêng service: `http://localhost:<port>/api/v1/agent/...`; qua Gateway: `http://localhost:8080/api/agent/...`. Mọi API (trừ health) cần header `Authorization: Bearer <access_token>`.

| Method | Đường dẫn | Việc |
|---|---|---|
| GET | `/agent/health` | Kiểm tra service còn chạy |
| POST | `/agent/conversations` | Tạo hội thoại (tiêu đề tùy chọn, bỏ trống thì tự sinh từ câu hỏi đầu) |
| GET | `/agent/conversations?page=1&page_size=20` | Danh sách hội thoại của tôi, mới nhất trước |
| GET | `/agent/conversations/{id}` | Chi tiết hội thoại kèm tin nhắn |
| DELETE | `/agent/conversations/{id}` | Xóa hội thoại và tin nhắn |
| POST | `/agent/conversations/{id}/messages` | Gửi câu hỏi `{"content": "..."}`, nhận câu hỏi đã lưu và câu trả lời của AI |
| POST | `/agent/chat` | Endpoint cũ (tìm thủ tục qua procedure-service), giữ nguyên |

Response luôn có dạng `{"data": ..., "error": null | {"code", "message"}, "request_id": "..."}`. Mã lỗi chính:

| HTTP | `error.code` | Khi nào |
|---|---|---|
| 401 | `UNAUTHORIZED`, `TOKEN_EXPIRED` | Thiếu, sai hoặc hết hạn token |
| 403 | `CONVERSATION_FORBIDDEN` | Hội thoại của người khác |
| 404 | `CONVERSATION_NOT_FOUND` | Không có hội thoại |
| 422 | `VALIDATION_ERROR` | Dữ liệu sai, `details` ghi rõ từng trường |
| 503 | `RAG_SERVICE_ERROR` | rag-service lỗi hoặc chưa sẵn sàng |
| 503 | `AI_SERVICE_ERROR` | Gemini lỗi hoặc quá tải |

Khi RAG hoặc Gemini lỗi, câu hỏi **không** được lưu để người dùng gửi lại. Mỗi lần gửi kèm tối đa 20 tin nhắn gần nhất làm ngữ cảnh.

Chi tiết đầy đủ: `services/agent-service/openapi.yaml`, hoặc trang Swagger `/docs` khi service đang chạy.

## 5. Cấu hình

Biến môi trường đặt trong `.env` ở **thư mục gốc** (không commit). Mẫu ở `.env.example`.

| Biến | Ý nghĩa |
|---|---|
| `GEMINI_API_KEY` | API key lấy ở https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | Tên model, hiện dùng `gemini-3.8-flash` (`gemini-2.5-flash` không còn mở cho tài khoản mới) |
| `AGENT_DATABASE_URL` | Chuỗi kết nối Supabase của agent |
| `RAG_SERVICE_URL` | Địa chỉ rag-service (Docker: `http://rag-service:8000`) |
| `INTERNAL_TOKEN` | Mật khẩu nội bộ giữa agent và rag-service, hai bên phải giống nhau |
| `JWT_PUBLIC_KEY_PATH` | Đường dẫn khóa công khai để kiểm tra token do user-service cấp |

Khi chạy bằng Docker, `docker-compose.yml` chỉ truyền vào container những biến khai báo trong khối `environment:` của `agent-service`; thêm biến mới thì phải khai báo ở đó.

## 6. Cài đặt và chạy

Cài lần đầu (PowerShell, ở thư mục gốc):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-test.txt
```

Nếu báo "running scripts is disabled": chạy `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` một lần.

Chạy riêng agent-service trên máy:

```powershell
cd services/agent-service
uvicorn app.main:app --reload --port 8001 --env-file ../../.env
```

Mở http://localhost:8001/docs để xem và gọi thử API. Token đăng nhập cho môi trường dev: `python services/user-service/scripts/dev_login.py --email <email>` (chạy ở thư mục gốc).

Chạy cả hệ thống bằng Docker (ở thư mục gốc): `docker compose up --build`, rồi gọi qua Gateway `http://localhost:8080/api/agent/...`.

## 7. Dùng trong code

```python
from app.services import agent

result = await agent.process_message(
    user_content="Sang tên sổ đỏ cần gì?",
    history=[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
    request_id="req-123",
)
# result = {"content": "...", "metadata": {"rag_chunk_ids": [...], "model": "...", "tokens_used": 512}}
```

Gọi thẳng Gemini: `from app.services import llm_client` → `await llm_client.generate(system_prompt, history, user_content)` trả về `(answer, tokens)`.

## 8. Lưu ý bảo mật

- Không dán API key vào code, không commit `.env`, không chụp màn hình khi đang mở `.env`. Key lỡ lộ thì xóa và tạo key mới ngay.
- Ở gói miễn phí, Google có thể dùng dữ liệu gửi lên để cải thiện sản phẩm; khi test không gửi dữ liệu cá nhân thật, lên production nên bật billing.
- Bảng của agent bật RLS để chặn truy cập qua Supabase REST API; agent-service kết nối bằng user `postgres` nên không bị ảnh hưởng.

## Nhật ký thực hiện

Ghi lại theo thứ tự những gì đã làm. **[Claude]** là việc Claude làm trực tiếp trên máy, **[Dũng]** là việc Dũng tự làm theo hướng dẫn.

### 2026-10-08

**Tìm hiểu dự án**
1. [Claude] Đọc mã nguồn `services/agent-service`: FastAPI, Python 3.11, chưa có chỗ nào gọi LLM. `app/services/agent.py`, `rag_client.py`, models, repositories, schemas đều đang TODO. Thiết kế ban đầu (`openapi.yaml`) định dùng OpenAI `gpt-4o-mini`.
2. [Claude] Đọc thư mục gốc: biến môi trường nằm ở `.env` gốc, `docker-compose.yml` chỉ truyền vào container các biến khai báo trong `environment:`. `requirements-test.txt` gom thư viện mọi service và `libs/common_auth`. Chưa có venv.

**Chuẩn bị**
3. [Dũng] Lấy API key ở Google AI Studio, điền `GEMINI_API_KEY` và `GEMINI_MODEL` vào `.env` gốc.
4. [Dũng] Thêm `google-genai>=1.0,<2` vào `services/agent-service/requirements.txt`.
5. [Dũng] Thêm `GEMINI_API_KEY`, `GEMINI_MODEL` vào khối `environment:` của `agent-service` trong `docker-compose.yml`.
6. [Dũng] Tạo nhánh riêng: `git checkout -b feature/gemini-agent` (không làm trên `main`).
7. [Dũng] Tạo venv ở thư mục gốc và cài thư viện:
   `python -m venv .venv`, `.\.venv\Scripts\Activate.ps1`, `pip install -r requirements-test.txt`.

**Code (Claude làm sau khi Dũng cho phép)**
8. [Claude] Đổi `docker-compose.yml` từ CRLF về LF (trình soạn thảo Windows đã đổi kiểu xuống dòng khiến git tưởng cả file bị sửa). Nội dung giữ nguyên.
9. [Claude] Thêm `GEMINI_API_KEY` (key giả) và `GEMINI_MODEL` vào `.env.example` gốc và `services/agent-service/.env.example`. `.env.example` gốc cũng được đổi về LF như bản trong git.
10. [Claude] Thêm `gemini_api_key`, `gemini_model` vào `Settings` trong `services/agent-service/app/core/config.py`.
11. [Claude] Tạo `services/agent-service/app/services/llm_client.py`: hàm async `generate()` gọi Gemini, ném `LLMError` khi lỗi.
12. [Claude] Tạo `services/agent-service/scripts/try_gemini.py`: đọc `.env` gốc rồi gọi thử `llm_client`.
13. [Claude] Tạo file này (`README-GEMINI.md`). `README.md` của nhóm giữ nguyên, không sửa.

**Chạy thử**
14. [Dũng] Chạy `python services/agent-service/scripts/try_gemini.py`:
    - Lần 1: lỗi `404 NOT_FOUND`, `gemini-2.5-flash` không còn mở cho tài khoản mới. Key vẫn hợp lệ.
    - [Dũng] Đổi `GEMINI_MODEL=gemini-3.8-flash` trong `.env` gốc.
    - Lần 2: lỗi `503 UNAVAILABLE`, Google quá tải tạm thời, không phải lỗi code.
    - Lần 3: **thành công**, Gemini trả lời tiếng Việt (901 tokens).
15. Nhận xét: câu trả lời còn nhắc "cấp huyện" (đã bỏ từ 1/7/2025), vì model trả lời theo kiến thức chung. Đây là lý do cần RAG để trả lời theo văn bản luật mới nhất.

**Tìm hiểu rag-service**
16. [Claude] Đọc `services/rag-service`: do thành viên khác phụ trách, hiện chỉ có `/api/v1/rag/search` trả dữ liệu mẫu. Endpoint nội bộ `/internal/v1/rag/search` (agent cần gọi) còn TODO, nhưng hợp đồng API đã có trong `services/rag-service/openapi.yaml`: trả về `data.chunks[]` gồm `content`, `score`, `section_info` (điều, khoản), `document` (tên luật, số hiệu). rag-service đang dùng thư viện `openai` để tạo embedding, nhóm cần quyết định có đổi sang Gemini không.

**RAG client, agent và test (Claude làm sau khi Dũng cho phép)**
17. [Claude] Viết `services/agent-service/app/services/rag_client.py`: hàm async `search()` gọi `POST /internal/v1/rag/search` của rag-service kèm `X-Internal-Token` và `X-Request-ID`, đọc `data.chunks` đúng hợp đồng (code mẫu cũ đọc nhầm `data.results`). Cắt câu hỏi còn tối đa 1000 ký tự vì rag-service chỉ nhận 1–1000. Mọi lỗi (timeout, không kết nối, mã lỗi HTTP, JSON sai) đều đổi thành `RAGError`.
18. [Claude] Viết `services/agent-service/app/services/agent.py`: hàm `process_message(user_content, history, request_id)` lấy chunks từ RAG, ghép thành system prompt có trích dẫn dạng `[1] Khoản 1 Điều 188, Luật Đất đai 2024 (31/2024/QH15)`, rồi gọi Gemini. Prompt yêu cầu chỉ trả lời theo trích đoạn và nói rõ khi chưa đủ căn cứ. Trả về `content` và `metadata` (`rag_chunk_ids`, `model`, `tokens_used`) đúng mẫu `openapi.yaml`. Khi RAG lỗi thì không gọi Gemini. Việc lưu DB để endpoint làm sau.
19. [Claude] Viết test trong `services/agent-service/tests/`: `conftest.py` (cấu hình giả, không đọc key thật), `test_rag_client.py` (giả lập rag-service bằng `httpx.MockTransport`), `test_agent.py` (giả lập cả rag-service và Gemini). Không gọi mạng, không tốn quota Gemini.
20. [Claude] Chạy test: **16/16 đạt**. Cách chạy (ở thư mục gốc, đã bật venv):
    `pytest services/agent-service/tests -q`
    Test agent-service chưa được thêm vào `.gitlab-ci.yml` (CI hiện chỉ chạy `tests`, `libs/common_auth`, `services/user-service/tests`).

**Chạy test trên laptop (VS Code)**
21. [Claude] Kiểm tra `.venv` trên máy Dũng: Python 3.14.0, đã có `pytest` 8.4.2, `httpx`, `google-genai`, `pydantic-settings`, `common_auth`. Không cần cài thêm.
22. [Dũng] Chạy test trong terminal VS Code:
    1. VS Code: **File → Open Folder** → chọn `B:\AI_03\BE\legal-assistant-ai`.
    2. **Ctrl+Shift+P** → `Python: Select Interpreter` → chọn `.\.venv\Scripts\python.exe`.
    3. Mở terminal: **Ctrl+`** (chọn PowerShell). Nếu đầu dòng chưa có `(.venv)` thì chạy `.\.venv\Scripts\Activate.ps1`.
    4. Chạy `pytest services/agent-service/tests -v`. Kết quả đúng: `16 passed`.
    5. (Tuỳ chọn) Chạy `python services/agent-service/scripts/try_gemini.py` để gọi Gemini thật.
    Lưu ý: chạy đúng đường dẫn `services/agent-service/tests`, không chạy `pytest` trống, vì test gốc của nhóm (`tests/`) nạp lại module `app` của nhiều service và có thể đụng nhau.

23. [Dũng] Đổi API key Gemini vì key cũ bị lộ trong ảnh chụp màn hình: xoá key cũ ở https://aistudio.google.com/apikey, tạo key mới, cập nhật `GEMINI_API_KEY` trong `.env` gốc. Lần sau chụp màn hình thì đóng tab `.env` trước.

24. [Dũng] Chạy `pytest services/agent-service/tests -v` trên laptop (Windows, Python 3.14): **16 passed**, 1 warning. Warning là `DeprecationWarning` bên trong thư viện `google-genai` (dùng `_UnionGenericAlias`, sẽ bị bỏ ở Python 3.17), không phải lỗi code của dự án; sẽ hết khi Google cập nhật thư viện.

25. [Claude] Đọc cách nhóm dựng database (user-service, `database/README.md`, rag-service) và lập kế hoạch database + vector DB, xem mục **Kế hoạch: database và vector DB** ở cuối file. Chưa sửa file code nào.

**Phần A: database và endpoint hội thoại của agent-service (Claude làm sau khi Dũng cho phép)**
26. [Claude] Tạo `services/agent-service/database/001_agent_schema.sql`: bảng `conversations` (id, user_id, title, created_at, updated_at) và `messages` (id, conversation_id → `on delete cascade`, role `user`/`assistant`, content, metadata JSONB, created_at), 2 index, bật RLS để chặn truy cập qua Supabase REST API. Chạy lại nhiều lần không sao.
27. [Claude] Tạo `services/agent-service/database/check_agent_db.sql` (chỉ đọc, cột `ket_qua` phải toàn `OK`) và viết lại `services/agent-service/database/README.md`.
28. [Claude] Thêm `psycopg[binary]>=3.1,<4` vào `services/agent-service/requirements.txt` (driver PostgreSQL giống user-service; venv của Dũng đã có sẵn nên không cần cài thêm).
29. [Claude] Tạo `app/core/database.py` (SQLAlchemy đồng bộ + psycopg, cùng cách với user-service, đọc `AGENT_DATABASE_URL`), `app/models/conversation.py`, `app/models/message.py`, `app/models/__init__.py`.
30. [Claude] Viết `app/repositories/conversation.py`: tạo / liệt kê (phân trang, mới nhất trước) / xem / xóa hội thoại, lấy tin nhắn, lưu cặp câu hỏi + câu trả lời trong 1 transaction, tự sinh tiêu đề từ câu hỏi đầu (tối đa 80 ký tự).
31. [Claude] Viết `app/schemas/conversation.py`, `app/schemas/message.py` theo `openapi.yaml`.
32. [Claude] Viết 5 endpoint trong `app/api/v1/conversations.py` và đăng ký router trong `app/main.py`:
    - `POST /api/v1/agent/conversations` tạo hội thoại (201)
    - `GET /api/v1/agent/conversations?page=&page_size=` danh sách của tôi
    - `GET /api/v1/agent/conversations/{id}` chi tiết kèm tin nhắn
    - `DELETE /api/v1/agent/conversations/{id}` xóa (kèm tin nhắn)
    - `POST /api/v1/agent/conversations/{id}/messages` gửi câu hỏi → RAG + Gemini → lưu cả 2 tin nhắn
    Quy tắc: hội thoại không tồn tại → 404 `CONVERSATION_NOT_FOUND`; của người khác → 403 `CONVERSATION_FORBIDDEN`; RAG lỗi → 503 `RAG_SERVICE_ERROR`; Gemini lỗi → 503 `AI_SERVICE_ERROR`, và khi lỗi thì **không lưu gì** để người dùng gửi lại được. Gửi kèm tối đa 20 tin nhắn gần nhất làm ngữ cảnh cho Gemini.
33. [Claude] Sửa nhỏ 2 file có sẵn:
    - `app/api/deps.py`: thiếu token trả 401 `UNAUTHORIZED` đúng openapi (trước đây FastAPI tự trả 403).
    - `app/main.py`: lỗi validation trả thông báo chung "Dữ liệu gửi lên không hợp lệ." kèm `details` theo từng trường (trước đây luôn báo "message phải có 1–2000 ký tự." cho mọi endpoint). Endpoint `/api/v1/agent/chat` cũ giữ nguyên.
34. [Claude] Viết test: `tests/test_repositories.py` (SQLite trong RAM), `tests/test_conversations_api.py` (JWT giả, RAG + Gemini giả lập), thêm fixture `db` trong `tests/conftest.py`. Kết quả trên máy Claude: **37/37 đạt** test agent-service, và 8/8 test chung của nhóm liên quan agent (`tests/test_api.py`, `tests/test_openapi.py`) vẫn đạt.

35. [Dũng] Chạy test agent-service trên laptop (Windows, Python 3.14): **37 passed**, 2 warning (đều từ thư viện `google-genai` và `starlette`, không phải lỗi dự án). Chạy `try_gemini.py`: lỗi `503 UNAVAILABLE`, Google quá tải tạm thời (lần thứ 2 gặp lỗi này).

36. [Dũng] Tạm hoãn 4 đề xuất (database SQLite để chạy thử, `dev_token.py`, sửa `database.py` cho SQLite, tự thử lại khi Gemini báo 503), chuyển sang phần tiếp theo (Phần B: vector DB).

37. [Claude] Chạy thử `services/agent-service/database/001_agent_schema.sql` (2 lần) và `check_agent_db.sql` trên PostgreSQL 16 thật ở máy Claude: toàn `OK`. Chạy thử code repositories của agent với PostgreSQL thật (tạo hội thoại, lưu tin nhắn có metadata JSONB, xóa kèm tin nhắn): đúng.
38. [Claude] Phần B, mặc định theo đề xuất vì chưa có câu trả lời: soạn **bản nháp** vector DB cho rag-service, embedding **Gemini 768 chiều**, chưa đặt vào thư mục rag-service trên máy Dũng:
    - `001_rag_schema.sql`: bật `pgvector`, bảng `documents`, `document_chunks` (`embedding vector(768)`), index HNSW cosine, RLS, câu truy vấn mẫu cho `/internal/v1/rag/search`.
    - `check_rag_db.sql`: kiểm tra extension, bảng, số chiều, index, cascade.
    - `GHI_CHU_CHO_RAG_SERVICE.md`: giải thích để gửi bạn phụ trách rag-service (cách tạo embedding Gemini, việc còn lại của rag-service).
    Đã chạy thử trên PostgreSQL 16 + pgvector ở máy Claude: chạy 2 lần không lỗi, kiểm tra toàn `OK`, tìm kiếm cosine và xóa cascade đúng.

### 2026-10-09

39. [Claude] Theo yêu cầu của Dũng, tạo `TONG_QUAN_DU_AN.md` ở thư mục gốc: tài liệu cho người mới vào dự án, gồm dự án làm gì, kiến trúc, bảng service và người phụ trách, cấu trúc thư mục, quy ước nhóm, những gì đã làm, việc cần làm tiếp, cách bắt đầu trên máy, tài liệu nên đọc, lưu ý bảo mật. Không sửa `README.md` của nhóm.
40. [Claude] Kiểm tra lại phần rag-service mô tả trong `README-RAG.md` (không sửa file nào): 45 test rag-service đạt, 2 test pgvector đạt trên PostgreSQL 16 + pgvector thật, 37 test agent-service đạt, lệnh CI không thêm lỗi mới. Code khớp mô tả. Sai ở hướng dẫn Git mục 8.3: `git switch -c feature/rag-search develop` sẽ báo lỗi vì máy không có nhánh `develop` local, và commit `62fdfbd` (đã có khung rag-service) chưa lên `origin/develop`. Cách đúng: tạo `feature/rag-search` từ `feature/gemini-agent` sau khi commit phần agent.

### Trạng thái hiện tại

- Nhánh: `feature/gemini-agent`, **chưa commit gì** (toàn bộ thay đổi từ đầu vẫn nằm ở local).
- Tài liệu cho người mới: `TONG_QUAN_DU_AN.md` ở thư mục gốc.
- Gọi Gemini: chạy được. `rag_client.py`, `agent.py`: xong.
- Database agent-service: code và SQL xong, 37/37 test đạt trên máy Claude và laptop của Dũng. **Chưa tạo bảng trên Supabase** (bước A3, Dũng làm).
- rag-service thật chưa có `/internal/v1/rag/search`, nên gửi tin nhắn qua API thật sẽ trả 503 `RAG_SERVICE_ERROR` cho tới khi rag-service xong.
- Giá trị mặc định `gemini-2.5-flash` vẫn còn trong `config.py`, `docker-compose.yml` và hai file `.env.example`. Không ảnh hưởng khi chạy vì `.env` gốc đã đặt `gemini-3.8-flash`.
- Đang chờ Dũng:
  - A3: chạy `001_agent_schema.sql` và `check_agent_db.sql` trên Supabase của agent.
  - Chạy `pytest services/agent-service/tests -v` trên laptop (kỳ vọng `37 passed`).
  - Đổi API key Gemini (key cũ bị lộ trong ảnh chụp màn hình).
  - Quyết định: đổi giá trị mặc định model; tự thử lại khi gặp lỗi 503; thêm test agent-service vào `.gitlab-ci.yml`; đổi `x-status` của 5 endpoint trong `openapi.yaml` từ `planned` sang `implemented`.

## Kế hoạch: database và vector DB

### Hiện trạng (đã kiểm tra 2026-10-08)

- Quy ước nhóm (`database/README.md`): **mỗi service một database riêng**, không dùng chung bảng, không đọc database của nhau.
- Mỗi service dùng một project **Supabase (PostgreSQL)** riêng. Trong `.env` gốc đã có `AGENT_DATABASE_URL` và `RAG_DATABASE_URL`.
- Cách nhóm tạo bảng (theo user-service): viết file SQL chạy lại nhiều lần không sao (`001_..._schema.sql`), chạy trong **Supabase → SQL Editor**, kèm file `check_..._db.sql` để kiểm tra. Code dùng SQLAlchemy.
- **Vector DB thuộc rag-service** (thành viên khác phụ trách): bảng `documents`, `document_chunks` có cột `embedding` dùng extension **pgvector**, nằm trong database của `RAG_DATABASE_URL`. Hiện mới có model mẫu (comment), dùng embedding OpenAI 1536 chiều.

### Phần A: Database của agent-service (lưu hội thoại)

Database: `AGENT_DATABASE_URL`.

| Bước | Ai làm | Việc |
|---|---|---|
| A1 ✓ | Claude | `services/agent-service/database/001_agent_schema.sql`: bảng `conversations` (id, user_id, title, created_at, updated_at; xóa là xóa hẳn theo openapi) và `messages` (id, conversation_id, role `user`/`assistant`, content, metadata JSONB, created_at), kèm index theo `user_id` và `conversation_id`. |
| A2 ✓ | Claude | `services/agent-service/database/check_agent_db.sql`: kiểm tra bảng/cột, cột kết quả phải toàn `OK`. Cập nhật `database/README.md` của agent-service. |
| A3 | Dũng | Supabase của agent → SQL Editor → chạy `001_agent_schema.sql`, rồi `check_agent_db.sql`. |
| A4 ✓ | Claude | `app/core/database.py` (SQLAlchemy đồng bộ + psycopg như user-service), `app/models/conversation.py`, `app/models/message.py`. |
| A5 ✓ | Claude | `app/repositories/conversation.py`: tạo / liệt kê / xem / xoá hội thoại, thêm và lấy tin nhắn; luôn lọc theo `user_id` lấy từ JWT. |
| A6 ✓ | Claude | `app/schemas/` và 5 endpoint trong `app/api/v1/conversations.py` theo `openapi.yaml`; đăng ký router vào `main.py`. Endpoint gửi tin nhắn gọi `agent.process_message`, đổi `RAGError`/`LLMError` thành 503. |
| A7 ✓ | Claude | Test repositories và endpoint (database SQLite tạm trong RAM, giả lập RAG và Gemini). |
| A8 | Dũng | Chạy test trên laptop, chạy thử service bằng `uvicorn` với database Supabase thật. |

### Phần B: Vector DB của rag-service

Database: `RAG_DATABASE_URL`. **Cần thành viên phụ trách rag-service đồng ý trước khi làm.**

| Bước | Ai làm | Việc |
|---|---|---|
| B1 | Nhóm | Chọn model embedding: giữ OpenAI (`text-embedding-3-small`, 1536 chiều) hoặc đổi sang Gemini embedding (đề xuất 768 chiều để nhẹ). Số chiều phải cố định trước khi tạo bảng. |
| B2 | Claude | `services/rag-service/database/001_rag_schema.sql`: `create extension vector`, bảng `documents` (theo `LegalDocument` trong openapi: title, doc_type, doc_number, issuing_body, issued_date, effective_date, status, source_url, ...) và `document_chunks` (document_id, chunk_index, content, section_info JSONB, embedding `vector(N)`, token_count), index **HNSW** cosine trên `embedding`. |
| B3 | Claude | `check_rag_db.sql` để kiểm tra extension, bảng và index. |
| B4 | Dũng | Supabase của rag → SQL Editor → chạy hai file trên. |
| B5 | Thành viên rag-service | Code nạp văn bản, cắt chunk, tạo embedding, và endpoint `/internal/v1/rag/search` (truy vấn `ORDER BY embedding <=> :query_vector LIMIT top_k`). |
