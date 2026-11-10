# README-RAG: rag-service
Cập nhật: 02/11/2026 | Nhánh: `feature/agent-conversation-summary` | Test: 295 đạt, 2 bỏ qua (pgvector cần DB thật)

> **TRƯỚC KHI BUILD LẠI rag-service: chạy `database/002_ingest_status.sql`, `database/003_ingest_log.sql` rồi `database/004_document_versions.sql` trên Supabase (SQL Editor), đúng thứ tự.** Code mới truy vấn hai cột `ingest_status`, `ingest_error` ở mọi nơi, kể cả tìm kiếm; chưa chạy 002 mà build lại thì tìm kiếm lỗi và agent báo 503. File 003 tạo bảng `ingest_log` (nhật ký nạp): chưa chạy thì việc nạp vẫn chạy bình thường (ghi nhật ký chỉ cảnh báo trong log) nhưng nhật ký trống và `GET /documents/logs` trả 500. File 004 tạo bảng `document_versions` (bản nội dung cũ khi thay) và thêm ba giá trị `replace_*` vào ràng buộc `action` của `ingest_log`; chưa chạy thì `POST /documents/{id}/replace` thất bại ở giai đoạn ghi (bản cũ giữ nguyên, `ingest_status=failed`) và nhật ký `replace_*` không ghi được (chỉ cảnh báo).

## 1. Service này làm gì

rag-service là **kho văn bản pháp luật có tìm kiếm**. Nó **không tự trả lời** người dùng; việc trả lời bằng AI nằm ở agent-service. Chỉ agent-service gọi endpoint nội bộ `/internal/v1/rag/search`, bằng header `X-Internal-Token`. Endpoint tra nguyên văn `POST /internal/v1/rag/articles` đã bỏ ngày 28/10/2026.

| Chức năng | Endpoint | File chính |
|---|---|---|
| Tìm đoạn luật theo ngữ nghĩa (vector) | `POST /internal/v1/rag/search` | `app/services/search.py` |
| Đổi từ đời thường sang thuật ngữ luật (trong search) | | `app/services/query_expansion.py` |
| Tách câu hỏi nhiều ý, tìm theo từng ý (trong search) | | `app/services/query_split.py` |
| Nạp văn bản vào kho (script, chưa có API) | `scripts/ingest_file.py`, `scripts/ingest_batch.py` | `ingest.py`, `chunking.py`, `embedding.py`, `batch_ingest.py` |
| Admin: danh sách văn bản (phân trang, lọc `status`, `doc_type`, không trả `content`) | `GET /api/v1/rag/documents` (gateway: `/api/rag/documents`) | `app/api/v1/documents.py`, `repositories/document.py:list_documents` |
| Admin: xem trước cách cắt văn bản (body JSON, **không ghi DB, không gọi embedding**) | `POST /api/v1/rag/documents/preview` | `app/api/v1/documents.py`, `app/services/document_preview.py` |
| Admin: lưu bản nháp (chưa chunk, chưa embedding), xem chi tiết, xóa nháp | `POST /api/v1/rag/documents` (201), `GET` và `DELETE /api/v1/rag/documents/{id}` | `app/api/v1/documents.py` |
| Admin: duyệt bản nháp, cắt chunk + embedding ở nền | `POST /api/v1/rag/documents/{id}/approve` (202) | `app/api/v1/documents.py`, `app/services/document_approval.py` |
| Admin: xem nhật ký nạp (ai tạo nháp, xóa nháp, duyệt, kết quả duyệt; phân trang, lọc `document_id`, `action`) | `GET /api/v1/rag/documents/logs` | `app/api/v1/documents.py:list_ingest_logs`, `app/services/ingest_log.py`, `repositories/document.py:list_ingest_logs` |
| Admin: xem trước việc thay nội dung văn bản active (so chunk cũ/mới, chunk nào dùng lại vector, Điều nào đổi, cảnh báo; **không gọi embedding, không ghi DB**) | `POST /api/v1/rag/documents/{id}/replace/preview` | `app/api/v1/documents.py`, `app/services/document_replace.py:build_replace_preview` |
| Admin: thay nội dung văn bản active bằng bản mới (chỉ embed chunk khác, đổi trong một giao dịch, lưu bản cũ vào `document_versions`) | `POST /api/v1/rag/documents/{id}/replace` (202) | `app/api/v1/documents.py`, `app/services/document_replace.py:replace_document_background` |
| Đo chất lượng tìm kiếm | `scripts/eval_retrieval.py` | `scripts/eval_questions.json`, `scripts/eval_natural.json` |
| Health check | `GET /api/v1/rag/health` | `app/main.py` |
| Endpoint demo cũ `POST /api/v1/rag/search` | trả kết quả mẫu cố định, **không phải tìm kiếm thật** | `app/main.py` |

Gateway chỉ mở `/api/rag/` tới `/api/v1/rag/...`, **không** mở `/internal/...` ra ngoài. Hợp đồng đầy đủ: `openapi.yaml`.

## 2. Quy tắc bắt buộc (KHÔNG được phá)

- Mỗi service một DB, không query DB service khác. rag-service dùng Supabase riêng (`RAG_DATABASE_URL`).
- Không gọi Gemini từ client, API key chỉ ở server.
- Không đổi hợp đồng API mà không sửa `openapi.yaml` của cả 2 service (rag và agent).
- Test không được gọi mạng thật (embedding dùng `FakeEmbedder`, DB dùng SQLite hoặc giả lập); chỉ `test_pgvector_integration.py` cần DB thật và tự bỏ qua.
- `EMBEDDING_DIM` phải khớp `vector(768)` trong SQL và `EMBEDDING_DIM` trong `app/models/chunk.py`; `get_embedder()` báo lỗi nếu lệch.
- `semantic_search` phải `defer` hai cột `legal_documents.content` (toàn văn cả bộ luật) và `document_chunks.embedding`: kéo chúng về theo từng dòng từng làm `db=15s`. Có test giữ: `tests/test_semantic_search_columns.py`.
- Tìm kiếm chỉ so với đoạn cùng `embedding_model`; đổi model = nạp lại toàn bộ (xem mục 13).

## 3. Luồng xử lý chính (file:hàm)

Tìm đoạn luật (`api/v1/internal.py:semantic_search`):
1. `api/deps.py:require_internal_token` kiểm tra `X-Internal-Token` (thiếu cấu hình -> 500, sai -> 401).
2. `services/search.py:search_chunks`:
   1. `query_split.py:split_question` (tắt bằng `QUERY_SPLIT_ENABLED=false`): câu nhiều ý thì danh sách tìm = [câu gốc, ý 1, ý 2...] (tối đa 3 ý).
   2. `query_expansion.py:expand` (tắt bằng `QUERY_EXPANSION_ENABLED=false`): **thêm** thuật ngữ luật vào cuối từng câu, không xóa từ gốc.
   3. `search.py:_embed_query_cached` (Gemini, task `RETRIEVAL_QUERY`, `lru_cache` 1024 câu); nhiều câu thì gọi song song bằng `ThreadPoolExecutor`.
   4. `repositories/document.py:semantic_search` cho từng vector: lấy `top_k x OVERFETCH(5)` đoạn gần nhất theo cosine (`<=>`, index HNSW); chỉ văn bản `status='active'`, `is_processed=true`, cùng `embedding_model`; có `doc_types` thì lọc thêm.
   5. `search.py:_interleave` xếp xen kẽ kết quả các câu (1 đoạn câu gốc, 1 đoạn ý 1...), bỏ đoạn trùng.
   6. `search.py:_diversify`: mỗi (văn bản, Điều) tối đa `MAX_PER_DIEU = 2` đoạn; thiếu `top_k` thì bù bằng đoạn đã bỏ, **không bao giờ trả ít hơn**.
   7. `score = 1 - khoảng cách cosine`, kẹp 0 đến 1.
3. Lỗi: `EmbeddingError` -> 503 `EMBEDDING_SERVICE_UNAVAILABLE`; `SQLAlchemyError` -> 503 `RAG_DATABASE_UNAVAILABLE`.

Khởi động: `main.py:warmup` mở sẵn kết nối Supabase và gọi thử 1 lần embedding (tắt bằng `WARMUP_ON_STARTUP=false`); lỗi chỉ ghi log `rag warmup lỗi (bỏ qua)`.

Quy tắc tách câu (`query_split.py`): cắt ở `? ; !`, xuống dòng, dấu chấm và từ nối `và / rồi / sau đó / đồng thời / ngoài ra`; dấu phẩy chỉ cắt khi đứng sau từ để hỏi. Ý quá ngắn ghép vào ý bên cạnh. Ý thiếu từ chỉ lĩnh vực (đất, nhà ở, doanh nghiệp...) thì mượn từ lĩnh vực của câu gốc, tránh ra nhầm Luật Doanh nghiệp. Chi phí: mỗi ý thêm 1 lượt embedding và 1 truy vấn DB; một ý lỗi Gemini thì cả lần tìm báo 503.

## 4. Bản đồ file

| File | Trách nhiệm |
|---|---|
| `app/main.py` | FastAPI app, `lifespan` + `warmup`, health, endpoint demo cũ, handler lỗi khung `{data,error,request_id}` |
| `app/api/v1/internal.py` | `/internal/v1/rag/search` |
| `app/api/v1/documents.py` | Router admin (`Depends(require_admin)`): `GET ""` danh sách, `GET /logs` nhật ký nạp (khai báo TRƯỚC `/{document_id}`, nếu không "logs" bị hiểu là id), `POST /preview`, `POST ""` tạo nháp, `GET`/`DELETE /{id}`, `POST /{id}/approve`, `POST /{id}/replace/preview`, `POST /{id}/replace`; tạo nháp, xóa nháp, yêu cầu duyệt, yêu cầu thay thành công thì gọi `ingest_log.record`. PATCH và reprocess mới là TODO |
| `app/api/deps.py` | `require_internal_token` (cho `/internal`); `get_current_user`, `require_admin` (cho `documents.py`) |
| `app/services/document_approval.py` | Việc nền của duyệt: `approve_document_background(id, actor_user_id=None, actor_role=None)` (đồng bộ, tự mở session; ba giai đoạn đọc / embed / ghi, xem mục 13; mọi lỗi thành `failed`, không ném ra ngoài; ghi `approve_done` hoặc `approve_failed` vào nhật ký); `_open_session` là chỗ test thay session (`ingest_log` dùng chung chỗ này); `recover_on_startup()` đặt văn bản còn `processing` thành `failed` |
| `app/services/document_replace.py` | Thay nội dung văn bản active. Hàm thuần: `reuse_plan` (khớp chunk cũ/mới theo chuỗi `embedding_text` + `embedding_model`, đa tập), `fill_vectors` (dùng lại vector cũ, một lần gọi embedder cho phần còn lại), `compare_chunks`, `replace_warnings`, `build_replace_preview`, `same_content`. Việc nền `replace_document_background(id, new_content, actor_user_id=None, actor_role=None)`: ba giai đoạn đọc / embed / ghi như duyệt (dùng chung `document_approval._open_session`, `_mark_failed`, `_failure_message`), mọi lỗi thành `ingest_status=failed` mà `status`, nội dung, chunk giữ nguyên, ghi `replace_done` hoặc `replace_failed` |
| `app/services/ingest_log.py` | `record(action, ...)`: ghi một dòng `ingest_log` bằng session riêng, commit riêng, **nuốt mọi lỗi** (chỉ cảnh báo); `mask_secrets(text)`: che khóa API, token, chuỗi kết nối (dùng chung với `ingest_error` của `document_approval`) |
| `app/services/document_preview.py` | Hàm thuần `analyze_document(text, title)`: chạy `chunk_legal_text` rồi thống kê (số Điều, cấp cắt, Điều bị tách, cảnh báo, ước tính embedding, 3 đoạn đầu); không đụng DB, không embedding |
| `app/services/search.py` | Luồng tìm kiếm, `_diversify`, `_interleave`, cache embedding |
| `app/services/query_split.py` | Tách câu nhiều ý |
| `app/services/query_expansion.py` | Bảng `SYNONYMS` (~30 nhóm đất đai + kinh doanh): từ đời thường -> thuật ngữ luật |
| `app/services/chunking.py` | Cắt văn bản theo Điều/Khoản/Điểm, `embedding_text` |
| `app/services/embedding.py` | `GeminiEmbedder`, `FakeEmbedder`, `get_embedder()`, `EmbeddingError` |
| `app/services/ingest.py` | `ingest_document`, `reembed_document` (chưa có script gọi) |
| `app/services/batch_ingest.py` | Nạp nhiều văn bản theo CSV |
| `app/repositories/document.py` | Truy vấn DB: tạo văn bản, thay chunk, `list_documents` (có `defer` `content`), `semantic_search` |
| `app/models/`, `app/schemas/` | `LegalDocument`, `DocumentChunk`, `IngestLog`, `DocumentVersion`; `SearchRequest`, `ChunkResult`; `DocumentListItem`, `DocumentList`, `DocumentPreviewRequest`, `IngestLogItem`, `IngestLogList`, `IngestAction`, `DocumentReplaceRequest` (`schemas/document.py`) |
| `app/core/` | `config.py`, `database.py`, `auth.py` |
| `database/001_rag_schema.sql`, `check_rag_db.sql` | Schema Supabase và script kiểm tra |
| `database/002_ingest_status.sql` | Thêm cột `ingest_status`, `ingest_error` (chạy lại nhiều lần được; **chạy trước khi build lại**) |
| `database/003_ingest_log.sql` | Tạo bảng `ingest_log` (chạy lại nhiều lần được; **chạy trước khi build lại**, sau 002) |
| `database/004_document_versions.sql` | Tạo bảng `document_versions`, mở rộng ràng buộc `ingest_log.action` thêm `replace_*` (chạy lại nhiều lần được; **chạy trước khi build lại**, sau 003) |
| `scripts/` | `ingest_file`, `ingest_batch`, `search_cli`, `eval_retrieval`, `demo_embedding`, `eval_questions.json`, `eval_natural.json`, `eval_multi_questions.json`, `danh_sach_luat.mau.csv` |
| `openapi.yaml` | Hợp đồng API |

## 5. Dữ liệu

- `legal_documents`: `title`, `doc_type` (`luat`, `nghi_dinh`, `thong_tu`, `quyet_dinh`, `khac`), `doc_number` (unique), `issuing_body`, `issued_date`, `effective_date`, `status` (`active`/`superseded`/`draft`; chỉ `active` được tìm), `source_url`, `content` (**toàn văn gốc**), `chunk_count`, `is_processed`, `ingest_status`, `ingest_error`. Index `(status, doc_type)`.
  - `ingest_status` (mặc định phía server là `done`, nên văn bản có sẵn và văn bản nạp bằng script đều là `done`): `pending` = bản nháp chưa duyệt; `processing` = đang cắt + embedding ở nền; `done` = đã xong; `failed` = lần duyệt gần nhất lỗi (duyệt lại được). `ingest_error` = thông báo lỗi ngắn (tối đa 500 ký tự, đã che khóa và chuỗi kết nối) của lần đó. Thêm bằng `database/002_ingest_status.sql`.
  - Luồng admin: `POST /documents` lưu `status=draft`, `ingest_status=pending` (không chunk) -> `POST /documents/{id}/approve` đặt `processing` (một câu UPDATE có điều kiện nên bấm hai lần không chạy embed hai lần), trả 202, việc nền cắt chunk + embedding -> thành công thì `status=active`, `ingest_status=done`; lỗi thì `status` giữ `draft`, `ingest_status=failed`. Chỉ xóa được văn bản `draft` không `processing` (`DELETE`).
- `ingest_log` (nhật ký nạp, thêm bằng `database/003_ingest_log.sql`, có RLS như 001): `id`, `created_at`, `actor_user_id` (`sub` của token người gọi, chuỗi tối đa 64), `actor_role`, `action`, `document_id` (**không có khóa ngoại**, văn bản bị xóa thì nhật ký còn nguyên), `doc_number`, `title`, `detail` (tối đa 500 ký tự, đã che khóa và chuỗi kết nối), `chunk_count`, `duration_ms`. Index `created_at desc` và `document_id`. `action` thuộc `create_draft` (tạo nháp thành công), `delete_draft` (xóa nháp thành công), `approve_requested` (yêu cầu duyệt được chấp nhận, trả 202), `approve_done` (duyệt xong, có `chunk_count`, `duration_ms`), `approve_failed` (duyệt lỗi, `detail` là thông báo lỗi đã che, có `duration_ms`), `replace_requested` (yêu cầu thay nội dung được chấp nhận, trả 202), `replace_done` (thay xong, `chunk_count` là số chunk mới, `detail` dạng "dùng lại N, embed M", có `duration_ms`), `replace_failed` (thay lỗi, `detail` là thông báo lỗi đã che, có `duration_ms`; ba giá trị `replace_*` thêm bằng `database/004_document_versions.sql`). Thao tác bị từ chối (404, 409, 422) và thao tác chỉ đọc không ghi; không ghi nội dung văn bản, khóa hay token. Xem qua `GET /documents/logs`.
- `document_versions` (bản nội dung CŨ mỗi lần thay, thêm bằng `database/004_document_versions.sql`, có RLS): `id`, `document_id` (**không có khóa ngoại**), `created_at`, `replaced_by_user_id` (`sub` của admin bấm thay), `content` (toàn văn cũ), `chunk_count` (số chunk cũ), `content_length`, `note`. Index `(document_id, created_at desc)`. Mỗi lần thay thành công thêm đúng một dòng, cùng giao dịch với việc thay chunk (thay lỗi thì không có dòng nào). Chưa có API đọc hay khôi phục từ bảng này.
- `document_chunks`: `document_id` (xóa văn bản thì xóa theo), `chunk_index`, `content` (đã kèm dòng "Điều X. ..."), `section_info` JSONB `{chuong, dieu, khoan, diem}`, `embedding vector(768)`, `embedding_model`. Unique `(document_id, chunk_index)`; **index HNSW cosine** trên `embedding`.
- Đổi schema: sửa `database/001_rag_schema.sql` (chạy lại nhiều lần được, dán vào Supabase SQL Editor) + `app/models/` + `check_rag_db.sql`. Thư mục `alembic/` mới chỉ có README, chưa có migration.
- Văn bản đã nạp (theo ghi nhận 21/10/2026, nên chạy `check_rag_db.sql` để xác nhận): Luật Đất đai 2024 (31/2024/QH15, bản gốc, chưa cập nhật Luật 43/2024), Luật Kinh doanh bất động sản 2023 (29/2023/QH15), Luật Doanh nghiệp 2020 (59/2020/QH14), Luật Đầu tư (DB đang có 143/2025/QH15, CSV mẫu còn ghi 61/2020), Luật Thương mại 2005 (36/2005/QH11), Luật Hợp tác xã 2023 (17/2023/QH15). **Chưa có nghị định, thông tư nào.**

## 6. Cấu hình

Giá trị mặc định lấy từ `app/core/config.py`. Trong Docker chỉ các biến có ở cột "Compose" mới được truyền; biến khác dùng mặc định trừ khi thêm vào `environment` của rag-service trong `docker-compose.yml`.

| Biến | Mặc định | Compose | Dùng ở đâu |
|---|---|---|---|
| `RAG_DATABASE_URL` | trống | có | `core/database.py` |
| `DB_CONNECT_TIMEOUT_SECONDS` | `10` | có | `database.engine_options`: `connect_args={"connect_timeout": N}`, chờ tối đa khi mở kết nối mới. `<= 0` = bỏ giới hạn (hành vi cũ) |
| `DB_COMMAND_TIMEOUT_SECONDS` | `20` | có | `database._apply_statement_timeout`: mỗi kết nối mới chạy `SET statement_timeout = N*1000` (sự kiện `connect`, có commit), truy vấn quá giờ bị server huỷ -> `psycopg.errors.QueryCanceled` -> `OperationalError` -> 503 `RAG_DATABASE_UNAVAILABLE`. `<= 0` = bỏ giới hạn. Dùng `SET` sau khi kết nối thay vì tham số `options` lúc mở kết nối vì pooler (pgbouncer, Supavisor) có thể từ chối tham số khởi động lạ |
| `DB_POOL_RECYCLE_SECONDS` | `300` | có | `database.engine_options`: `pool_recycle`, kết nối trong pool quá tuổi này thì bỏ và tạo lại. `<= 0` = không recycle. `pool_pre_ping=True` luôn bật (không có biến) |
| `INTERNAL_TOKEN` | trống | có | `api/deps.py`, khớp agent-service |
| `GEMINI_API_KEY` | trống | có | `embedding.py` |
| `EMBEDDING_PROVIDER` | `gemini` | có | `get_embedder()`; `fake` để thử không gọi mạng |
| `EMBEDDING_MODEL` | `gemini-embedding-001` | có | `embedding.py`; đổi thì nạp lại toàn bộ |
| `EMBEDDING_DIM` | `768` | có | phải khớp `vector(768)` |
| `EMBEDDING_BATCH_SIZE` | `20` | không | số đoạn mỗi request khi nạp |
| `EMBEDDING_TOKENS_PER_MINUTE` | `25000` | không | giảm tốc khi nạp (gói free), 0 = tắt |
| `QUERY_EXPANSION_ENABLED` | `true` | không | `search.py` |
| `QUERY_SPLIT_ENABLED` | `true` | không | `search.py` |
| `WARMUP_ON_STARTUP` | `true` | không | `main.py:lifespan` |
| `JWT_PUBLIC_KEY_PATH` | `keys/jwt_public.pem` | có | `core/auth.py`, kiểm JWT của endpoint admin `/api/v1/rag/documents` |
| `top_k` | do agent gửi (4) | | `SearchRequest`: 1 đến 20, mặc định 5 |

Ba biến `DB_*` dùng chung engine với script nạp văn bản (`ingest_batch.py`, `ingest_file.py` qua `get_session`): một câu SQL chạy lâu hơn `DB_COMMAND_TIMEOUT_SECONDS` sẽ bị huỷ, nên nạp lô lớn mà gặp lỗi `statement timeout` thì đặt `DB_COMMAND_TIMEOUT_SECONDS=0` cho lần chạy đó.

Lưu ý `top_k x 5` nên **<= 40** (`hnsw.ef_search` mặc định của pgvector), quá thì pgvector chỉ trả khoảng 40 đoạn. Đổi `.env` xong phải `docker compose up -d --force-recreate rag-service`.

## 7. Phụ thuộc service khác

| Service | Endpoint | Timeout | Lỗi thì xử lý |
|---|---|---|---|
| Gemini embedding | SDK `google-genai` | tìm: thử lại 2 lần (chờ 1s, 2s); nạp: chờ 5, 15, 30, 60, 60s | tìm: 503 `EMBEDDING_SERVICE_UNAVAILABLE`; nạp: dừng, không ghi dữ liệu dở |
| Supabase (rag DB) | `RAG_DATABASE_URL` | | 503 `RAG_DATABASE_UNAVAILABLE` |
| agent-service (người gọi) | gọi vào rag | agent chờ rag `RAG_TIMEOUT_SECONDS` = 30s | rag chậm hơn 30s thì agent trả 503 `RAG_SERVICE_ERROR` cho người dùng |

rag-service không gọi service nội bộ nào khác.

## 8. Công thức thêm tính năng

- **Thêm endpoint nội bộ:** schema (`app/schemas/`) -> service hoặc repository -> `api/v1/internal.py` (có `Depends(require_internal_token)`) -> test -> `openapi.yaml` **và** `rag_client.py` + `openapi.yaml` bên agent-service.
- **Thêm từ đời thường -> thuật ngữ luật:** thêm vào `SYNONYMS` (`query_expansion.py`), thêm câu vào `tests/test_query_expansion.py`, chạy `eval_retrieval.py --compare` để chắc không làm tụt điểm.
- **Thêm văn bản luật:** mục 13 (không cần sửa code).
- **Đổi cách xếp hạng / số đoạn:** sửa `search.py`; **không** thêm cột vào `select` của `semantic_search` mà không `defer`; chạy lại bộ đo (mục 14) và ghi số mới.
- **Thêm bộ lọc (vd `doc_numbers`):** thêm field vào `SearchRequest`, truyền xuống `semantic_search` (`.where(...)`), `openapi.yaml` của cả 2 service, agent truyền field đó.

## 9. Debug nhanh

| Triệu chứng | Xem log nào | File nghi ngờ |
|---|---|---|
| Tìm chậm | `rag timing embed=..s db=..s results=.. parts=N query='...'` | `embed` lớn: `embedding.py`, quota Gemini. `db` lớn (từng 15s): `repositories/document.py:semantic_search`, kiểm tra `defer`, `EXPLAIN` có dùng HNSW không |
| Agent báo 503 `RAG_SERVICE_ERROR` | log rag + log agent `RAG lỗi` | `INTERNAL_TOKEN` lệch (401), DB, vượt 30s |
| Agent `ReadTimeout` dù rag `healthy`, rag không ghi `rag timing` cho request đó (truy vấn DB treo, nghi kết nối Supabase chết ngầm; gặp 02/11/2026) | log agent `rag-service quá thời gian chờ`; từ khi có `DB_*`: log rag `Lỗi database khi tìm kiếm` (statement timeout / connect timeout) và rag trả 503 sau tối đa `DB_COMMAND_TIMEOUT_SECONDS` | `core/database.py`, `DB_COMMAND_TIMEOUT_SECONDS`, `DB_POOL_RECYCLE_SECONDS`; xem `pg_stat_activity` trên Supabase |
| 503 `EMBEDDING_SERVICE_UNAVAILABLE` | `Embedding câu hỏi thất bại` | hết quota / sai khóa Gemini |
| Tìm ra Luật Doanh nghiệp cho câu về đất | `parts=` và query | `query_split.py` (`_domains`), `query_expansion.py` |
| Tìm không ra gì / ít kết quả | | chunk `embedding_model` khác `EMBEDDING_MODEL`; văn bản `is_processed=false` hoặc `status!='active'` |
| Khởi động chậm / `rag warmup lỗi` | log khởi động | Supabase, `GEMINI_API_KEY` |

## 10. Chạy và test

```powershell
# Ở thư mục gốc legal-assistant-ai, dùng .venv của repo
.venv\Scripts\python -m pytest services/rag-service/tests -q
docker compose up -d --force-recreate rag-service
docker compose logs -f rag-service
python services/rag-service/scripts/search_cli.py "Chuyển nhượng đất cần giấy tờ gì?" --top-k 5
python services/rag-service/scripts/search_cli.py "Chuyển nhượng đất cần giấy tờ gì?" --max-chars 0   # in đủ nội dung đoạn (mặc định chỉ in 300 ký tự đầu, chunk không bị cắt)
```
Nạp và đo xem mục 13 và 14.

## 11. Hạn chế đã biết / nợ kỹ thuật

1. **Thiếu nghị định** (quan trọng nhất): chưa có Nghị định 101/2024/NĐ-CP (đăng ký đất đai, cấp Giấy chứng nhận) nên hỏi giấy tờ, hồ sơ, lệ phí hay ra "chưa đủ căn cứ". Nạp bằng `ingest_batch.py`.
2. **Chưa đo lại bộ đo** sau gộp đoạn trùng Điều, tách câu hỏi và `defer` (mục 14). Chưa có số `db=` sau khi sửa `defer`.
3. Bộ câu hỏi và bảng `SYNONYMS` cùng do một người soạn, 100% có thể cao hơn thực tế; nên thêm câu hỏi thật từ log vào `eval_questions.json`.
4. Chưa có lọc theo số hiệu (`doc_numbers`); `doc_types` rag đã làm nhưng agent chưa truyền.
5. API quản lý văn bản mới có danh sách, xem trước, tạo nháp, chi tiết, xóa nháp, duyệt (`documents.py`); chưa có sửa metadata (PATCH), reprocess, khôi phục bản cũ, đánh dấu `superseded`. Thay nội dung văn bản active đã có (mục 13, 5a1). Văn bản `active` chưa xóa được qua API. `reembed_document()` chưa có script gọi.
5a. Duyệt chạy bằng `BackgroundTasks` trong chính tiến trình API, **giả định rag-service chạy một tiến trình**. Build lại hoặc restart giữa lúc đang embed thì việc nền mất; khi khởi động `main.py:lifespan` đặt mọi văn bản `processing` thành `failed` ("Bị ngắt do service khởi động lại, hãy bấm duyệt lại") rồi admin bấm duyệt lại. Chạy nhiều bản thì bản khởi động sau sẽ nhầm việc đang chạy của bản kia thành bị ngắt. Chưa kiểm với Postgres thật và Gemini thật.
5a1. Thay nội dung văn bản active (`document_replace.py`): `id` của MỌI chunk đổi sau khi thay (`replace_chunks` xóa rồi tạo lại, kể cả chunk dùng lại vector cũ), nên trích dẫn cũ nào đã lưu `id` chunk cũ sẽ không còn khớp. Chưa có API đọc hay khôi phục từ `document_versions` (bản cũ chỉ nằm trong bảng, khôi phục bằng tay), chưa đánh dấu `superseded`, chưa giới hạn số bản cũ giữ lại. Xem trước dùng `EMBEDDING_MODEL` trong cấu hình còn việc thay thật dùng `embedder.model_name`: hai giá trị này khác nhau (ví dụ provider `fake`) thì số "dùng lại" của xem trước lệch so với thực tế. Khớp chunk theo chuỗi `embedding_text` giống hệt nên chỉ đổi một chữ trong Điều là cả Điều (cả chunk) phải embed lại; chunk bị cắt khác đi (Điều dài tách khác) cũng không khớp. Thông báo khôi phục khi khởi động ("hãy bấm duyệt lại") áp dụng cả cho thay nội dung bị ngắt (văn bản `active` còn `processing` thành `failed`, `status` giữ nguyên, bấm thay lại). Giai đoạn ghi mới kiểm bằng SQLite và giả lập dialect, chưa chạy trên Postgres/Supabase thật, chưa thử bộ luật dài và Gemini thật.
5a2. Nhật ký nạp (`ingest_log.py`) là việc PHỤ: `record` dùng session riêng, commit riêng và nuốt mọi lỗi (chưa chạy `003_ingest_log.sql`, DB chết...) chỉ ghi cảnh báo `Không ghi được nhật ký nạp ...` (không kèm thông báo lỗi gốc để khỏi lộ chuỗi kết nối), nên có thể thiếu dòng nhật ký mà việc chính vẫn xong. Mỗi lần ghi mở thêm một kết nối ngắn. Chưa có xóa hay lưu trữ nhật ký cũ (bảng chỉ lớn thêm). Giai đoạn ghi của việc duyệt (`SET LOCAL statement_timeout = 0`, một giao dịch) mới kiểm bằng SQLite và giả lập dialect: chưa chạy trên Postgres/Supabase thật, chưa thử với bộ luật dài thật (hàng trăm vector) nên chưa biết một lần ghi mất bao lâu và pooler (pgbouncer, Supavisor) có chấp nhận `SET LOCAL` như mong đợi không.
5b. Xem trước dùng `get_by_doc_number` nên kéo cả cột `content` của văn bản trùng số hiệu (một dòng, chưa `defer`). Thiếu header `Authorization` trả 401 mã `ERROR` (từ `HTTPBearer` của FastAPI, không phải `UNAUTHORIZED`).
6. Cache embedding riêng từng tiến trình, mất khi restart; chuyển Redis nếu chạy nhiều bản.
7. Chưa có tìm theo từ khóa (hybrid); đã bỏ `min_score` vì điểm đúng/sai quá sát nhau (0.65 đến 0.79).
8. Chữ "và" trong cụm danh từ cũng bị cắt thành 2 ý (tốn thêm 1 lần tìm, không làm kết quả kém đi). `query_split.py`.
9. Comment cũ trong `search.py` và `embedding.py` ghi "agent chỉ chờ rag 10 giây"; hiện agent chờ 30 giây (`RAG_TIMEOUT_SECONDS`).
10. Luật Đất đai nạp là bản gốc, chưa cập nhật Luật 43/2024 (còn nhắc "cấp huyện"); Luật Doanh nghiệp chưa có sửa đổi 2022, 2025.
11. Chống treo DB (`DB_*`, `core/database.py`) mới kiểm bằng mock, chưa thử với Supabase thật nên chưa biết có hết treo không. `statement_timeout` do SERVER thực thi: nếu kết nối chết ngầm ở tầng TCP (không còn gói tin nào), server không báo được và client vẫn chờ socket; khi đó chỉ `connect_timeout` (kết nối mới), `pool_recycle` (bỏ kết nối cũ trước khi chết) và `pool_pre_ping` giúp, còn kết nối đã chết mà đang dùng thì vẫn có thể treo tới khi TCP tự báo lỗi. Muốn chặn hẳn cần TCP keepalive hoặc `tcp_user_timeout` của libpq (chưa làm). Cũng chưa kiểm `SET statement_timeout` có chạy được qua pooler Supabase (session mode, cổng 5432) hay không; qua transaction mode (cổng 6543) thì `SET` ở mức session không đáng tin. Lỗi quá giờ chỉ được báo 503, không thử lại.

## 12. Sửa xong phải cập nhật gì

1. Đổi API / lỗi: `openapi.yaml` của rag-service và agent-service (nếu agent bị ảnh hưởng); mục 1, 3, 7.
2. Thêm / đổi biến cấu hình: `config.py`, `.env.example`, `docker-compose.yml` (nếu cần truyền vào container), mục 6.
3. Thêm / đổi file: mục 4. Đổi schema: `database/001_rag_schema.sql`, `check_rag_db.sql`, model, mục 5.
4. **Sửa `search.py`, `query_*.py`, `chunking.py`, `repositories/document.py`:** chạy `eval_retrieval.py` và ghi dòng mới vào mục 14 (ngày, lệnh, kết quả).
5. Đổi model embedding hoặc cách cắt đoạn: nạp lại toàn bộ (mục 13).
6. Chạy test, ghi số đạt + ngày + nhánh lên dòng đầu.

## 13. Ingest: nạp văn bản vào kho

`scripts/ingest_file.py` (1 văn bản) hoặc `scripts/ingest_batch.py` (nhiều văn bản theo CSV) gọi `ingest.py:ingest_document`:

1. **Đọc** file `.txt` UTF-8 toàn văn. Trùng `doc_number` thì báo `DocumentExistsError`; văn bản không cắt ra đoạn nào thì `EmptyDocumentError`.
2. **Cắt đoạn theo Điều/Khoản** (`chunking.py:chunk_legal_text`):
   - Chuẩn hóa chữ (NFC, bỏ khoảng trắng thừa, gộp dòng trống). Phần trước Điều đầu tiên (Quốc hiệu, "Căn cứ...") bị bỏ.
   - Mỗi **Điều** là 1 đoạn. Điều dài hơn `DEFAULT_MAX_CHARS = 2500` ký tự (khoảng 600 đến 900 token tiếng Việt, dưới giới hạn 2048 token của `gemini-embedding-001`) thì tách theo **Khoản**; Khoản dài tách theo **Điểm**; vẫn dài thì cắt theo dòng (`_hard_split`).
   - Đoạn con luôn có dòng "Điều X. Tên điều" ở đầu (và câu dẫn của Khoản khi tách theo Điểm) để đứng một mình vẫn hiểu.
   - Nhận Điều bằng mẫu `Điều <số>[chữ].` hoặc `:` (bắt buộc có `.` hoặc `:` để không nhầm câu "Điều 5 của Luật này..."). Văn bản không có "Điều ..." thì cắt theo đoạn văn.
   - Mỗi đoạn mang `section_info = {chuong, dieu, khoan, diem}` để agent tạo trích dẫn.
3. **Embedding** (`embedding.py:GeminiEmbedder.embed_documents`): model `gemini-embedding-001`, **768 chiều** (`output_dimensionality=768`, chuẩn hóa L2), task `RETRIEVAL_DOCUMENT`. Văn bản gửi đi là `embedding_text()` = tên văn bản + chương + nội dung đoạn. Gửi theo lô 20 đoạn, tự giảm tốc ở 25.000 token/phút (gói free), lỗi 429/500/503/504 thì thử lại sau 5, 15, 30, 60, 60 giây.
4. **Lưu** (`repositories/document.py`): tạo `legal_documents` (giữ toàn văn gốc ở `content`) và các `document_chunks` trong 1 transaction. Embedding xong hết mới ghi DB nên lỗi giữa chừng không để lại dữ liệu dở.

Nạp hàng loạt (`batch_ingest.py`, CSV mẫu `scripts/danh_sach_luat.mau.csv`, cột bắt buộc `file,title,doc_type,doc_number`): văn bản đã có (trùng số hiệu) thì bỏ qua nên chạy lại bao nhiêu lần cũng được; hết quota Gemini thì dừng gọn, lần sau nạp tiếp; dòng có `file` bắt đầu bằng `#` thì bỏ qua; `--dry-run` chỉ cắt đoạn và in thống kê (cảnh báo nếu có đoạn cắt theo "đoạn văn", thường do các dòng "Điều ..." bị dính nhau), không gọi Gemini, không ghi DB.

```bash
python services/rag-service/scripts/ingest_file.py duong_dan/van_ban.txt --title "Luật Đất đai 2024" --doc-number 31/2024/QH15 --dry-run
python services/rag-service/scripts/ingest_batch.py duong_dan/danh_sach_luat.csv --dry-run   # bỏ --dry-run để nạp thật
```
Cần `RAG_DATABASE_URL` + `GEMINI_API_KEY` trong `.env`; thêm `--fake-embedding` để thử không gọi Gemini. File luật gốc nằm ở `D:\AI_03\BE\data` (máy của nhóm, không trong repo).

Nạp qua API admin (nháp rồi duyệt): `POST /documents` chỉ lưu `legal_documents` với `status=draft`, `ingest_status=pending` (kiểm tra có cắt được ít nhất 1 đoạn, không chunk, không embedding, không gọi Gemini). `POST /documents/{id}/approve` trả 202 và chạy `document_approval.py:approve_document_background` ở nền, ba giai đoạn (không còn gọi `reembed_document`, hàm đó vẫn giữ cho script khác): (1) ĐỌC: mở session, đọc `id`, `title`, `content` rồi `rollback` ngay để kết thúc giao dịch; (2) EMBED: `chunk_legal_text`, `embedding_text`, `get_embedder().embed_documents` (có thể vài phút với bộ luật dài), session không ở trong giao dịch nào nên không giữ kết nối DB; văn bản không cắt ra đoạn nào thì `EmptyDocumentError`; (3) GHI: một giao dịch mới ngắn, nếu dialect là PostgreSQL thì câu đầu tiên là `SET LOCAL statement_timeout = 0` (chỉ trong giao dịch này; `statement_timeout` mặc định 20 giây của `database.py` có thể cắt một lần ghi hàng trăm vector, `scripts/replace_document.py` từng gặp lỗi này, mục 6), rồi nạp lại văn bản, `replace_chunks`, đặt `active`/`done`, commit MỘT lần (chunk và trạng thái cùng một giao dịch, nên commit hỏng thì không còn chunk dở). Lỗi bất kỳ ở giai đoạn nào thì rollback, `ingest_status=failed`, `ingest_error` ghi lý do, văn bản giữ `draft` nên không vào kết quả tìm; bấm duyệt lại để thử lại (chunk cũ bị ghi đè). Chi tiết trạng thái ở mục 5, giới hạn ở mục 11.

Thay nội dung văn bản đã active (luật được sửa đổi; bản trong API của `scripts/replace_document.py`, hai bên chạy độc lập): 
1. Xem trước `POST /documents/{id}/replace/preview` (chỉ đọc DB, không embedding, không tốn quota): cắt bản mới rồi so với chunk hiện có bằng `document_replace.py:compare_chunks`. Quy tắc khớp để dùng lại vector: hai chunk khớp khi **cùng** chuỗi `embedding_text(title, chunk)` (tên văn bản + chương + nội dung) và chunk cũ **cùng** `embedding_model` với model hiện tại (xem trước lấy `EMBEDDING_MODEL` trong cấu hình, việc thay thật lấy `embedder.model_name`); khớp theo đa tập, một chunk cũ chỉ khớp một chunk mới. Kết quả gồm cả `analyze_document` cho bản mới, `comparison` (số chunk cũ/mới/dùng lại/phải embed/bị bỏ, tối đa 50 số Điều đổi kèm cờ `changed_articles_truncated`, ước tính embedding chỉ cho phần phải embed) và `replace_warnings` (số Điều mới ít hơn cũ, độ dài chênh quá 30%, không chunk nào dùng lại được, nội dung giống hệt).
2. Thay thật `POST /documents/{id}/replace` (202): chỉ văn bản `active` có `ingest_status` `done` hoặc `failed`; nội dung giống hệt (sau `normalize_text`) thì 409 "không có thay đổi". `repositories/document.py:claim_for_replace` chiếm chỗ bằng một câu UPDATE có điều kiện (`ingest_status=processing`, `status` vẫn `active`), rồi ghi `replace_requested` và chạy `replace_document_background` ở nền, ba giai đoạn: (1) ĐỌC title, độ dài + băm SHA-256 của nội dung cũ và toàn bộ chunk cũ (kèm vector) rồi `rollback`; (2) EMBED không giữ giao dịch: chunk mới khớp chunk cũ thì dùng lại vector, phần còn lại gọi `embed_documents` MỘT lần theo đúng thứ tự (không có chunk nào cần embed thì không gọi embedder); (3) GHI một giao dịch ngắn, PostgreSQL thì câu đầu là `SET LOCAL statement_timeout = 0`: nạp lại văn bản, nếu không còn `active` hoặc nội dung khác bản đã đọc ở giai đoạn 1 thì không ghi gì và báo "Văn bản vừa bị đổi trong lúc xử lý, hãy thử lại."; lưu toàn văn CŨ vào `document_versions`, `replace_chunks`, đổi `content`, `ingest_status=done`, commit một lần. Suốt thời gian xử lý văn bản vẫn `active` và tìm kiếm vẫn dùng chunk cũ; chunk cũ chỉ bị thay ở giai đoạn 3. Lỗi ở giai đoạn nào cũng: `ingest_status=failed` + `ingest_error` (đã che khóa), `status`, nội dung và chunk giữ nguyên, ghi `replace_failed`; bấm thay lại để thử lại.

Nhật ký nạp (`services/ingest_log.py:record`, bảng `ingest_log`, xem qua `GET /documents/logs`): endpoint ghi `create_draft`, `delete_draft`, `approve_requested` sau khi thao tác thành công (thao tác bị từ chối thì không ghi); việc nền ghi `approve_done` hoặc `approve_failed` kèm `duration_ms` (và `chunk_count`). Người gọi lấy từ `sub` và `role` của token, truyền cho việc nền qua `actor_user_id`, `actor_role`.

**Đổi model embedding (hoặc `EMBEDDING_DIM`, hoặc cách cắt đoạn) thì phải nạp lại TOÀN BỘ dữ liệu.** Vector của hai model không so sánh được, và `semantic_search` chỉ so với đoạn có cùng `embedding_model`: đổi `EMBEDDING_MODEL` mà chưa nạp lại thì tìm kiếm trả về **rỗng**. Đổi số chiều còn phải sửa `vector(768)` trong SQL, `EMBEDDING_DIM` trong `models/chunk.py` và `.env`, tạo lại cột + index HNSW. Hàm `reembed_document()` (`ingest.py`) làm việc này cho 1 văn bản nhưng chưa có script gọi; hiện cách an toàn là xóa văn bản rồi `ingest_batch.py` lại. Quota Gemini gói free có thể khiến việc nạp lại kéo dài nhiều lần chạy.

## 14. Số liệu đo

Công cụ: `scripts/eval_retrieval.py`. Bộ 1 ý: `eval_questions.json` (20 câu đời thường, mỗi câu ghi văn bản và các Điều được coi là đúng; đáp án do Claude soạn, **nhóm nên duyệt lại**). Bộ nhiều ý: `eval_multi_questions.json` (10 câu, 22 ý). Bộ theo lời người dân thường hỏi: `eval_natural.json` (12 câu, cùng định dạng với bộ 20 câu, chạy bằng `--file`; đáp án theo yêu cầu giao việc 29/10/2026, **nhóm nên duyệt lại**). Chỉ số: `hit@4` (số câu có Điều đúng trong 4 đoạn đầu), `MRR` (càng gần 1 càng tốt), với bộ nhiều ý là `đủ mọi ý` và `phủ được X/22 ý`.

```bash
python services/rag-service/scripts/eval_retrieval.py --compare      # so sánh tắt/bật đổi từ đời thường
python services/rag-service/scripts/eval_retrieval.py --compare -v   # in từng câu trúng/trượt
python services/rag-service/scripts/eval_retrieval.py --multi        # so sánh tắt/bật tách câu hỏi
python services/rag-service/scripts/eval_retrieval.py --file services/rag-service/scripts/eval_natural.json -v   # chọn bộ câu khác
# hoặc Docker:
docker compose run --rm -v ./services/rag-service/scripts:/app/scripts rag-service python scripts/eval_retrieval.py --compare
docker compose run --rm -v ./services/rag-service/scripts:/app/scripts rag-service python scripts/eval_retrieval.py --file scripts/eval_natural.json --top-k 4 -v
```
`--file` mặc định là `eval_questions.json` (với `--multi` là `eval_multi_questions.json`). Trong Docker thư mục làm việc là `/app` nên đường dẫn của `--file` tính từ đó. Có thể thêm `--no-deps` để `docker compose run` không dựng service phụ thuộc.
Cần Supabase + Gemini thật (tốn quota); chạy ở thư mục gốc.

**Mốc hiện tại (lần đo gần nhất 29/10/2026; các dòng 19/10 trở xuống là mốc cũ, trước khi gộp đoạn trùng Điều và tách câu):**

| Ngày | Đo gì | Kết quả |
|---|---|---|
| 29/10/2026 | Docker, `eval_retrieval.py --top-k 4`, bộ 20 câu (cấu hình mặc định: BẬT đổi từ + tách câu) | hit@4 **20/20 (100%)**, MRR **0.90**, 0.73s/câu |
| 29/10/2026 | Như trên, `--top-k 10` | hit@10 **20/20 (100%)**, MRR **0.90**, 0.74s/câu |
| 29/10/2026 | Docker, `eval_retrieval.py --file scripts/eval_natural.json --top-k 4 -v`, bộ 12 câu lời người dân | hit@4 **9/12 (75%)**, MRR **0.61**, 0.90s/câu. Trượt: đất chưa có sổ đỏ bán được không; hàng xóm xây tường lấn sang đất; chuyển văn phòng sang quận khác |
| 29/10/2026 | Như trên, `--top-k 10` (không `-v`) | hit@10 **10/12 (83%)**, MRR **0.62**, 0.81s/câu; chưa biết 2 câu còn trượt là câu nào |
| 19/10/2026 | Bộ 20 câu, TẮT đổi từ | hit@4 **17/20 (85%)**, MRR **0.73**, 2.19s/câu. Trượt: tách sổ, gộp sổ, lên thổ cư |
| 19/10/2026 | Bộ 20 câu, BẬT đổi từ | hit@4 **20/20 (100%)**, MRR **0.90**, 2.49s/câu |
| 19/10/2026 | Khởi động sẵn (warmup) | `rag warmup xong sau 3.44s` |
| 16/10/2026 | Thời gian RAG trước khi sửa | câu đầu 5.57s, câu sau 1.61s |
| 20/10/2026 | Tra cứu nguyên văn qua Postman (Docker, Supabase thật) | "Điều 45 Luật Đất đai": 200, đủ 8 khoản; lần đầu 2.83s (tải toàn văn), lần sau 0.8s (đã cache) |
| 20/10/2026 | Tách câu hỏi, thử tay bằng `search_cli` (câu demo 3 ý, top_k=4) | ra Đ.172, Đ.220 k1, Đ.45 k1, Đ.220 k2: đủ cả 3 ý, không còn Luật Doanh nghiệp |

**Chưa có số đo cho:** `eval_retrieval.py --multi` (22 ý), nhánh TẮT đổi từ của `--compare` sau khi gộp đoạn trùng Điều (20/10) và tách câu (20/10) (nhánh BẬT đã có số ngày 29/10), và `db=` sau khi thêm `defer` (trước đó `db=15s`). Việc đầu tiên khi có kết nối Supabase + Gemini: chạy `--compare` và `--multi`, thêm dòng mới **ở đầu bảng** (ngày, lệnh, hit@4, MRR, giây/câu) để các lần sửa sau có mốc so sánh. Nếu bộ 20 câu một ý tụt sau khi bật tách câu thì xem lại quy tắc tách.

Nhận xét lần đo 19/10: đổi từ sửa được cả 3 câu trượt (sang tên #3 -> #1, sổ ghi sai tên #4 -> #1, đóng công ty #2 -> #1). Điểm `score` đoạn đúng và sai sát nhau nên `min_score` không dùng được.

Nhận xét lần đo 29/10 (số liệu gốc, chưa sửa gì ở tìm kiếm): bộ 20 câu giữ 100% / MRR 0.90 như 19/10, còn bộ 12 câu theo lời người dân chỉ 75% / MRR 0.61, đúng như nợ kỹ thuật số 3 (bộ 20 câu cùng người soạn với `SYNONYMS`). Ba câu trượt ở top 4: "đất chưa có sổ đỏ thì có bán được không" (cần Đ.45, ra Đ.137, Đ.24 Luật KDBĐS, Đ.256, Đ.148), "hàng xóm xây tường lấn sang đất nhà tôi" (cần Đ.235/236, ra Đ.139 hai lần, Đ.3, Đ.210) và "công ty chuyển văn phòng sang quận khác phải báo đâu" (cần Đ.30 Luật DN, ra Đ.45, Đ.31 hai lần, Đ.198; bộ 20 câu chấp nhận cả Đ.30 lẫn Đ.31 cho câu "đổi địa chỉ công ty", bộ này chỉ ghi Đ.30). `top-k 10` làm `top_k x 5` = 50, vượt mức 40 của `hnsw.ef_search` (mục 6), chưa kiểm chứng ảnh hưởng.
