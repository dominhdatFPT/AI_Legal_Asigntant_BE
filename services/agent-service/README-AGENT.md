# README-AGENT: agent-service
Cập nhật: 28/10/2026 | Nhánh: `develop` | Test: 292 đạt, 0 lỗi

## 1. Service này làm gì

agent-service là **trợ lý hỏi đáp pháp luật**: nhận câu hỏi, chọn nhánh xử lý, trả câu trả lời. Service **không tự giữ kiến thức luật**: đoạn luật lấy từ rag-service, câu chữ do LLM (Groq / Gemini / OpenRouter) viết dựa trên đoạn luật đó.

| Chức năng | Endpoint | File chính |
|---|---|---|
| Chatbox công khai (không đăng nhập, không lưu), kèm gợi ý thủ tục | `POST /api/v1/agent/chat` | `app/main.py` |
| Tạo / liệt kê / xem / xóa hội thoại (cần đăng nhập) | `/api/v1/agent/conversations[/{id}]` | `app/api/v1/conversations.py` |
| Gửi tin nhắn, nhận câu trả lời, lưu lịch sử | `POST /conversations/{id}/messages` | `conversations.py` + `app/services/agent.py` |
| Như trên nhưng trả lời dạng stream (SSE): chữ hiện dần | `POST /conversations/{id}/messages/stream` | `conversations.py:send_message_stream`, `llm_client.py:generate_stream` |
| Chặn câu gõ bừa | (nhánh trong `process_message`) | `app/services/query_filter.py` |
| Chặn câu ngoài phạm vi kho luật (chấm độ liên quan, tắt mặc định `RELEVANCE_CHECK`) | (bước trong `qa_agent.answer`) | `app/services/relevance.py` |
| Rà soát tài liệu / hợp đồng đính kèm (`document_ids`) | `POST /conversations/{id}/messages` | `review_agent.py`, `contract_review.py`, `document_client.py` |
| Health check | `GET /api/v1/agent/health` | `app/main.py` |

Gateway chuyển `/api/agent/...` thành `/api/v1/agent/...` (nginx, `proxy_read_timeout 120s`). Hợp đồng đầy đủ: `openapi.yaml`.

## 2. Quy tắc bắt buộc (KHÔNG được phá)

- Mỗi service một DB, không query DB service khác (`conversations.user_id` không có khóa ngoại sang user-service).
- Không gọi Gemini từ client, API key chỉ ở server.
- Không đổi hợp đồng API mà không sửa `openapi.yaml` của cả 2 service (agent và rag).
- Test không được gọi mạng thật (rag / LLM / document-service / DB đều mock).
- Gọi rag-service chỉ bằng header `X-Internal-Token`; gateway **không** mở `/internal/...` ra ngoài.
- Gọi document-service bằng **token của chính người dùng** (không dùng token nội bộ), để document-service tự kiểm tra chủ file.
- Câu trả lời pháp lý chỉ dựa trên đoạn luật rag trả về; không để LLM tự bịa số Điều (xem `SYSTEM_PROMPT` trong `prompts.py`).

## 3. Luồng xử lý chính (file:hàm)

Hỏi đáp trong hội thoại:
1. `api/v1/conversations.py:send_message` kiểm tra quyền (`_owned_conversation`), lấy 6 tin nhắn gần nhất (`_load_for_chat`, `HISTORY_LIMIT`).
2. `conversations.py:_load_documents` (chỉ khi có `document_ids`) gọi `services/document_client.py:get_extractions`, **trước** khi tốn lượt RAG/LLM.
3. `services/agent.py:process_message` (bộ điều hướng, không gọi LLM) chọn nhánh:
   - `query_filter.py:is_meaningless` -> trả `DEFAULT_ANSWER`, hết (bỏ qua bước này nếu có tài liệu).
   - Có tài liệu: `review_agent.py:review`: `contract_review.py:detect_contract_type` + `review_queries` -> `review_agent.py:search_for_review` (nhiều câu tìm song song, `merge_chunks`) -> `prompts.py:build_review_prompt` (`REVIEW_SYSTEM_PROMPT`), `metadata.mode = contract_review`.
   - Còn lại: `qa_agent.py:answer`: `rag_client.py:search` (`top_k = RAG_TOP_K`) -> (chỉ khi `RELEVANCE_CHECK=true` và `LLM_BYPASS=false`) `relevance.py:grade` -> `prompts.py:build_qa_prompt`. `grade` nhờ LLM (`llm_client.generate`, `max_output_tokens=100`, chờ `RELEVANCE_TIMEOUT_SECONDS`, không thử lại) chọn các đoạn dùng được, trả JSON `{"relevant": [số thứ tự]}`: danh sách rỗng -> trả `OUT_OF_SCOPE_ANSWER` ngay, KHÔNG gọi LLM trả lời, `metadata.type = out_of_scope`; có danh sách -> chỉ các đoạn đó vào prompt và `rag_chunk_ids`; `None` (LLM lỗi, quá giờ, JSON hỏng, số thứ tự ngoài phạm vi, có log warning) -> giữ nguyên các đoạn. Không dùng ngưỡng điểm vì rag luôn trả đủ `top_k` và điểm đúng/sai sát nhau. Rà soát tài liệu không qua bước này. Câu dạng "Điều 45 Luật Đất đai" cũng đi nhánh này (không còn tra nguyên văn từ kho, bỏ ngày 28/10/2026).
4. `services/responder.py:respond` (hai agent dùng chung) gọi `services/llm_client.py:generate`, thử lần lượt các provider trong `LLM_PROVIDER`. Nếu `LLM_BYPASS=true` (chỉ để test) thì bước này bị bỏ: `respond` trả thẳng các đoạn luật rag tìm được (xem mục 10).
5. `repositories/conversation.py:add_message_pair` lưu câu hỏi + trả lời trong 1 transaction. Bất kỳ bước 2-4 lỗi thì **không lưu gì**, trả 503 (hoặc 404/409 cho tài liệu).

Hỏi đáp dạng stream (`conversations.py:send_message_stream`, `POST .../messages/stream`, cùng body và quyền với `send_message`):
1. Bước 1-2 như trên, rồi **chuẩn bị trước khi mở stream**: có tài liệu thì `agent.process_message` chạy cả nhánh rà soát như cũ (không stream, phát cả câu một `delta`); không có thì `agent.prepare` (câu vô nghĩa -> `query_filter`; còn lại `qa_agent.prepare`: rag + chấm độ liên quan). `qa_agent.answer` cũng gọi `qa_agent.prepare` rồi `LLMCall.run()` nên hai endpoint cho cùng kết quả. `prepare` trả dict `{"content","metadata"}` khi xong luôn (câu vô nghĩa, `out_of_scope`, `LLM_BYPASS`) hoặc `responder.LLMCall` (prompt + đoạn luật, chưa gọi LLM). Lỗi ở bước chuẩn bị (quyền, tài liệu, rag) trả JSON lỗi bình thường 401/403/404/409/503, chưa mở stream.
2. Mở `StreamingResponse` (`text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no` để nginx không gom, `X-Request-ID`). Sự kiện `event: <tên>` + `data: <JSON một dòng>`: `delta` `{"text"}` (mỗi mẩu chữ), `done` (`SendMessageResponse` như endpoint cũ), `error` `{"code","message"}`. Kết quả "xong luôn" phát cả câu trong một `delta`.
3. Cần LLM: `llm_client.generate_stream` đi theo thứ tự `LLM_PROVIDER` như `generate`, chỉ chuyển provider kế khi provider đó chưa phát mẩu nào; đã phát rồi mà lỗi thì ném `LLMError` -> sự kiện `error` (`AI_SERVICE_ERROR`). OpenRouter `stream=True` + `stream_options.include_usage`, Gemini `generate_content_stream`, Groq gọi `_generate_groq` rồi phát cả câu một lần. Mỗi mẩu (kể cả mẩu đầu, tính cả bước mở stream) chờ tối đa timeout của provider, không thử lại cùng provider. Xong thì `info` có `model`, `tokens`; `LLMCall.result` ghép metadata và ghi log `agent timing` (dùng chung với `respond`).
4. Chỉ sau khi stream xong đầy đủ mới `add_message_pair` (threadpool) rồi phát `done`. Lỗi giữa chừng (`error`) hoặc client ngắt kết nối thì **không lưu gì**. Lỗi khác ngoài `LLMError` (vd DB) sau khi đã mở stream cũng thành sự kiện `error` với `INTERNAL_ERROR`.

Chatbox công khai: `main.py:chat` chạy song song `find_procedure` (procedure-service) và `process_message` (không history, không tài liệu). AI/rag lỗi mà vẫn có thủ tục thì trả 200 với `answer = "Đã tìm thấy thủ tục phù hợp."`.

Metadata tin nhắn trả lời: `{rag_chunk_ids, model, tokens_used}`; câu ngoài phạm vi thêm `type: out_of_scope` (`rag_chunk_ids` rỗng, `model` và `tokens_used` của lượt chấm); nhánh rà soát thêm `mode: contract_review`, `contract_type`; khi `LLM_BYPASS` bật thêm `llm_bypass: true`, `queries`, `system_prompt` và `model = no-llm`; lượt có tài liệu thêm `document_ids` (không lưu nội dung tài liệu).

## 4. Bản đồ file

| File | Trách nhiệm |
|---|---|
| `app/main.py` | FastAPI app, `/health`, `/chat`, `find_procedure`, handler lỗi khung `{data,error,request_id}` |
| `app/api/v1/conversations.py` | 5 endpoint hội thoại, gửi tin nhắn (cả bản stream SSE: `send_message_stream`, `_stream_events`, `_sse`), map lỗi tài liệu/RAG/LLM sang mã HTTP |
| `app/api/deps.py` | `get_current_user` (Bearer, dự phòng cookie `access_token`), `raw_access_token` |
| `app/core/auth.py` | Verify JWT RS256 bằng public key |
| `app/core/config.py` | Toàn bộ cấu hình (`Settings`) |
| `app/core/database.py` | Engine/session SQLAlchemy cho `AGENT_DATABASE_URL` |
| `app/services/agent.py` | `process_message`: bộ điều hướng (câu vô nghĩa, chọn `review_agent` hay `qa_agent`), không gọi LLM; `prepare`: nhánh không tài liệu, dừng trước bước gọi LLM (cho stream) |
| `app/services/qa_agent.py` | Agent hỏi đáp pháp luật: `prepare` (rag + chấm liên quan, trả kết quả hoặc `LLMCall`), `answer` (= `prepare` rồi `run`) |
| `app/services/review_agent.py` | Agent rà soát tài liệu: `review`, `search_for_review` |
| `app/services/prompts.py` | `SYSTEM_PROMPT`, `REVIEW_SYSTEM_PROMPT`, `build_qa_prompt`, `build_review_prompt`, `format_chunk`, `format_documents` |
| `app/services/responder.py` | `respond`: gọi LLM, hoặc trả đoạn luật thô khi `LLM_BYPASS`; `BYPASS_HEADER`; `LLMCall`: việc gọi LLM đã chuẩn bị xong (`run` lấy cả câu, `result` ghép metadata + log thời gian, dùng chung với stream) |
| `app/services/query_filter.py` | Nhận diện câu vô nghĩa (đa số từ không phải âm tiết tiếng Việt) |
| `app/services/relevance.py` | `grade`: nhờ LLM chọn đoạn luật liên quan (trả `None` = không chấm được); prompt chấm `RELEVANCE_PROMPT` và câu `OUT_OF_SCOPE_ANSWER` ở `prompts.py` |
| `app/services/contract_review.py` | Nhận diện loại hợp đồng (`CONTRACT_PATTERNS`), câu tìm luật theo loại (`REVIEW_QUERIES`), `merge_chunks` |
| `app/services/rag_client.py` | Gọi rag-service: `search`; `RAGError` |
| `app/services/document_client.py` | Lấy chữ tài liệu từ document-service; `DocumentNotFound/NotReady/ServiceError` |
| `app/services/llm_client.py` | Gọi Groq / Gemini / OpenRouter, thử lại, chuyển provider; `LLMError`; `generate_stream` (phát từng mẩu chữ, `_timed_stream` đặt timeout từng mẩu) |
| `app/repositories/conversation.py` | CRUD hội thoại và tin nhắn, `add_message_pair`, `make_title` |
| `app/models/`, `app/schemas/` | `Conversation`, `Message`; schema request/response (`SendMessageRequest` có `document_ids`) |
| `database/001_agent_schema.sql`, `check_agent_db.sql` | Schema Supabase và script kiểm tra |
| `scripts/try_groq.py`, `try_gemini.py` | Chạy thử LLM thật qua `llm_client` |
| `scripts/eval_pipeline.py`, `agent_cases.json` | Chấm agent không qua LLM (`LLM_BYPASS`): đúng nhánh, hit@k, MRR, prompt đủ đoạn luật; rag-service thật qua HTTP |
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
| `RAG_TOP_K` | `4` | không | `qa_agent.answer`, `review_agent.search_for_review` |
| `DOCUMENT_SERVICE_URL` | `http://document-service:8000` | có | `document_client` |
| `PROCEDURE_SERVICE_URL` | `http://procedure-service:8000` | có | `main.find_procedure` (đọc bằng `os.getenv`) |
| `JWT_PUBLIC_KEY_PATH` | `keys/jwt_public.pem` | có | `core/auth.py` |
| `AGENT_DATABASE_URL` | trống | có | `core/database.py` |
| `LLM_PROVIDER` | `openrouter,gemini` | có | `llm_client.generate`: một provider (`openrouter`, `groq`, `gemini`) hoặc danh sách ưu tiên |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | trống / `gemini-3.1-flash-lite` | có | `_generate_gemini`; luôn tắt suy nghĩ bằng `thinking_budget=0` (`_gemini_thinking_config`) |
| `GROQ_API_KEY` / `GROQ_MODEL` | trống / `qwen/qwen3.8-27b` | có (mặc định compose: `llama-3.3-70b-versatile`) | `_generate_groq` |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` / `OPENROUTER_BASE_URL` | trống / trống (bắt buộc đặt) / `https://openrouter.ai/api/v1` | có | `_generate_openrouter` (SDK `openai`); thiếu key hoặc model thì báo lỗi ngay, không gọi mạng |
| `OPENROUTER_SITE_URL` / `OPENROUTER_APP_NAME` | trống | có | tuỳ chọn, gửi header `HTTP-Referer` / `X-Title` khi có giá trị |
| `OPENROUTER_REASONING_MAX_TOKENS` | `1024` | có | token chừa cho phần suy nghĩ của model suy luận (vd `recursive/flint-v1.0:free`, tính vào `max_tokens`); `0` = không gửi |
| `GEMINI_TIMEOUT_SECONDS`, `GEMINI_MAX_RETRIES` | `45`, `3` | có | `_generate_gemini` (test cố định `15`, `1` trong `tests/conftest.py`) |
| `GROQ_TIMEOUT_SECONDS`, `GROQ_MAX_RETRIES` | `15`, `1` | không | `_generate_groq` |
| `OPENROUTER_TIMEOUT_SECONDS`, `OPENROUTER_MAX_RETRIES` | `30`, `1` | không | `_generate_openrouter` |
| `LLM_BYPASS` | `false` | có | `responder.respond`: `true` = bỏ bước LLM, trả thẳng đoạn luật rag tìm được. CHỈ để test, không bật ở môi trường thật |
| `RELEVANCE_CHECK` | `false` | có | `qa_agent.answer`: `true` = chấm độ liên quan trước khi trả lời, thêm 1 lượt gọi LLM ngắn mỗi câu (đo 28/10/2026 với `google/gemini-2.5-flash` qua OpenRouter: khoảng 4 đến 5s khi `OPENROUTER_REASONING_MAX_TOKENS=1024`, khoảng 1s khi `0`; xem mục 11 mục 13). Bỏ qua khi `LLM_BYPASS` bật; không áp dụng cho rà soát tài liệu |
| `RELEVANCE_TIMEOUT_SECONDS` | `8` | có | `relevance.grade`: chờ tối đa mỗi provider, quá giờ thì bỏ qua bước chấm |
| `LLM_MAX_OUTPUT_TOKENS` | `800` | không | hỏi đáp thường |
| `LLM_REVIEW_MAX_OUTPUT_TOKENS` / `LLM_REVIEW_TIMEOUT_SECONDS` | `2000` / `40` | không | rà soát tài liệu (không thử lại cùng provider) |

Đổi `.env` xong phải `docker compose up -d --force-recreate agent-service` (`restart` không nạp lại). Compose mặc định `GROQ_MODEL` khác mặc định trong code: nếu `.env` không đặt thì chạy `llama-3.3-70b-versatile`.

## 7. Phụ thuộc service khác

| Service | Endpoint | Timeout | Lỗi thì xử lý |
|---|---|---|---|
| rag-service | `POST /internal/v1/rag/search` | 30s (`RAG_TIMEOUT_SECONDS`) | `RAGError` -> 503 `RAG_SERVICE_ERROR`; không lưu tin nhắn Circuit breaker (`rag_client._post`): 3 lần lỗi liên tiếp (timeout, lỗi kết nối, HTTP 5xx) thì 30 giây không gửi HTTP, ném ngay `RAGError` như khi rag lỗi; một lần phản hồi < 500 thì đếm lại từ 0; đếm theo từng process |
| LLM (Groq / Gemini / OpenRouter) | SDK | Gemini 45s thử lại 3 lần, Groq 15s và OpenRouter 30s thử lại 1 lần (khi 429/500/503/504/timeout); rà soát 40s, không thử lại | Provider lỗi -> thử provider kế; tất cả lỗi -> `LLMError` -> 503 `AI_SERVICE_ERROR` |
| procedure-service | `POST /api/v1/procedures/search` (chỉ `/chat`) | 5s | Bỏ qua, `procedure = null` |
| document-service | `GET /api/v1/documents/{id}/extraction` (chỉ khi có `document_ids`) | 10s | 401/403/404 -> 404 `DOCUMENT_NOT_FOUND` (không phân biệt để không lộ file người khác); 409 -> 409 `DOCUMENT_NOT_READY`; còn lại -> 503 `DOCUMENT_SERVICE_ERROR` |
| Supabase (agent DB) | `AGENT_DATABASE_URL` | | Truy vấn đồng bộ, chạy trong threadpool |

Gateway chờ agent tối đa 120s. Rà soát tài liệu tệ nhất: rag + 40s x số provider (không thử lại) nên nằm trong ngưỡng này với 2 provider.

Stream (`generate_stream`): mỗi mẩu chữ, kể cả mẩu đầu (tính cả bước mở stream), chờ tối đa timeout của provider (`GEMINI_TIMEOUT_SECONDS`, `OPENROUTER_TIMEOUT_SECONDS`; Groq theo `GROQ_TIMEOUT_SECONDS` cho cả câu), không thử lại cùng provider. `proxy_read_timeout 120s` của nginx tính giữa hai lần đọc nên stream dài hơn 120s vẫn chạy được miễn là có chữ đều đặn.

## 8. Công thức thêm tính năng

- **Thêm endpoint:** schema (`app/schemas/`) -> repository (`app/repositories/`) -> api (`app/api/v1/`) -> test -> `openapi.yaml`.
- **Thêm nhánh xử lý mới trong agent:** thêm bước trong `agent.process_message` đặt **trước** chỗ gọi `review_agent` / `qa_agent` (nhánh nào không cần AI thì trả sớm, như câu vô nghĩa; nhánh cần AI thì làm thành agent riêng như `qa_agent.py`, gọi `responder.respond`); trả đúng dạng `{"content", "metadata"}` và `metadata` có `rag_chunk_ids`, `model`, `tokens_used`; thêm test vào `tests/test_agent.py`; ghi `type`/`mode` vào `metadata` và `openapi.yaml` (schema `MessageMetadata`).
- **Thêm loại hợp đồng cho rà soát:** thêm mẫu vào `CONTRACT_PATTERNS` và 3-4 khía cạnh vào `REVIEW_QUERIES` (`contract_review.py`), thêm case vào `tests/test_contract_review.py`.
- **Thêm provider LLM:** viết `_generate_<tên>` trong `llm_client.py` (nhận `max_output_tokens`, `timeout_seconds`, `max_retries`, trả `(text, tokens)`), thêm nhánh `elif` trong `generate`, thêm biến vào `config.py`, `.env.example`, `docker-compose.yml`, test trong `test_llm_client.py`.
- **Đổi câu dặn LLM:** sửa `SYSTEM_PROMPT` / `REVIEW_SYSTEM_PROMPT` đầu `prompts.py`; dòng chống prompt injection dùng chung ở `INJECTION_RULE`.

## 9. Debug nhanh

| Triệu chứng | Xem log nào | File nghi ngờ |
|---|---|---|
| Trả lời chậm | `agent timing request_id=... rag=..s llm=..s` (agent) rồi `rag timing embed=..s db=..s` (rag) | `rag_client.py`, rag-service `search.py`, `repositories/document.py` |
| 503 `RAG_SERVICE_ERROR` | log `RAG lỗi, request_id=...`; nếu có `rag-service tạm ngắt do lỗi liên tiếp` thì breaker đang mở, không còn gửi HTTP | `INTERNAL_TOKEN` hai bên khớp chưa, rag còn chạy, `RAG_TIMEOUT_SECONDS`, DB rag; breaker ở `rag_client.py` (`_FAIL_LIMIT`, `_OPEN_SECONDS`) |
| 503 `AI_SERVICE_ERROR` | `Provider 'groq' thất bại` | hết quota / sai khóa / timeout; `llm_client.py` |
| Câu hỏi lại ra "chưa hiểu câu hỏi" | `agent bỏ qua câu vô nghĩa` | `query_filter.py` |
| Câu hỏi đúng phạm vi bị trả "chưa tìm thấy quy định liên quan" | metadata `type = out_of_scope`; log `agent timing ... out_of_scope=1`; log `agent chấm liên quan ...` (warning khi bước chấm lỗi) | `relevance.py`, `prompts.RELEVANCE_PROMPT`; tạm tắt bằng `RELEVANCE_CHECK=false` rồi `--force-recreate` |
| Stream: chữ hiện một lần thay vì dần (qua gateway) | `curl -N` thẳng vào `:8080` xem sự kiện `delta` có đến từng cái không; log `agent timing` ghi thời gian cả câu | header `X-Accel-Buffering: no` có trong response không; nginx (`gateway/nginx.conf`, `/api/agent/`) hoặc proxy khác phía trước đang gom (buffering); FE có đọc bằng `fetch` stream hay vẫn gọi endpoint cũ |
| Stream: nhận `error` giữa chừng, DB không có tin nhắn | log `LLM lỗi khi stream` / `Lỗi khi stream câu trả lời` kèm `request_id`; log `Provider '...' thất bại` | đúng thiết kế: lỗi giữa chừng không lưu gì, người dùng gửi lại; `llm_client.generate_stream` |
| Rà soát ra chung chung | `agent rà soát ... contract_type=khac` | `contract_review.detect_contract_type`, `REVIEW_QUERIES` |
| Tài liệu đính kèm bị 409 | trả `DOCUMENT_NOT_READY` | document-service chưa trích xong; không có worker thì file kẹt `queued` (`docker-compose.document-worker.yml`) |
| Đáp án chỉ toàn đoạn luật thô, có tiêu đề `[CHẾ ĐỘ TEST` | `metadata.llm_bypass = true`, log `agent bypass LLM` | `LLM_BYPASS` đang bật; đặt `false` rồi `--force-recreate` |
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
Test không qua LLM (LLM tự thêm ý sẽ che lỗi của rag và agent): đặt `LLM_BYPASS=true` trong `.env`, `docker compose up -d --force-recreate agent-service`, gọi `/api/agent/conversations/{id}/messages` như thường, đáp án là các đoạn luật thô (tiêu đề `[CHẾ ĐỘ TEST - KHÔNG QUA LLM]`), `metadata` có `queries` và `system_prompt` để soi prompt. `/chat` công khai chỉ trả `answer`, không có metadata. Xong việc phải đặt lại `false`. Chấm tự động 30 ca (nhánh + hit@k), rag-service thật, chỉ tốn lượt embedding Gemini của rag:
```powershell
# rag-service không mở cổng ra máy host nên chạy trong mạng Docker; mount app/scripts để dùng code mới nhất
docker compose run --rm --no-deps -e LLM_BYPASS=true -v ./services/agent-service/app:/app/app -v ./services/agent-service/scripts:/app/scripts -v ./services/rag-service/scripts:/app/rag_scripts agent-service python scripts/eval_pipeline.py -v --rag-questions /app/rag_scripts/eval_questions.json
```
Thêm `--top-k N` để thử đổi `RAG_TOP_K`. Mã thoát 1 khi có ca sai nhánh, prompt thiếu đoạn luật hoặc có lượt gọi LLM; câu "TRƯỢT" (không có đoạn đúng Điều trong top_k) chỉ báo, không làm hỏng lượt chạy. Thêm ca mới vào `scripts/agent_cases.json`. Phía rag riêng có `services/rag-service/scripts/eval_retrieval.py` và `search_cli.py` (`--max-chars 0` để in đủ nội dung đoạn).

Thử qua gateway: `POST http://localhost:8080/api/agent/chat` body `{"message": "Sang tên sổ đỏ cần giấy tờ gì?"}`.

Thử stream qua gateway (cần token và một hội thoại; ghi body ra file UTF-8 vì tiếng Việt gõ thẳng trong `-d` có thể sai mã hóa trên Windows, từng gặp HTTP 400; lệnh mẫu này chưa chạy qua gateway thật):
```powershell
$token = (.venv\Scripts\python scripts/mint_dev_token.py --role citizen | Select-Object -Last 1).Trim()
$id = (curl.exe -s -X POST http://localhost:8080/api/agent/conversations -H "Authorization: Bearer $token" | ConvertFrom-Json).data.id
'{"content": "Điều kiện tách thửa đất cho con"}' | Set-Content -Encoding utf8NoBOM body.json
curl.exe -N -X POST "http://localhost:8080/api/agent/conversations/$id/messages/stream" -H "Authorization: Bearer $token" -H "Content-Type: application/json" --data-binary "@body.json"
```
Đúng là thấy `event: delta` hiện dần rồi một `event: done`. Xong nhớ xóa `body.json`. FE phải đọc bằng `fetch` + `response.body.getReader()` (`EventSource` không gửi được POST kèm token).

OpenRouter: `.env` cần `OPENROUTER_API_KEY` và `OPENROUTER_MODEL`. Kết quả 200 có `data.answer`; nếu OpenRouter lỗi, log có dòng `Provider 'openrouter' thất bại: <lý do>` rồi Gemini trả lời thay. Model `recursive/flint-v1.0:free` bắt buộc suy luận nên cần `OPENROUTER_REASONING_MAX_TOKENS`, thiếu thì câu trả lời bị cắt hoặc rỗng. Lỗi 401 = sai key, 402 = hết credit, 429 = vượt giới hạn model free, chặn data policy: bật tại https://openrouter.ai/settings/privacy.

## 11. Hạn chế đã biết / nợ kỹ thuật

1. Câu hỏi nối tiếp không được viết lại khi tìm luật: `rag_client.search` chỉ nhận câu hiện tại, lịch sử chỉ đưa cho LLM ("thế còn thời hạn thì sao?" tìm kém). `qa_agent.py`.
2. Chưa truyền `doc_types` sang rag-service; kho hiện chỉ có luật. `rag_client.py`.
3. Chất lượng trả lời phụ thuộc kho: rag chưa có nghị định, thông tư nên hỏi giấy tờ, hồ sơ, lệ phí hay ra "chưa đủ căn cứ" (xem README-RAG).
3b. Bước chấm độ liên quan (`relevance.py`) mới thử 5 câu với model thật (28/10/2026): chặn đúng "Ly hôn", "Nghị định 101/2024", "tội trộm cắp", giữ Điều 220 cho "tách thửa"; nhưng cũng chặn "Sang tên sổ đỏ cần giấy tờ gì?" (câu mẫu của `DEFAULT_ANSWER`; trước đó LLM cũng chỉ trả "chưa đủ căn cứ" vì kho thiếu nghị định). Chưa biết tỉ lệ chặn nhầm trên bộ câu lớn: bật `RELEVANCE_CHECK` rồi thử một bộ câu của người dân trước khi dùng thật.
4. Câu hỏi đúng số Điều ("Điều 45 Luật Đất đai") đi tìm vector như câu thường; đoạn đúng Điều có thể không lọt top_k (đo bằng 2 ca trong `agent_cases.json`).
5. `DEFAULT_ANSWER` cố định nhắc "đất đai hoặc hộ kinh doanh". `query_filter.py`.
6. `/chat` công khai không giới hạn tần suất (không có `limit_req` ở `gateway/nginx.conf`), mỗi câu tốn lượt embedding và LLM.
7. Stream (SSE) mới có ở `POST /conversations/{id}/messages/stream`; `/chat` công khai và nhánh rà soát tài liệu chưa stream (rà soát phát cả câu một `delta`). Groq không stream thật (phát cả câu một lần). Chữ chỉ hiện dần khi FE đọc bằng `fetch` stream: repo này không có code FE, FE chưa đổi thì vẫn dùng endpoint cũ và chờ đủ câu. Chưa thử qua gateway thật: cần kiểm `X-Accel-Buffering: no` đủ để nginx không gom (nếu không thì thêm `proxy_buffering off` cho `/api/agent/`). Stream chỉ lưu DB sau khi `StreamingResponse` bắt đầu chạy nên cần FastAPI 0.118 trở lên (session `get_db` đóng sau khi stream xong); `requirements.txt` ghi `fastapi>=0.115`, image hiện dùng 0.162. Chấm độ liên quan cộng thêm thời gian trước chữ đầu tiên (xem mục 13).
8. Tài liệu đính kèm: cắt cứng `MAX_DOCUMENT_CHARS = 12000` ký tự (mất phần sau của tài liệu dài, chưa chia đoạn); chưa hỗ trợ Excel; tài liệu không tự mang sang lượt sau (client gửi lại `document_ids`); `/chat` công khai không hỗ trợ tài liệu. `prompts.py`, `document_client.py`.
9. Nhận diện loại hợp đồng bằng từ khóa: hợp đồng đặt tên lạ hoặc viết tắt thành `khac`, rà soát dùng 300 ký tự đầu tài liệu làm câu tìm. `contract_review.py`.
10. Chống prompt injection trong tài liệu chỉ bằng lời dặn (`INJECTION_RULE`), chưa kiểm thử với mô hình thật.
11. Compose mặc định `GROQ_MODEL` khác mặc định code; compose truyền `RABBITMQ_URL` nhưng app code agent-service không dùng. `docker-compose.yml`.
12. `.env.example` đầu file còn trỏ "README-AGENT.md, mục 7" (cấu hình nay ở mục 6).
13. `OPENROUTER_REASONING_MAX_TOKENS` (mặc định 1024, `.env` không đặt) bắt `google/gemini-2.5-flash` "nghĩ" khoảng 850 token trước khi trả lời. Đo 28/10/2026 qua OpenRouter: hỏi đáp thường khoảng 7s; stream chữ đầu tiên đến lúc ~6.8s rồi cả câu đến cùng lúc (stream gần như không có lợi); bước chấm liên quan 4 đến 5s. Đặt `0`: chữ đầu ~1.1s, hỏi đáp ~3s, chấm ~1s. Đánh đổi: ở câu "Đất đang thế chấp có tặng cho con được không?", bản `1024` trả "chưa đủ căn cứ" (đúng), bản `0` khẳng định "không thể tặng cho con, trừ trường hợp hàng thừa kế" (ý không có trong đoạn luật); mới thử 2 câu nên chưa kết luận, cần so nhiều câu trước khi đổi. Tham số này dùng chung cho mọi lượt gọi OpenRouter (cả lượt chấm), chưa chỉnh được riêng từng lượt. Gemini trực tiếp luôn tắt suy nghĩ (`thinking_budget=0`).
14. Gemini trực tiếp (fallback của OpenRouter) ngày 28/10/2026 trả 503 "high demand" với `gemini-3.8-flash` (kể cả gọi không stream), `gemini-3.1-flash-lite` quá giờ hoặc 503, `gemini-2.5-flash` báo ngừng cho người dùng mới: OpenRouter lỗi thì chưa có đường dự phòng chạy được. Đường stream Gemini mới kiểm chứng phần báo lỗi với SDK thật, phần nhận chữ thành công chỉ mới thử bằng giả lập.

## 12. Sửa xong phải cập nhật gì

1. Đổi API / lỗi / metadata: sửa `openapi.yaml` của agent-service (và rag-service nếu đổi cách gọi rag); sửa mục 1, 3, 7 của file này.
2. Thêm / đổi biến cấu hình: `config.py`, `.env.example`, `docker-compose.yml` (nếu cần truyền vào container), mục 6.
3. Thêm / đổi file: mục 4.
4. Đổi schema DB: `database/001_agent_schema.sql`, `check_agent_db.sql`, model, mục 5.
5. Chạy test, ghi số test đạt + ngày + nhánh lên dòng đầu.
6. Gặp lỗi mới đáng nhớ: thêm vào mục 9; việc còn dang dở: mục 11.
