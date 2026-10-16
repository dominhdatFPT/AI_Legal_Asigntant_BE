# RAG Service: embedding + vector DB (nhật ký 09/10/2026, cập nhật tối 09/10)

> Người viết: Claude, theo yêu cầu của Dũng. Đọc kèm `TONG_QUAN_DU_AN.md` và `README-GEMINI.md`.
> Mọi thay đổi nằm trong repo `B:\AI_03\BE\legal-assistant-ai`, nhánh hiện tại `feature/gemini-agent`, **chưa commit**.
> Bản clone cũ ở `C:\Users\laptop\legal-assistant-ai` (nhánh `feature/agent-service`) **không bị sửa gì**.

---

## 1. Tóm tắt

rag-service đã có đủ phần lõi để AI trả lời bằng dữ liệu thật:

- **Embedding (tạo vector)** bằng Gemini `gemini-embedding-001`, 768 chiều, dùng gói free.
- **Vector DB (cơ sở dữ liệu vector)** bằng **pgvector** trên Supabase, có index HNSW.
- **Chunking (cắt văn bản)** theo cấu trúc luật Việt Nam: Chương → Điều → Khoản → Điểm.
- **Ingestion (nạp dữ liệu)** bằng script dòng lệnh.
- **`POST /internal/v1/rag/search`** trả `data.chunks[]` đúng hợp đồng mà `agent-service/app/services/rag_client.py` đang gọi.

Chưa làm được: gọi Gemini thật (chưa có key trong phiên làm việc), tạo bảng trên Supabase, sửa các file dùng chung (`docker-compose.yml`, `.gitlab-ci.yml`, `.env.example` gốc).

---

## 2. Các quyết định

| Vấn đề | Chọn | Lý do |
|---|---|---|
| Model embedding | `gemini-embedding-001`, `output_dimensionality=768` | Miễn phí (gói free), đang dùng sẵn `google-genai` và key Gemini, không phải tải model về máy nên Docker image nhẹ. Model hỗ trợ MRL (Matryoshka Representation Learning), rút từ 3072 xuống 768 chiều mà vẫn tốt. |
| `task_type` | Nạp: `RETRIEVAL_DOCUMENT`. Hỏi: `RETRIEVAL_QUERY` | Đúng cách Google khuyến nghị cho tìm kiếm. |
| Chuẩn hóa vector | Tự L2-normalize (chuẩn hóa độ dài về 1) | Gemini chỉ chuẩn hóa sẵn ở 3072 chiều. |
| Vector DB | pgvector trên Supabase | Miễn phí, đúng kiến trúc "mỗi service một DB", không phải dựng Qdrant/Chroma. 768 chiều ≈ 3 KB/vector, 10.000 đoạn chỉ vài chục MB. |
| Index | HNSW, `vector_cosine_ops` | Tìm gần đúng nhanh theo khoảng cách cosine (toán tử `<=>`). Đã kiểm tra `EXPLAIN` có dùng index. |
| Đơn vị cắt | 1 Điều = 1 đoạn; Điều dài thì tách Khoản, Khoản dài thì tách Điểm; tối đa 2.500 ký tự | Trích dẫn tự nhiên ("Khoản 1 Điều 188"). 2.500 ký tự ≈ 600–900 token, dưới giới hạn 2.048 token của model. |
| Phương án dự phòng | `BAAI/bge-m3` chạy trên máy (chưa làm) | Code viết theo interface `Embedder`, đổi model chỉ cần thêm 1 class rồi nạp lại. |
| Gói free Gemini | Theo forum Google: ~100 request/phút, 30.000 token/phút, 1.000 request/ngày | Có thể thay đổi. Code tự chờ và thử lại khi gặp 429 (vượt giới hạn). |

---

## 3. File đã tạo / sửa

Tất cả trong `services/rag-service/`, trừ dòng cuối.

### Mới

| File | Nội dung |
|---|---|
| `database/001_rag_schema.sql` | Tạo extension `vector`, bảng `legal_documents`, `document_chunks` (`embedding vector(768)`), index HNSW, bật RLS (Row Level Security). Chạy lại nhiều lần không lỗi. |
| `database/check_rag_db.sql` | 11 dòng kiểm tra; dòng 1–9 phải là `OK`. Chạy được cả khi chưa có bảng. |
| `app/core/database.py` | SQLAlchemy 2 (sync) + psycopg, giống agent-service. Tự đổi `postgresql://` sang driver psycopg. |
| `app/services/chunking.py` | Bộ cắt văn bản luật (xem mục 4). |
| `app/services/ingest.py` | `ingest_document()` nạp văn bản mới, `reembed_document()` embedding lại khi đổi model. Tạo hết embedding rồi mới ghi DB, lỗi giữa chừng không để lại dữ liệu dở. |
| `scripts/ingest_file.py` | Nạp file `.txt`. Có `--dry-run` (chỉ cắt, không gọi API) và `--fake-embedding`. |
| `scripts/search_cli.py` | Tìm thử từ dòng lệnh. |
| `tests/conftest.py`, `test_chunking.py`, `test_embedding.py`, `test_ingest.py`, `test_search_api.py` | 45 test, dùng SQLite trong RAM + embedder giả, không cần Supabase hay key. |
| `tests/test_pgvector_integration.py` | 2 test với PostgreSQL + pgvector thật. Mặc định bỏ qua; chạy khi đặt `RAG_TEST_DATABASE_URL` (test **xóa sạch** 2 bảng, không trỏ vào DB chính). |
| `tests/fixtures/van_ban_mau.txt` | Văn bản mẫu nhóm tự viết, **không phải luật thật**. |

### Sửa

| File | Thay đổi |
|---|---|
| `requirements.txt` | Bỏ `openai`, `asyncpg`; thêm `google-genai`, `psycopg[binary]`. |
| `.env.example` | Thay `OPENAI_API_KEY` bằng `EMBEDDING_PROVIDER`, `GEMINI_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `EMBEDDING_BATCH_SIZE`. |
| `app/core/config.py` | Thêm cấu hình embedding (mặc định `gemini`, `gemini-embedding-001`, 768). |
| `app/models/document.py`, `chunk.py`, `__init__.py` | Model SQLAlchemy thật (trước chỉ là ghi chú TODO, dùng 1536 chiều của OpenAI). Thêm cột `content` (toàn văn) và `embedding_model`. |
| `app/services/embedding.py` | `GeminiEmbedder`, `FakeEmbedder`, `get_embedder()` (xem mục 5). |
| `app/repositories/document.py` | `create_document`, `replace_chunks`, `semantic_search` (chỉ văn bản `active`, đã xử lý, cùng model embedding). |
| `app/services/search.py` | `search_chunks()`: embedding câu hỏi → tìm → trả dict đúng hợp đồng; điểm = 1 − khoảng cách cosine, kẹp 0–1. |
| `app/schemas/search.py` | `SearchRequest`: `query` 1–1000 ký tự, `top_k` 1–20 (mặc định 5), `doc_types` tùy chọn. |
| `app/api/deps.py` | `require_internal_token` đọc từ settings (trước đọc `os.environ` nên không nhận `.env`), so sánh bằng `hmac.compare_digest`. |
| `app/api/v1/internal.py` | Endpoint `POST /internal/v1/rag/search`. |
| `app/main.py` | Gắn router internal; lỗi 422 có `details` theo từng trường; thêm handler lỗi 500 chung. Giữ endpoint demo `/api/v1/rag/search` để test cũ không vỡ. |
| `openapi.yaml` | `x-status: implemented` cho health và internal search; thêm response 503. |
| `database/README.md` | Hướng dẫn tạo bảng, embedding, nạp văn bản. |
| `tests/test_api.py` (thư mục gốc) | Thêm 4 dòng trong `load()`: xóa package `app` khỏi `sys.modules` sau khi load một service. Không có thì test của user-service import nhầm `app` của rag-service và lỗi 49 test. |

---

## 4. Cách cắt văn bản (`chunking.py`)

1. Chuẩn hóa Unicode NFC (chữ gõ dấu tổ hợp vẫn nhận ra "Điều"), gộp dòng trống.
2. Nhận dạng đầu dòng: `Chương I`, `Mục 1`, `Điều 188.` / `Điều 10a:`. Bắt buộc có dấu `.` hoặc `:` sau số Điều, nên câu "quy định tại Điều 5 của Luật này" không bị hiểu nhầm là Điều mới.
3. Bỏ phần trước Điều 1 (Quốc hiệu, "Căn cứ...") và tên Chương/Mục.
4. Điều ≤ 2.500 ký tự → 1 đoạn. Dài hơn → mỗi Khoản 1 đoạn, luôn kèm dòng "Điều X. Tên điều" ở đầu. Khoản vẫn dài → mỗi Điểm 1 đoạn, kèm thêm câu dẫn của Khoản ("... khi có đủ các điều kiện sau đây:"). Vẫn dài → cắt theo dòng.
5. Văn bản không có "Điều" → gom theo đoạn văn (dòng trống).
6. Chuỗi gửi đi embedding = tên văn bản + chương + nội dung đoạn, để vector có đủ ngữ cảnh.

Mỗi đoạn có `section_info = {"chuong", "dieu", "khoan", "diem"}` để agent-service tạo trích dẫn.

---

## 5. Embedding (`embedding.py`)

- `GeminiEmbedder`
  - `embed_documents()`: gửi theo batch 20 đoạn; lỗi 429/500/503/504 thì chờ 5 → 15 → 30 → 60 → 60 giây rồi thử lại.
  - `embed_query()`: chỉ thử lại 2 lần (1 và 2 giây) vì agent-service chờ tối đa 10 giây.
  - Kiểm tra số vector và số chiều trả về; sai thì báo `EmbeddingError`.
- `FakeEmbedder`: băm từ và cặp từ vào 768 ô. Câu nhiều từ chung thì gần nhau, nhưng **không hiểu nghĩa** (không biết "sổ đỏ" = "giấy chứng nhận"). Chỉ để test.
- `get_embedder()`: chọn theo `EMBEDDING_PROVIDER`; báo lỗi nếu `EMBEDDING_DIM` khác 768.

---

## 6. API `POST /internal/v1/rag/search`

Header `X-Internal-Token` bắt buộc. Gateway không chuyển tiếp `/internal/...`.

```json
// Gửi
{ "query": "Chuyển nhượng đất cần điều kiện gì?", "top_k": 5, "doc_types": ["luat"] }

// Nhận
{
  "data": { "chunks": [ {
    "id": "…", "content": "Điều 4. …", "score": 0.87,
    "section_info": { "chuong": "II", "dieu": "4", "khoan": "1", "diem": null },
    "document": { "id": "…", "title": "…", "doc_number": "…", "doc_type": "luat" }
  } ] },
  "error": null, "request_id": "req-…"
}
```

| Mã | Khi nào |
|---|---|
| 401 `UNAUTHORIZED` | Thiếu / sai `X-Internal-Token` |
| 422 `VALIDATION_ERROR` | `query` rỗng hoặc > 1000 ký tự, `top_k` ngoài 1–20, `doc_types` sai; có `details` theo trường |
| 500 `INTERNAL_ERROR` | Server chưa cấu hình `INTERNAL_TOKEN` / cấu hình embedding sai |
| 503 `EMBEDDING_SERVICE_UNAVAILABLE` | Gemini quá tải / hết quota |
| 503 `RAG_DATABASE_UNAVAILABLE` | Không kết nối được database |

---

## 7. Kết quả kiểm tra

| Bộ test | Kết quả |
|---|---|
| `pytest services/rag-service/tests` | 45 đạt, 2 bỏ qua (test pgvector) |
| `test_pgvector_integration.py` trên PostgreSQL 16 + pgvector 0.6 (máy ảo của Claude) | 2 đạt; `check_rag_db.sql` toàn `OK`; `EXPLAIN` dùng index HNSW |
| Chạy thật: script nạp → uvicorn → `curl` → `rag_client` của agent-service | Trả kết quả đúng hợp đồng |
| `pytest services/agent-service/tests` | 37 đạt (không đổi) |
| Lệnh CI `pytest tests libs/common_auth services/user-service/tests` | 4 lỗi, **giống hệt bản gốc trước khi sửa**: `test_gateway_config::test_idempotency_header_allowed_for_frontend` và 3 test `openapi` của location-service. Không do phần RAG gây ra. |

---

## 8. Chạy thật trên máy Dũng (tối 09/10/2026)

### 8.1. Kết quả

| Hạng mục | Kết quả |
|---|---|
| Bảng RAG trên Supabase (project `byysvstpwltmxmjoyhad`) | Tạo xong, `check_rag_db.sql` dòng 1–9 toàn `OK` |
| Nạp "Luật Mẫu" bằng Gemini thật | 6/6 đoạn, Gemini trả `200 OK` |
| `search_cli.py` | "điều kiện chuyển nhượng đất" → Điều 4 (0.770); "bố mẹ mất thì con cái nhận đất thế nào" → Điều 5 Thừa kế (0.731), dù không có từ nào trùng → embedding hiểu nghĩa |
| `POST /internal/v1/rag/search` qua Postman | `200 OK`, sai token → `401`, query rỗng / `top_k` 50 → `422` |
| agent → rag → Gemini qua Postman | Tạo hội thoại `201`, hỏi AI `200` (câu trả lời kèm `metadata.rag_chunk_ids`) |
| Luật Đất đai 2024 (31/2024/QH15) | `--dry-run`: 797 đoạn (188 theo Điều, 517 theo Khoản, 92 theo Điểm), ~211.000 token. Đang nạp |

### 8.2. Sự cố gặp phải và cách xử lý

| Sự cố | Nguyên nhân | Cách xử lý |
|---|---|---|
| `check_rag_db.sql` dòng 4, 5 THIẾU, dòng 6 SAI | Supabase đã có sẵn 2 bảng cùng tên theo thiết kế cũ (thiếu cột, sai số chiều), `create table if not exists` bỏ qua | Bảng đang trống → `drop table document_chunks; drop table legal_documents;` rồi chạy lại `001_rag_schema.sql` |
| Dòng 6 vẫn SAI khi bảng đúng | Supabase cài pgvector trong schema `extensions`, kiểu cột hiện là `extensions.vector(768)` | Dòng 6 so khớp `like '%vector(768)'` |
| Supabase chỉ chạy một phần câu lệnh | Nút **Run selected**: đang bôi đen một đoạn | Bấm vào chỗ trống để bỏ bôi đen, nút phải là **Run** |
| Postman `404` ở `/health` | URL dính dấu `.` ở cuối khi copy | Xóa dấu chấm |
| `500` + log `RuntimeError: Chưa cấu hình RAG_DATABASE_URL` | Terminal cũ giữ biến môi trường rỗng nên `--env-file` không ghi đè | Đóng terminal, mở terminal mới rồi chạy lại uvicorn |
| `503 AI_SERVICE_ERROR`, log `503 UNAVAILABLE ... high demand` | Gemini quá tải (lỗi phía Google) | Gửi lại sau ít phút; agent nay tự thử lại (mục 8.4) |
| Nạp luật báo `429 Too Many Requests` liên tục | Gói free giới hạn token/phút | Script tự chờ 5 → 15 → 30 → 60s rồi gửi tiếp; không đóng terminal, không cho máy sleep |

### 8.3. Cách test bằng Postman (chạy trên máy, không Docker)

Mỗi service một terminal **mới**, đứng ở thư mục gốc, venv đang bật:

```powershell
# Terminal 1 – rag-service
cd services/rag-service
uvicorn app.main:app --port 8001 --env-file ..\..\.env

# Terminal 2 – agent-service (ghi đè địa chỉ rag: .env đang để tên Docker http://rag-service:8000)
cd services/agent-service
$env:RAG_SERVICE_URL="http://localhost:8001"
uvicorn app.main:app --port 8002 --env-file ..\..\.env

# Terminal 3 – token đăng nhập (sống 60 phút)
python scripts/mint_dev_token.py --role citizen
```

| Request | Cấu hình Postman | Kết quả đúng |
|---|---|---|
| `GET http://localhost:8001/api/v1/rag/health` | No Auth | `200` |
| `POST http://localhost:8001/internal/v1/rag/search` | Authorization **API Key**: Key `X-Internal-Token`, Value = `INTERNAL_TOKEN` trong `.env`, Add to Header. Body raw JSON `{"query": "...", "top_k": 2}` | `200`, `data.chunks[]` |
| `POST http://localhost:8002/api/v1/agent/conversations` | Authorization **Bearer Token** = chuỗi `eyJ...`, Body none | `201`, lấy `data.id` |
| `POST http://localhost:8002/api/v1/agent/conversations/<id>/messages` | Bearer Token, Body raw JSON `{"content": "..."}` | `200`, `data.assistant_message` |
| `GET http://localhost:8002/api/v1/agent/conversations` | Bearer Token | Danh sách hội thoại |

Lỗi `500` thì xem log ở terminal của service đó: khối `Traceback`, dòng cuối cùng là nguyên nhân.

### 8.4. Thay đổi code tối 09/10/2026

- `docker-compose.yml`:
  - agent-service: thêm `RAG_SERVICE_URL: http://rag-service:8000`, `INTERNAL_TOKEN`; model mặc định `gemini-3.8-flash`.
  - rag-service: thêm `INTERNAL_TOKEN`, `GEMINI_API_KEY`, `EMBEDDING_PROVIDER/MODEL/DIM`.
- `services/agent-service/app/services/llm_client.py`: Gemini lỗi tạm thời (429/500/503/504, quá thời gian chờ, mất kết nối) thì chờ 2s, 4s rồi thử lại, tối đa `GEMINI_MAX_RETRIES` lần (mặc định 2). Mỗi lần gọi chờ tối đa `GEMINI_TIMEOUT_SECONDS` (mặc định 30). Lỗi khác (sai key, sai model) báo ngay. Test: `tests/test_llm_client.py` (15 test). Tổng agent-service: 52 test đạt.
- `services/agent-service/app/core/config.py` + hai file `.env.example`: model mặc định `gemini-3.8-flash`.
- `services/agent-service/openapi.yaml`: 6 API `x-status: implemented`, sửa "OpenAI" thành "Gemini".
- `services/rag-service/database/check_rag_db.sql`: dòng 6 chấp nhận `extensions.vector(768)`.

## 9. Việc cần làm tiếp

1. **Nạp xong Luật Đất đai 2024**, rồi kiểm tra:
   ```sql
   select count(*) as so_doan, count(distinct section_info->>'dieu') as so_dieu from document_chunks;  -- 797 và 260
   ```
   Tìm thử "điều kiện chuyển nhượng quyền sử dụng đất" → nên ra Điều 45. Hỏi AI qua Postman để xem trích dẫn.
   Nếu script dừng vì 429: **không chạy lại ngay** (gói free ~1.000 request/ngày). Sửa script cho gửi chậm hơn và lưu phần đã xong rồi mới chạy lại.
2. **Chạy bằng Docker**: `docker compose up --build`, test qua Gateway `http://localhost:8080/api/agent/...` (đường `/internal/...` bị Gateway chặn, đúng thiết kế).
3. **`.gitlab-ci.yml`**: thêm `pytest services/agent-service/tests` và `pytest services/rag-service/tests` thành **hai lệnh riêng** (chạy chung một lệnh thì hai package `app` đè nhau).
4. **Git** (khi Dũng muốn đưa lên GitLab):
   ```powershell
   # Máy chưa có nhánh develop ở local, và commit 62fdfbd chưa có trên GitLab,
   # nên tạo nhánh rag từ nhánh agent sau khi commit phần agent.
   git add services/agent-service docker-compose.yml .env.example README-GEMINI.md TONG_QUAN_DU_AN.md
   git commit -m "feat: agent-service gọi Gemini, RAG client, lịch sử hội thoại"
   git push -u origin feature/gemini-agent

   git switch -c feature/rag-search
   git add services/rag-service tests/test_api.py README-RAG.md
   git commit -m "feat: rag-service embedding Gemini + pgvector + internal search"
   git push -u origin feature/rag-search
   ```
   Trước khi commit, kiểm tra `git status` không có `.env`, `.venv/`, `*.pem`. Merge Request nhánh rag merge **sau** nhánh agent.
5. Sau này: API quản lý văn bản cho admin (`/api/v1/rag/documents`); nạp bản hợp nhất của Luật Đất đai (đã có sửa đổi) và các Nghị định hướng dẫn; nếu nhiều văn bản `superseded` thì bật `hnsw.iterative_scan` (pgvector ≥ 0.8).
