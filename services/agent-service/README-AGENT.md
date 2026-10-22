# README-AGENT: agent-service

Tài liệu này mô tả agent-service **đang làm gì và làm như thế nào**, để người mới (hoặc phiên chat sau) nắm nhanh mà không phải đọc lại toàn bộ code. Viết cùng cấu trúc với `services/rag-service/README-RAG.md` (agent gọi rag-service, nên hai file nên đọc cùng nhau).

- Muốn hiểu tổng quan: đọc mục 1 đến 3.
- Muốn sửa/thêm tính năng: đọc mục 4 (cách hoạt động), mục 8 (bản đồ file), mục 11 (sửa xong phải cập nhật gì).
- Cập nhật lần cuối: 21/10/2026 (khớp code nhánh `feature/legal-article-lookup`; test: 144 đạt).

---

## 1. Tóm tắt nhanh

agent-service là **trợ lý hỏi đáp pháp luật**: nhận câu hỏi của người dùng, quyết định cách xử lý, rồi trả câu trả lời.

| Việc agent-service làm | Dùng khi nào |
|---|---|
| **Hỏi đáp pháp luật bằng AI có căn cứ** (RAG + LLM) | Người dùng hỏi tình huống ("Sang tên sổ đỏ cần giấy tờ gì?") |
| **Trả nguyên văn Điều/Khoản, không qua AI** | Người dùng gõ "Điều 45 Luật Đất đai" |
| **Phân tích tài liệu người dùng đính kèm** (chỉ khi đăng nhập) | Gửi kèm `document_ids` (DOCX/PDF/ảnh đã upload) để AI đọc và đối chiếu với luật |
| **Chặn câu vô nghĩa** | Người dùng gõ bừa ("dsadsafsaf"), trả lời mặc định, không tốn lượt RAG/AI |
| **Gợi ý thủ tục phù hợp** (chỉ ở chatbox công khai) | Hỏi qua `/chat`, kèm thủ tục khớp câu hỏi từ procedure-service |
| **Lưu lịch sử hội thoại** (chỉ khi đăng nhập) | Người dùng có tài khoản, xem lại và hỏi nối tiếp |

agent-service **không tự giữ kiến thức luật**: đoạn luật lấy từ rag-service, câu chữ do LLM (Groq/Gemini) viết dựa trên đoạn luật đó.

## 2. Vị trí trong hệ thống

```
Web (frontend)
  -> gateway (nginx)  /api/agent/...  ->  agent-service /api/v1/agent/...
        |
        |-- rag-service        POST /internal/v1/rag/search     tìm đoạn luật
        |                      POST /internal/v1/rag/articles   nguyên văn Điều/Khoản
        |-- LLM                Groq (Qwen), lỗi thì Gemini
        |-- procedure-service  POST /api/v1/procedures/search   gợi ý thủ tục (chỉ /chat)
        |-- document-service   GET /api/v1/documents/{id}/extraction   chữ của tài liệu đính kèm (chỉ hội thoại)
        '-- Supabase riêng     AGENT_DATABASE_URL               bảng conversations, messages
```

Gọi sang rag-service dùng header `X-Internal-Token` (`INTERNAL_TOKEN`, phải khớp bên rag-service), timeout 10 giây.

## 3. Chức năng chi tiết

| # | Chức năng | Điểm vào | Cần đăng nhập | Lưu DB | Code chính |
|---|---|---|---|---|---|
| 1 | Chatbox công khai | `POST /api/v1/agent/chat` | Không | Không | `app/main.py` |
| 2 | Tạo hội thoại | `POST /api/v1/agent/conversations` | Có | Có | `api/v1/conversations.py` |
| 3 | Danh sách hội thoại (phân trang) | `GET /api/v1/agent/conversations` | Có | | như trên |
| 4 | Xem 1 hội thoại kèm tin nhắn | `GET /api/v1/agent/conversations/{id}` | Có | | như trên |
| 5 | Xóa hội thoại (xóa luôn tin nhắn) | `DELETE /api/v1/agent/conversations/{id}` | Có | Có | như trên |
| 6 | Gửi tin nhắn, nhận câu trả lời | `POST /api/v1/agent/conversations/{id}/messages` | Có | Có | như trên |
| 7 | Health check | `GET /api/v1/agent/health` | Không | | `app/main.py` |

Chức năng 1 và 6 đều chạy qua cùng 1 hàm xử lý `agent.process_message()` (`app/services/agent.py`), nên hành vi hỏi đáp giống nhau. Khác nhau:

| | `/chat` (công khai) | `/conversations/{id}/messages` |
|---|---|---|
| Lịch sử gửi cho LLM | Không (câu hỏi đứng một mình) | 6 tin nhắn gần nhất của hội thoại |
| Gợi ý thủ tục | Có (`procedure`) | Không |
| Lưu câu hỏi + trả lời | Không | Có |
| Kết quả | `{answer, procedure, sources}` | `{user_message, assistant_message}` |

Gateway chuyển `/api/agent/...` tới `/api/v1/agent/...` (ví dụ web gọi `POST /api/agent/chat`).

**Chưa có:** trả lời dạng stream, viết lại câu hỏi nối tiếp trước khi tìm luật (xem mục 10), lọc theo loại văn bản khi gọi rag.

## 4. Cách hoạt động

### 4.1. Luồng xử lý một câu hỏi (`agent.process_message`)

```
câu hỏi
  1. query_filter.is_meaningless?  --có-->  trả DEFAULT_ANSWER, KHÔNG gọi rag, KHÔNG gọi AI
  2. article_lookup.parse?         --có-->  rag /articles  -> trả nguyên văn, KHÔNG gọi AI
  3. rag /search (top_k = RAG_TOP_K = 4)  -> các đoạn luật
  4. llm_client.generate(system prompt chứa các đoạn luật, history, câu hỏi)  -> câu trả lời
```

Kết quả luôn có dạng `{"content": "...", "metadata": {...}}`. `metadata` lưu cùng tin nhắn trả lời:

| Nhánh | `metadata` |
|---|---|
| Câu vô nghĩa | `{"rag_chunk_ids": [], "model": null, "tokens_used": null}` |
| Tra cứu nguyên văn | `{"rag_chunk_ids": [], "model": null, "tokens_used": null, "type": "article_lookup"}` |
| Hỏi đáp AI | `{"rag_chunk_ids": [id các đoạn luật], "model": "<tên model>", "tokens_used": N}` |

Log mỗi lần hỏi đáp AI: `agent timing request_id=... rag=..s llm=..s total=..s model=... tokens=... chunks=N history=N` (xem bằng `docker compose logs agent-service`). Dùng để biết chậm ở rag (embedding + tìm kiếm) hay ở LLM.

### 4.2. Lọc câu vô nghĩa (`app/services/query_filter.py`)

`is_meaningless(text)` đúng khi **đa số từ không phải âm tiết tiếng Việt** (so sau khi bỏ dấu bằng một mẫu phụ âm + nguyên âm + phụ âm cuối). Từ có chữ số (số hiệu, số Điều, năm) và từ viết tắt hoa 2 đến 6 chữ (CCCD, GCN, UBND) luôn được coi là hợp lệ. Câu rỗng cũng bị coi là vô nghĩa. Câu bị chặn nhận `DEFAULT_ANSWER` ("Mình chưa hiểu câu hỏi của bạn. Bạn hãy hỏi rõ hơn về thủ tục đất đai hoặc hộ kinh doanh...").

### 4.3. Tra cứu nguyên văn (`app/services/article_lookup.py`)

`parse(message)` quyết định câu nào là yêu cầu xem nguyên văn:
- Tìm cụm "Điều X [khoản Y] Luật Z" hoặc "Luật Z điều X [khoản Y]" (cũng nhận số hiệu như `31/2024/QH15`, gõ không dấu cũng được).
- Phần còn lại của câu chỉ được là **từ đệm** ("cho tôi xem", "nội dung", "quy định gì", "tra cứu"...). Có từ nào khác thì coi là câu hỏi và đi luồng AI.
- Ví dụ tra cứu: "Điều 45 Luật Đất đai", "khoản 2 điều 220 luật đất đai 2024 quy định gì", "dieu 45 luat dat dai", "Luật Đất đai điều 45".
- Ví dụ KHÔNG phải tra cứu: "Theo Điều 45 Luật Đất đai thì tôi có được tặng cho không?" (hỏi việc khác, đi luồng AI).
- Chưa hỗ trợ "điểm a khoản 1" và nhiều Điều trong 1 câu (những câu này đi luồng AI).

Nếu là tra cứu: `rag_client.get_article()` gọi rag `POST /internal/v1/rag/articles`, `format_article()` định dạng:
```
Nguyên văn Khoản 2 Điều 220, Luật Đất đai 2024 (31/2024/QH15):

<nguyên văn>

Lưu ý: <note, nếu có>   (vd kho không có bản năm đó)
Lưu ý: văn bản này không còn hiệu lực ...   (nếu văn bản không còn "active")
```
Rag báo không có văn bản/Điều/Khoản (404) hoặc tên mơ hồ (409) thì **hiện nguyên câu báo của rag cho người dùng** (vd "Luật Đất đai 2024 không có Điều 999 (văn bản này có 260 Điều)."), không coi là lỗi. Lỗi khác (rag sập, timeout) ném `RAGError`, endpoint trả 503.

### 4.4. Hỏi đáp AI (RAG + LLM)

1. **Lấy đoạn luật:** `rag_client.search(câu hỏi, top_k=4)` (câu hỏi cắt còn 1000 ký tự). Rag lo việc đổi từ đời thường, tách câu nhiều ý, gộp đoạn trùng Điều (xem README-RAG, mục 4.2). Agent chỉ nhận danh sách đoạn `{id, content, score, section_info, document}`.
2. **Dựng system prompt** (`build_system_prompt`): chèn các đoạn vào `SYSTEM_PROMPT`, mỗi đoạn có dòng tiêu đề trích dẫn, ví dụ `[1] Khoản 1 Điều 188, Luật Đất đai 2024 (31/2024/QH15)` rồi nội dung. Không có đoạn nào thì chèn câu "(Không tìm thấy trích đoạn pháp luật nào liên quan đến câu hỏi.)".
3. **Quy tắc trong prompt** (sửa ở `SYSTEM_PROMPT` đầu `app/services/agent.py`): trả lời tiếng Việt, câu đầu trả lời thẳng; sau đó tối đa 3 đến 5 gạch đầu dòng, căn cứ ghi gọn trong ngoặc ở cuối dòng; dưới 200 chữ, không chào hỏi/kết bài; **chỉ dùng thông tin trong các trích đoạn, không bịa**; không đủ căn cứ thì nói chưa đủ căn cứ và gợi ý hỏi luật sư; thiếu thông tin thì hỏi lại đúng 1 câu.
4. **Gọi LLM** (`app/services/llm_client.py`, hàm `generate`):
   - `LLM_PROVIDER` là một provider (`groq` hoặc `gemini`) hoặc **danh sách ưu tiên** cách nhau dấu phẩy, ví dụ `groq,gemini`: provider đầu lỗi thì tự thử provider kế tiếp; tất cả lỗi mới ném `LLMError`. (`.env` hiện đặt `groq,gemini`.)
   - Gửi kèm lịch sử hội thoại (cũ đến mới), `temperature = 0.2`, `max_output_tokens = 800`.
   - Mỗi provider: timeout 15 giây, thử lại 1 lần (chờ 2 giây) khi lỗi tạm thời (429/500/503/504, timeout, lỗi mạng). Lỗi khác (ví dụ 400, 401) báo ngay, không thử lại.
   - Gemini: giảm "suy nghĩ" tối đa (`thinking_budget=0` cho gemini-2.x, `thinking_level=MINIMAL` cho bản mới). Groq model qwen3: gửi `reasoning_effort="none"` để không sinh đoạn `<think>`.
   - Trả về `(câu trả lời, số token, tên model đã dùng)`.

### 4.5. Hội thoại có lưu lịch sử (`api/v1/conversations.py`, `repositories/conversation.py`)

`POST /conversations/{id}/messages`:
1. Kiểm tra hội thoại tồn tại (404 `CONVERSATION_NOT_FOUND`) và thuộc về người đang đăng nhập (403 `CONVERSATION_FORBIDDEN`). Chủ sở hữu lấy từ JWT `sub`.
2. Lấy 6 tin nhắn gần nhất (`HISTORY_LIMIT`) làm lịch sử cho LLM.
3. Gọi `process_message()`.
4. Lưu **cả câu hỏi và câu trả lời trong 1 transaction** (`add_message_pair`), cập nhật `updated_at`, tự sinh tiêu đề từ câu hỏi đầu tiên nếu hội thoại chưa có (gộp khoảng trắng, tối đa 80 ký tự).
5. Rag hoặc LLM lỗi thì **không lưu gì**, trả 503, người dùng gửi lại được.

Các truy vấn DB là đồng bộ nên chạy trong threadpool để không chặn event loop khi chờ LLM. Danh sách hội thoại sắp theo `updated_at` mới nhất trước, phân trang (`page` mặc định 1, `page_size` mặc định 20, tối đa 100).

### 4.5b. Đính kèm tài liệu (`document_ids`, chỉ ở `/conversations/{id}/messages`)

Người dùng đã đăng nhập gửi thêm `document_ids` (tối đa 3 UUID, không trùng; sai thì 422) là các tài liệu đã upload ở document-service. Chat công khai `/chat` **không** hỗ trợ (không biết chủ file).

1. Sau khi kiểm tra hội thoại, agent gọi `document_client.get_extractions()` (`app/services/document_client.py`): song song `GET {DOCUMENT_SERVICE_URL}/api/v1/documents/{id}/extraction`, timeout 10 giây, **chuyển tiếp nguyên token của người dùng** (`Authorization: Bearer ...`, lấy từ header hoặc cookie `access_token` qua `raw_access_token` trong `app/api/deps.py`) và `X-Request-ID`. document-service tự kiểm tra quyền chủ file; agent không dùng token nội bộ ở đây. Thiếu token thì 401.
2. Lấy tài liệu **trước** khi gọi RAG/LLM, nên tài liệu lỗi thì không tốn lượt AI và không lưu tin nhắn.
3. Đưa chữ vào system prompt (`agent.format_documents`, `build_system_prompt`) thành khối "Tài liệu người dùng cung cấp": mỗi tài liệu nằm giữa `<<<TÀI LIỆU n (loại)>>>` và `<<<HẾT TÀI LIỆU n>>>`, loại lấy từ `source_kind` (Word DOCX, PDF có chữ, PDF scan, ảnh...). Tổng `MAX_DOCUMENT_CHARS = 12000` ký tự chia đều cho các tài liệu, quá thì cắt và ghi `[đã cắt bớt]` (cắt cứng theo ký tự, chưa chia đoạn).
4. **Chống prompt injection** (nội dung tài liệu cài lệnh): prompt dặn tài liệu chỉ là DỮ LIỆU, bỏ qua mọi mệnh lệnh nằm trong tài liệu. Đây là biện pháp bằng lời dặn, không phải đảm bảo tuyệt đối.
5. Dặn AI phân tích tài liệu, chỉ ra điểm phù hợp/chưa phù hợp với các trích đoạn luật, trích dẫn căn cứ; không đủ căn cứ thì nói rõ. Các quy tắc trả lời cũ giữ nguyên. RAG vẫn tìm theo câu hỏi của người dùng như cũ (không tìm theo nội dung tài liệu).
6. Có tài liệu thì **bỏ qua nhánh tra cứu nguyên văn** (người dùng đang hỏi về tài liệu); lọc câu vô nghĩa vẫn chạy.
7. Không lưu nội dung tài liệu; chỉ thêm `document_ids` vào `metadata` tin nhắn trả lời. Lượt hỏi sau muốn dùng lại tài liệu thì client gửi lại `document_ids`.

Lỗi (đều không lưu tin nhắn nào):

| HTTP | `error.code` | Khi nào |
|---|---|---|
| 404 | `DOCUMENT_NOT_FOUND` | document-service trả 401/403/404: không có file hoặc của người khác (không phân biệt, để không lộ file tồn tại) |
| 409 | `DOCUMENT_NOT_READY` | Chưa trích xong ("Tài liệu đang xử lý, vui lòng thử lại sau."), hoặc trích lỗi/file bị từ chối (message khác: cần tải tài liệu khác) |
| 503 | `DOCUMENT_SERVICE_ERROR` | Timeout, lỗi mạng, 5xx hoặc kết quả sai hợp đồng |

Điều kiện chạy: document-service phải có worker trích chữ (`docker-compose.document-worker.yml`, xem `docs/api/DOCUMENT_WORKER.md`); không có worker thì file kẹt `queued` và luôn 409.

### 4.6. Chatbox công khai (`POST /api/v1/agent/chat`)

Chạy **song song** `find_procedure()` (gọi procedure-service `POST /api/v1/procedures/search`, timeout 5 giây, lấy thủ tục đầu tiên) và `process_message()`.
- Tìm thủ tục lỗi thì bỏ qua (`procedure = null`), không chặn câu trả lời AI.
- AI/rag lỗi mà **vẫn tìm được thủ tục** thì trả 200 với `answer = "Đã tìm thấy thủ tục phù hợp."`, `sources = []`.
- AI/rag lỗi và không có thủ tục thì trả 503.
- `sources` là danh sách id đoạn luật đã dùng (`rag_chunk_ids`).

### 4.7. Xác thực và lỗi chung

- Các endpoint `/conversations...` yêu cầu JWT RS256 do user-service cấp, verify bằng public key `JWT_PUBLIC_KEY_PATH`. Token lấy từ header `Authorization: Bearer ...`; không có thì lấy từ cookie `access_token` (frontend dùng `credentials: "include"`). Thiếu hoặc sai token trả 401 (`app/api/deps.py`).
- `/chat` và `/health` không cần đăng nhập.
- Mọi phản hồi theo khung `{"data": ..., "error": ..., "request_id": ...}`; `X-Request-ID` được giữ nguyên hoặc tự sinh, và được chuyển tiếp sang rag-service và procedure-service.

## 5. API

Hợp đồng đầy đủ ở `openapi.yaml` (đường dẫn trong file không có tiền tố `/api/v1`).

### `POST /api/v1/agent/chat`
Request: `{ "message": "Sang tên sổ đỏ cần giấy tờ gì?" }` (1 đến 2000 ký tự).

Response 200:
```json
{ "data": { "answer": "...", "procedure": { "id": "...", "code": "...", "name": "..." }, "sources": ["<chunk id>"] },
  "error": null, "request_id": "..." }
```
`procedure` có thể là `null`.

### `POST /api/v1/agent/conversations/{id}/messages`
Request: `{ "content": "...", "document_ids": ["<uuid>"] }`. `content` 1 đến 2000 ký tự; `document_ids` không bắt buộc, tối đa 3, không trùng (xem mục 4.5b).

Response 200:
```json
{ "data": {
    "user_message":      { "id": "...", "role": "user",      "content": "...", "metadata": null, "created_at": "..." },
    "assistant_message": { "id": "...", "role": "assistant", "content": "...", "metadata": { "rag_chunk_ids": [], "model": "...", "tokens_used": 512 }, "created_at": "..." }
}, "error": null, "request_id": "..." }
```

### Hội thoại
- `POST /conversations` body `{ "title": "..." }` (không bắt buộc, tối đa 255 ký tự) trả 201 `{id, title, created_at, updated_at}`.
- `GET /conversations?page=&page_size=` trả `{items, pagination{page, page_size, total, total_pages}}`.
- `GET /conversations/{id}` trả hội thoại kèm `messages` (cũ đến mới).
- `DELETE /conversations/{id}` trả `{"deleted": true}`.

### Mã lỗi

| HTTP | `error.code` | Khi nào |
|---|---|---|
| 401 | `UNAUTHORIZED` | Thiếu/sai token (nhóm `/conversations`) |
| 403 | `CONVERSATION_FORBIDDEN` | Hội thoại của người khác |
| 404 | `CONVERSATION_NOT_FOUND` | Không có hội thoại |
| 422 | `VALIDATION_ERROR` | Body sai (kèm `details`) |
| 503 | `RAG_SERVICE_ERROR` | Không gọi được hoặc rag-service trả lỗi/kết quả sai |
| 503 | `AI_SERVICE_ERROR` | Mọi provider LLM đều lỗi |
| 404 / 409 / 503 | `DOCUMENT_NOT_FOUND` / `DOCUMENT_NOT_READY` / `DOCUMENT_SERVICE_ERROR` | Chỉ khi gửi `document_ids` (bảng ở mục 4.5b) |

## 6. Dữ liệu

Database: Supabase riêng của agent-service (`AGENT_DATABASE_URL`, đổi `postgresql://` thành `postgresql+psycopg://` tự động). Schema ở `database/001_agent_schema.sql` (chạy trong Supabase SQL Editor, chạy lại nhiều lần được), kiểm tra bằng `database/check_agent_db.sql`.

- `conversations`: `id`, `user_id` (lấy từ JWT `sub`, **không có khóa ngoại** sang user-service vì mỗi service một database), `title` (null cho tới câu hỏi đầu tiên), `created_at`, `updated_at`.
- `messages`: `id`, `conversation_id` (xóa hội thoại thì xóa theo), `role` (`user` hoặc `assistant`), `content`, `metadata` (JSONB, chỉ tin nhắn trả lời; trong code thuộc tính tên `meta`; có `document_ids` khi lượt hỏi đính kèm tài liệu, **không** lưu nội dung tài liệu), `created_at`.
- Chỉ lưu khi dùng hội thoại có đăng nhập. `/chat` công khai không lưu gì.

## 7. Cấu hình (`app/core/config.py`)

| Biến | Mặc định (trong code) | Ý nghĩa |
|---|---|---|
| `RAG_SERVICE_URL` | `http://rag-service:8000` | Địa chỉ rag-service |
| `INTERNAL_TOKEN` | (trống) | Phải khớp `INTERNAL_TOKEN` của rag-service |
| `PROCEDURE_SERVICE_URL` | `http://procedure-service:8000` | Địa chỉ procedure-service (đọc trực tiếp ở `app/main.py`) |
| `DOCUMENT_SERVICE_URL` | `http://document-service:8000` | Địa chỉ document-service, lấy chữ tài liệu đính kèm |
| `JWT_PUBLIC_KEY_PATH` | `keys/jwt_public.pem` | Public key verify JWT |
| `AGENT_DATABASE_URL` | (trống) | Chuỗi kết nối Supabase của agent-service |
| `LLM_PROVIDER` | `gemini` | `groq`, `gemini` hoặc danh sách ưu tiên `groq,gemini` |
| `GEMINI_API_KEY` | (trống) | Khóa Gemini |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Model Gemini |
| `GEMINI_TIMEOUT_SECONDS` / `GEMINI_MAX_RETRIES` | `15` / `1` | Chờ tối đa mỗi lần / số lần thử lại |
| `GROQ_API_KEY` | (trống) | Khóa Groq |
| `GROQ_MODEL` | `qwen/qwen3.8-27b` | Model Groq |
| `GROQ_TIMEOUT_SECONDS` / `GROQ_MAX_RETRIES` | `15` / `1` | Như trên |
| `LLM_MAX_OUTPUT_TOKENS` | `800` | Chốt chặn độ dài câu trả lời (prompt đã yêu cầu dưới 200 chữ) |
| `RAG_TOP_K` | `4` | Số đoạn luật lấy từ rag-service |

Lưu ý Docker (`docker-compose.yml`): chỉ truyền cho agent-service các biến `JWT_PUBLIC_KEY_PATH`, `PROCEDURE_SERVICE_URL`, `LOCATION_SERVICE_URL`, `DOCUMENT_SERVICE_URL`, `AGENT_DATABASE_URL`, `LLM_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `GROQ_API_KEY`, `GROQ_MODEL`, `RAG_SERVICE_URL`, `INTERNAL_TOKEN`, `RABBITMQ_URL`. Các biến còn lại (timeout, số lần thử lại, `RAG_TOP_K`, `LLM_MAX_OUTPUT_TOKENS`) luôn dùng mặc định trừ khi thêm vào `environment`. Khi `GROQ_MODEL`/`LLM_PROVIDER` không có trong `.env`, compose dùng mặc định riêng (`llama-3.3-70b-versatile`, `gemini`) khác mặc định trong code. Đổi `.env` xong phải `docker compose up -d --force-recreate agent-service` (`restart` không nạp lại `.env`). Ngoài ra `.env.example` của service còn cũ (chưa có Groq, `LLM_PROVIDER`, `RAG_TOP_K`); `.env` gốc ở thư mục `legal-assistant-ai` mới là bản đang dùng.

## 8. Bản đồ file

```
services/agent-service/
  app/
    main.py                  khởi tạo FastAPI, /health, /chat (kèm find_procedure), xử lý lỗi chung
    api/deps.py              get_current_user (Bearer, cookie access_token làm dự phòng)
    api/v1/conversations.py  5 endpoint hội thoại + gửi tin nhắn
    services/agent.py        process_message: luồng lọc, tra cứu, RAG, LLM; SYSTEM_PROMPT; format_chunk
    services/query_filter.py chặn câu vô nghĩa
    services/article_lookup.py  nhận diện câu tra cứu nguyên văn, định dạng nguyên văn
    services/rag_client.py   gọi rag-service: search, get_article; RAGError, ArticleNotFound
    services/document_client.py  lấy chữ tài liệu đính kèm từ document-service (chuyển tiếp token người dùng)
    services/llm_client.py   gọi Groq/Gemini, thử lại, chuyển provider; LLMError
    repositories/conversation.py  CRUD hội thoại và tin nhắn, add_message_pair, make_title
    models/                  Conversation, Message
    schemas/                 ConversationCreate/Out/Detail/List, SendMessageRequest, MessageOut
    core/                    config.py, database.py, auth.py (verify JWT)
  scripts/                   try_gemini.py, try_groq.py (chạy thử LLM qua llm_client)
  database/                  001_agent_schema.sql, check_agent_db.sql
  tests/                     test từng module (api, agent, llm, rag_client, repositories, query_filter, article_lookup)
  openapi.yaml               hợp đồng API
```

## 9. Cách chạy, test

Chạy lệnh ở thư mục gốc `legal-assistant-ai`, dùng venv `.venv` của repo.

```powershell
# Test (144 test, không cần mạng: rag, LLM, document-service và DB đều được giả lập)
cd services/agent-service
..\..\.venv\Scripts\python -m pytest -q

# Chạy bằng Docker và xem log
docker compose up -d agent-service
docker compose logs agent-service

# Chạy thử LLM thật (cần khóa trong .env)
python services/agent-service/scripts/try_groq.py
python services/agent-service/scripts/try_gemini.py
```

Thử cả luồng qua gateway (Docker đang chạy): `POST http://localhost:8080/api/agent/chat` body `{"message": "Điều 45 Luật Đất đai"}` (nhánh nguyên văn) và `{"message": "Sang tên sổ đỏ cần giấy tờ gì?"}` (nhánh AI). Xem `agent timing` trong log để biết rag và LLM mất bao lâu.

Gặp 503 `RAG_SERVICE_ERROR`: kiểm tra rag-service còn chạy, `INTERNAL_TOKEN` hai bên khớp, và database rag (Supabase) kết nối được. Gặp 503 `AI_SERVICE_ERROR`: xem log `Provider 'groq' thất bại`, thường do hết quota hoặc sai khóa.

## 10. Hạn chế đã biết / việc tiếp theo

1. **Câu hỏi nối tiếp không được viết lại khi tìm luật:** `rag_client.search` chỉ nhận câu hỏi hiện tại, lịch sử chỉ đưa cho LLM. Câu như "thế còn thời hạn thì sao?" tìm luật kém vì thiếu chủ đề. Hướng làm: ghép chủ đề từ câu trước hoặc viết lại câu hỏi trước khi gọi rag.
2. **Chưa truyền `doc_types`** sang rag-service. Kho hiện chỉ có luật nên chưa cần; khi nạp nghị định, thông tư thì cân nhắc.
3. **Chất lượng câu trả lời phụ thuộc kho văn bản:** rag chưa có nghị định, thông tư nên hỏi giấy tờ, hồ sơ, lệ phí hay ra "chưa đủ căn cứ" (xem README-RAG, mục 6 và 11).
4. Tra cứu nguyên văn chưa hỗ trợ "điểm a khoản 1" và nhiều Điều trong 1 câu.
5. `DEFAULT_ANSWER` của bộ lọc câu vô nghĩa đang nhắc "đất đai hoặc hộ kinh doanh" (cố định trong code); cập nhật khi mở rộng lĩnh vực.
6. `/chat` công khai không cần đăng nhập mà mỗi câu tốn lượt Gemini (embedding ở rag) và LLM; không thấy giới hạn tần suất trong code agent-service lẫn `gateway/nginx.conf` (không có `limit_req`). Cân nhắc thêm khi đưa ra ngoài.
7. Chưa trả lời dạng stream; người dùng chờ đủ rag + LLM (xem log `agent timing` để biết thời gian thực tế).
8. `.env.example` còn cũ (mục 7), mới chỉ bổ sung `DOCUMENT_SERVICE_URL`.
9. Tài liệu đính kèm: cắt cứng 12.000 ký tự (tài liệu dài mất phần sau); chưa chia đoạn rồi tìm đoạn liên quan; chưa hỗ trợ Excel; tài liệu không tự mang sang lượt sau (client gửi lại `document_ids`); lời dặn chống prompt injection chưa kiểm thử với mô hình thật; chưa thử với worker và file thật (xem nhật ký).

## 11. Khi sửa code, cập nhật tài liệu này thế nào

1. Sửa mục liên quan trong phần 3 đến 8 cho khớp code mới (chức năng, luồng xử lý, API, cấu hình, bản đồ file).
2. Thêm 1 mục **đầu tiên** vào "Nhật ký thay đổi" bên dưới: ngày, file đã sửa, test (số đạt), có đổi database/API không, lỗi gặp khi làm.
3. Chạy test, ghi số test đạt ở dòng "Cập nhật lần cuối" trên cùng.
4. Đổi cách gọi rag-service hoặc hợp đồng API thì sửa cả `openapi.yaml` (cả 2 service) và README-RAG.md.

## 12. Nhật ký thay đổi

### 21/10/2026: đính kèm tài liệu khi hỏi trong hội thoại
- Thêm `document_ids` (tối đa 3) vào `POST /conversations/{id}/messages`; AI đọc chữ tài liệu đã upload và đối chiếu với luật. Không sửa document-service.
- File: `app/services/document_client.py` (mới), `app/services/agent.py` (khối tài liệu trong prompt, `MAX_DOCUMENT_CHARS`, bỏ qua nhánh tra cứu nguyên văn khi có tài liệu), `app/api/v1/conversations.py` (lấy tài liệu trước RAG/LLM, map lỗi, lưu `document_ids`), `app/api/deps.py` (`raw_access_token`), `app/schemas/message.py`, `app/core/config.py`, `openapi.yaml`, `.env.example`, `docker-compose.yml` (env `DOCUMENT_SERVICE_URL`).
- Test: thêm `tests/test_document_client.py`, mở rộng `test_agent.py` và `test_conversations_api.py`; fixture giả `process_message` nhận thêm tham số `documents`. agent-service 144 đạt (trước đó 104).
- Không đổi database. Chưa chạy thử với worker và file thật.

### 21/10/2026: tạo README-AGENT
- Viết tài liệu này từ code hiện tại (chưa đổi code). Test lúc viết: agent-service 104 đạt.
- Lịch sử các thay đổi gần đây liên quan agent-service (tra cứu nguyên văn điều luật 20/10, gọi rag chung qua `_post()`) nằm trong nhật ký của `services/rag-service/README-RAG.md`; từ nay ghi thay đổi của agent-service ở đây.
