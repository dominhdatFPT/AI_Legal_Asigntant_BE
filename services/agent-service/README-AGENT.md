# README-AGENT: agent-service
Cập nhật: 24/10/2026 | Nhánh: `develop` | Test: 221 đạt

## 1. Service này làm gì

agent-service là **trợ lý hỏi đáp pháp luật**: nhận câu hỏi, chọn nhánh xử lý, trả câu trả lời. Service **không tự giữ kiến thức luật**: đoạn luật lấy từ rag-service, câu chữ do LLM (Groq / Gemini / OpenRouter) viết dựa trên đoạn luật đó.

| Chức năng | Endpoint | File chính |
|---|---|---|
| Chatbox công khai (không đăng nhập, không lưu), kèm gợi ý thủ tục | `POST /api/v1/agent/chat` | `app/main.py` |
| Tạo / liệt kê / xem / xóa hội thoại (cần đăng nhập) | `/api/v1/agent/conversations[/{id}]` | `app/api/v1/conversations.py` |
| Gửi tin nhắn, nhận câu trả lời, lưu lịch sử | `POST /conversations/{id}/messages` | `conversations.py` + `app/services/agent.py` |
| Trả nguyên văn Điều/Khoản, không qua AI | (nhánh trong `process_message`) | `app/services/article_lookup.py` |
| Chặn câu gõ bừa | (nhánh trong `process_message`) | `app/services/query_filter.py` |
| Rà soát tài liệu / hợp đồng đính kèm (`document_ids`) | `POST /conversations/{id}/messages` | `contract_review.py`, `document_client.py` |
| Health check | `GET /api/v1/agent/health` | `app/main.py` |

Gateway chuyển `/api/agent/...` thành `/api/v1/agent/...` (nginx, `proxy_read_timeout 120s`). Hợp đồng đầy đủ: `openapi.yaml`.

## 2. Quy tắc bắt buộc (KHÔNG được phá)

- Mỗi service một DB, không query DB service khác (`conversations.user_id` không có khóa ngoại sang user-service).
- Không gọi Gemini từ client, API key chỉ ở server.
- Không đổi hợp đồng API mà không sửa `openapi.yaml` của cả 2 service (agent và rag).
- Test không được gọi mạng thật (rag / LLM / document-service / DB đều mock).
- Gọi rag-service chỉ bằng header `X-Internal-Token`; gateway **không** mở `/internal/...` ra ngoài.
- Gọi document-service bằng **token của chính người dùng** (không dùng token nội bộ), để document-service tự kiểm tra chủ file.
- Câu trả lời pháp lý chỉ dựa trên đoạn luật rag trả về; không để LLM tự bịa số Điều (xem `SYSTEM_PROMPT`).

## 3. Luồng xử lý chính (file:hàm)

Hỏi đáp trong hội thoại:
1. `api/v1/conversations.py:send_message` kiểm tra quyền (`_owned_conversation`), lấy 6 tin nhắn gần nhất (`_load_for_chat`, `HISTORY_LIMIT`).
2. `conversations.py:_load_documents` (chỉ khi có `document_ids`) gọi `services/document_client.py:get_extractions`, **trước** khi tốn lượt RAG/LLM.
3. `services/agent.py:process_message` chọn nhánh:
   - `query_filter.py:is_meaningless` -> trả `DEFAULT_ANSWER`, hết (bỏ qua bước này nếu có tài liệu).
   - `article_lookup.py:parse` -> `rag_client.py:get_article` -> `format_article`, KHÔNG gọi AI (bỏ qua nếu có tài liệu).
   - Có tài liệu: `contract_review.py:detect_contract_type` + `review_queries` -> `agent.py:search_for_review` (nhiều câu tìm song song, `merge_chunks`) -> prompt `REVIEW_SYSTEM_PROMPT`, `metadata.mode = contract_review`.
   - Còn lại: `rag_client.py:search` (`top_k = RAG_TOP_K`) -> `agent.py:build_system_prompt`.
4. `services/llm_client.py:generate` thử lần lượt các provider trong `LLM_PROVIDER`.
5. `repositories/conversation.py:add_message_pair` lưu câu hỏi + trả lời trong 1 transaction. Bất kỳ bước 2-4 lỗi thì **không lưu gì**, trả 503 (hoặc 404/409 cho tài liệu).

Chatbox công khai: `main.py:chat` chạy song song `find_procedure` (procedure-service) và `process_message` (không history, không tài liệu). AI/rag lỗi mà vẫn có thủ tục thì trả 200 với `answer = "Đã tìm thấy thủ tục phù hợp."`.

Metadata tin nhắn trả lời: `{rag_chunk_ids, model, tokens_used}`; nhánh nguyên văn thêm `type: article_lookup`; nhánh rà soát thêm `mode: contract_review`, `contract_type`; lượt có tài liệu thêm `document_ids` (không lưu nội dung tài liệu).

## 4. Bản đồ file

| File | Trách nhiệm |
|---|---|
| `app/main.py` | FastAPI app, `/health`, `/chat`, `find_procedure`, handler lỗi khung `{data,error,request_id}` |
| `app/api/v1/conversations.py` | 5 endpoint hội thoại, gửi tin nhắn, map lỗi tài liệu/RAG/LLM sang mã HTTP |
| `app/api/deps.py` | `get_current_user` (Bearer, dự phòng cookie `access_token`), `raw_access_token` |
| `app/core/auth.py` | Verify JWT RS256 bằng public key |
| `app/core/config.py` | Toàn bộ cấu hình (`Settings`) |
| `app/core/database.py` | Engine/session SQLAlchemy cho `AGENT_DATABASE_URL` |
| `app/services/agent.py` | `process_message`, `SYSTEM_PROMPT`, `REVIEW_SYSTEM_PROMPT`, `build_system_prompt`, `format_chunk`, `format_documents`, `search_for_review` |
| `app/services/query_filter.py` | Nhận diện câu vô nghĩa (đa số từ không phải âm tiết tiếng Việt) |
| `app/services/article_lookup.py` | `parse` nhận diện câu tra cứu nguyên văn, `format_article` |
| `app/services/contract_review.py` | Nhận diện loại hợp đồng (`CONTRACT_PATTERNS`), câu tìm luật theo loại (`REVIEW_QUERIES`), `merge_chunks` |
| `app/services/rag_client.py` | Gọi rag-service: `search`, `get_article`; `RAGError`, `ArticleNotFound` |
| `app/services/document_client.py` | Lấy chữ tài liệu từ document-service; `DocumentNotFound/NotReady/ServiceError` |
| `app/services/llm_client.py` | Gọi Groq / Gemini / OpenRouter, thử lại, chuyển provider; `LLMError` |
| `app/repositories/conversation.py` | CRUD hội thoại và tin nhắn, `add_message_pair`, `make_title` |
| `app/models/`, `app/schemas/` | `Conversation`, `Message`; schema request/response (`SendMessageRequest` có `document_ids`) |
| `database/001_agent_schema.sql`, `check_agent_db.sql` | Schema Supabase và script kiểm tra |
| `scripts/try_groq.py`, `try_gemini.py` | Chạy thử LLM thật qua `llm_client` |
| `openapi.yaml` | Hợp đồng API |

## 5. Dữ liệu

- `conversations`: `id`, `user_id` (JWT `sub`), `title` (null tới câu hỏi đầu, tự sinh tối đa 80 ký tự), `created_at`, `updated_at`. Index `(user_id, updated_at desc)`.
- `messages`: `id`, `conversation_id` (xóa hội thoại thì xóa theo), `role` (`user`/`assistant`), `content`, `metadata` JSONB (thuộc tính ORM tên `meta`), `created_at`. Index `(conversation_id, created_at)`.
- Chỉ hội thoại có đăng nhập mới lưu; `/chat` không lưu gì.
- Đổi schema: sửa `database/001_agent_schema.sql` (chạy lại nhiều lần được, dán vào Supabase SQL Editor) + `app/models/` + `check_agent_db.sql`. Thư mục `alembic/` mới chỉ có README, chưa có migration.

## 6. Cấu hình

Giá trị mặc định lấy từ `app/core/config.py`. Trong Docker chỉ các biến có ở cột "Compose" mới được truyền; biến khác luôn dùng mặc định trừ khi thêm vào `environment` của agent-service trong `docker-compose.yml`.

| Biến | Mặc định | Compose | Dùng ở đâu |
|---|---|---|---|
| `RAG_SERVICE_URL` | `http://rag-service:8000` | cố định | `rag_client._post` |
| `INTERNAL_TOKEN` | trống | có | header `X-Internal-Token`, khớp rag-service |
| `RAG_TIMEOUT_SECONDS` | `30` | có | timeout gọi rag |
| `RAG_TOP_K` | `4` | không | `agent.process_message`, `search_for_review` |
| `DOCUMENT_SERVICE_URL` | `http://document-service:8000` | có | `document_client` |
| `PROCEDURE_SERVICE_URL` | `http://procedure-service:8000` | có | `main.find_procedure` (đọc bằng `os.getenv`) |
| `JWT_PUBLIC_KEY_PATH` | `keys/jwt_public.pem` | có | `core/auth.py` |
| `AGENT_DATABASE_URL` | trống | có | `core/database.py` |
| `LLM_PROVIDER` | `openrouter,gemini` | có | `llm_client.generate`: một provider (`openrouter`, `groq`, `gemini`) hoặc danh sách ưu tiên |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | trống / `gemini-3.8-flash` | có | `_generate_gemini` |
| `GROQ_API_KEY` / `GROQ_MODEL` | trống / `qwen/qwen3.8-27b` | có (mặc định compose: `llama-3.3-70b-versatile`) | `_generate_groq` |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` / `OPENROUTER_BASE_URL` | trống / trống (bắt buộc đặt) / `https://openrouter.ai/api/v1` | có | `_generate_openrouter` (SDK `openai`); thiếu key hoặc model thì báo lỗi ngay, không gọi mạng |
| `OPENROUTER_SITE_URL` / `OPENROUTER_APP_NAME` | trống | có | tuỳ chọn, gửi header `HTTP-Referer` / `X-Title` khi có giá trị |
| `OPENROUTER_REASONING_MAX_TOKENS` | `1024` | có | token chừa cho phần suy nghĩ của model suy luận (vd `recursive/flint-v1.0:free`, tính vào `max_tokens`); `0` = không gửi |
| `GEMINI_`/`GROQ_` `TIMEOUT_SECONDS`, `MAX_RETRIES` | `15`, `1` | không | từng provider |
| `OPENROUTER_TIMEOUT_SECONDS`, `OPENROUTER_MAX_RETRIES` | `30`, `1` | không | `_generate_openrouter` |
| `LLM_MAX_OUTPUT_TOKENS` | `800` | không | hỏi đáp thường |
| `LLM_REVIEW_MAX_OUTPUT_TOKENS` / `LLM_REVIEW_TIMEOUT_SECONDS` | `2000` / `40` | không | rà soát tài liệu (không thử lại cùng provider) |

Đổi `.env` xong phải `docker compose up -d --force-recreate agent-service` (`restart` không nạp lại). Compose mặc định `GROQ_MODEL` khác mặc định trong code: nếu `.env` không đặt thì chạy `llama-3.3-70b-versatile`.

## 7. Phụ thuộc service khác

| Service | Endpoint | Timeout | Lỗi thì xử lý |
|---|---|---|---|
| rag-service | `POST /internal/v1/rag/search` | 30s (`RAG_TIMEOUT_SECONDS`) | `RAGError` -> 503 `RAG_SERVICE_ERROR`; không lưu tin nhắn Circuit breaker (`rag_client._post`, cho cả `search` và `articles`): 3 lần lỗi liên tiếp (timeout, lỗi kết nối, HTTP 5xx) thì 30 giây không gửi HTTP, ném ngay `RAGError` như khi rag lỗi; một lần phản hồi < 500 thì đếm lại từ 0; đếm theo từng process |
| rag-service | `POST /internal/v1/rag/articles` | 30s | 404/409 -> `ArticleNotFound`, hiện nguyên câu báo của rag cho người dùng (không phải lỗi); lỗi khác -> 503 `RAG_SERVICE_ERROR` |
| LLM (Groq / Gemini / OpenRouter) | SDK | 15s mỗi provider (OpenRouter 30s), thử lại 1 lần khi 429/500/503/504/timeout; rà soát 40s, không thử lại | Provider lỗi -> thử provider kế; tất cả lỗi -> `LLMError` -> 503 `AI_SERVICE_ERROR` |
| procedure-service | `POST /api/v1/procedures/search` (chỉ `/chat`) | 5s | Bỏ qua, `procedure = null` |
| document-service | `GET /api/v1/documents/{id}/extraction` (chỉ khi có `document_ids`) | 10s | 401/403/404 -> 404 `DOCUMENT_NOT_FOUND` (không phân biệt để không lộ file người khác); 409 -> 409 `DOCUMENT_NOT_READY`; còn lại -> 503 `DOCUMENT_SERVICE_ERROR` |
| Supabase (agent DB) | `AGENT_DATABASE_URL` | | Truy vấn đồng bộ, chạy trong threadpool |

Gateway chờ agent tối đa 120s. Rà soát tài liệu tệ nhất: rag + 40s x số provider (không thử lại) nên nằm trong ngưỡng này với 2 provider.

## 8. Công thức thêm tính năng

- **Thêm endpoint:** schema (`app/schemas/`) -> repository (`app/repositories/`) -> api (`app/api/v1/`) -> test -> `openapi.yaml`.
- **Thêm nhánh xử lý mới trong agent:** thêm bước trong `agent.process_message` đặt **trước** bước RAG (nhánh nào không cần AI thì trả sớm, như `article_lookup`); trả đúng dạng `{"content", "metadata"}` và `metadata` có `rag_chunk_ids`, `model`, `tokens_used`; thêm test vào `tests/test_agent.py`; ghi `type`/`mode` vào `metadata` và `openapi.yaml` (schema `MessageMetadata`).
- **Thêm loại hợp đồng cho rà soát:** thêm mẫu vào `CONTRACT_PATTERNS` và 3-4 khía cạnh vào `REVIEW_QUERIES` (`contract_review.py`), thêm case vào `tests/test_contract_review.py`.
- **Thêm provider LLM:** viết `_generate_<tên>` trong `llm_client.py` (nhận `max_output_tokens`, `timeout_seconds`, `max_retries`, trả `(text, tokens)`), thêm nhánh `elif` trong `generate`, thêm biến vào `config.py`, `.env.example`, `docker-compose.yml`, test trong `test_llm_client.py`.
- **Đổi câu dặn LLM:** sửa `SYSTEM_PROMPT` / `REVIEW_SYSTEM_PROMPT` đầu `agent.py`; dòng chống prompt injection dùng chung ở `INJECTION_RULE`.

## 9. Debug nhanh

| Triệu chứng | Xem log nào | File nghi ngờ |
|---|---|---|
| Trả lời chậm | `agent timing request_id=... rag=..s llm=..s` (agent) rồi `rag timing embed=..s db=..s` (rag) | `rag_client.py`, rag-service `search.py`, `repositories/document.py` |
| 503 `RAG_SERVICE_ERROR` | log `RAG lỗi, request_id=...`; nếu có `rag-service tạm ngắt do lỗi liên tiếp` thì breaker đang mở, không còn gửi HTTP | `INTERNAL_TOKEN` hai bên khớp chưa, rag còn chạy, `RAG_TIMEOUT_SECONDS`, DB rag; breaker ở `rag_client.py` (`_FAIL_LIMIT`, `_OPEN_SECONDS`) |
| 503 `AI_SERVICE_ERROR` | `Provider 'groq' thất bại` | hết quota / sai khóa / timeout; `llm_client.py` |
| Câu hỏi lại ra "chưa hiểu câu hỏi" | `agent bỏ qua câu vô nghĩa` | `query_filter.py` |
| "Điều 45 Luật Đất đai" vẫn đi qua AI | không có log `agent tra cứu nguyên văn` | `article_lookup.parse` (có từ lạ trong câu), hoặc request có `document_ids` |
| Rà soát ra chung chung | `agent rà soát ... contract_type=khac` | `contract_review.detect_contract_type`, `REVIEW_QUERIES` |
| Tài liệu đính kèm bị 409 | trả `DOCUMENT_NOT_READY` | document-service chưa trích xong; không có worker thì file kẹt `queued` (`docker-compose.document-worker.yml`) |
| Đổi `.env` không có tác dụng | | phải `--force-recreate`; kiểm tra biến có trong `environment` của compose |

## 10. Chạy và test

```powershell
# Ở thư mục gốc legal-assistant-ai, dùng .venv của repo (chạy đúng đường dẫn này, không chạy pytest trống)
.venv\Scripts\python -m pytest services/agent-service/tests -q
docker compose up -d --force-recreate agent-service
docker compose logs -f agent-service
python services/agent-service/scripts/try_groq.py      # chạy thử LLM thật, cần khóa trong .env
python services/agent-service/scripts/try_openrouter.py   # chỉ OpenRouter, in rõ lỗi (sai key, hết credit, 429...), không in khóa
```
Thử qua gateway: `POST http://localhost:8080/api/agent/chat` body `{"message": "Sang tên sổ đỏ cần giấy tờ gì?"}`.

OpenRouter: `.env` cần `OPENROUTER_API_KEY` và `OPENROUTER_MODEL`. Kết quả 200 có `data.answer`; nếu OpenRouter lỗi, log có dòng `Provider 'openrouter' thất bại: <lý do>` rồi Gemini trả lời thay. Model `recursive/flint-v1.0:free` bắt buộc suy luận nên cần `OPENROUTER_REASONING_MAX_TOKENS`, thiếu thì câu trả lời bị cắt hoặc rỗng. Lỗi 401 = sai key, 402 = hết credit, 429 = vượt giới hạn model free, chặn data policy: bật tại https://openrouter.ai/settings/privacy.

## 11. Hạn chế đã biết / nợ kỹ thuật

1. Câu hỏi nối tiếp không được viết lại khi tìm luật: `rag_client.search` chỉ nhận câu hiện tại, lịch sử chỉ đưa cho LLM ("thế còn thời hạn thì sao?" tìm kém). `agent.py`.
2. Chưa truyền `doc_types` sang rag-service; kho hiện chỉ có luật. `rag_client.py`.
3. Chất lượng trả lời phụ thuộc kho: rag chưa có nghị định, thông tư nên hỏi giấy tờ, hồ sơ, lệ phí hay ra "chưa đủ căn cứ" (xem README-RAG).
4. Tra cứu nguyên văn chưa hỗ trợ "điểm a khoản 1" và nhiều Điều trong 1 câu. `article_lookup.py`.
5. `DEFAULT_ANSWER` cố định nhắc "đất đai hoặc hộ kinh doanh". `query_filter.py`.
6. `/chat` công khai không giới hạn tần suất (không có `limit_req` ở `gateway/nginx.conf`), mỗi câu tốn lượt embedding và LLM.
7. Chưa trả lời dạng stream; người dùng chờ đủ rag + LLM.
8. Tài liệu đính kèm: cắt cứng `MAX_DOCUMENT_CHARS = 12000` ký tự (mất phần sau của tài liệu dài, chưa chia đoạn); chưa hỗ trợ Excel; tài liệu không tự mang sang lượt sau (client gửi lại `document_ids`); `/chat` công khai không hỗ trợ tài liệu. `agent.py`, `document_client.py`.
9. Nhận diện loại hợp đồng bằng từ khóa: hợp đồng đặt tên lạ hoặc viết tắt thành `khac`, rà soát dùng 300 ký tự đầu tài liệu làm câu tìm. `contract_review.py`.
10. Chống prompt injection trong tài liệu chỉ bằng lời dặn (`INJECTION_RULE`), chưa kiểm thử với mô hình thật.
11. Compose mặc định `GROQ_MODEL` khác mặc định code; compose truyền `RABBITMQ_URL` nhưng app code agent-service không dùng. `docker-compose.yml`.
12. `.env.example` đầu file còn trỏ "README-AGENT.md, mục 7" (cấu hình nay ở mục 6).

## 12. Sửa xong phải cập nhật gì

1. Đổi API / lỗi / metadata: sửa `openapi.yaml` của agent-service (và rag-service nếu đổi cách gọi rag); sửa mục 1, 3, 7 của file này.
2. Thêm / đổi biến cấu hình: `config.py`, `.env.example`, `docker-compose.yml` (nếu cần truyền vào container), mục 6.
3. Thêm / đổi file: mục 4.
4. Đổi schema DB: `database/001_agent_schema.sql`, `check_agent_db.sql`, model, mục 5.
5. Chạy test, ghi số test đạt + ngày + nhánh lên dòng đầu.
6. Gặp lỗi mới đáng nhớ: thêm vào mục 9; việc còn dang dở: mục 11.
