# README-RAG: rag-service
Cập nhật: 28/10/2026 | Nhánh: `develop` | Test: 104 đạt, 2 bỏ qua (pgvector cần DB thật)

## 1. Service này làm gì

rag-service là **kho văn bản pháp luật có tìm kiếm**. Nó **không tự trả lời** người dùng; việc trả lời bằng AI nằm ở agent-service. Chỉ agent-service gọi endpoint nội bộ `/internal/v1/rag/search`, bằng header `X-Internal-Token`. Endpoint tra nguyên văn `POST /internal/v1/rag/articles` đã bỏ ngày 28/10/2026.

| Chức năng | Endpoint | File chính |
|---|---|---|
| Tìm đoạn luật theo ngữ nghĩa (vector) | `POST /internal/v1/rag/search` | `app/services/search.py` |
| Đổi từ đời thường sang thuật ngữ luật (trong search) | | `app/services/query_expansion.py` |
| Tách câu hỏi nhiều ý, tìm theo từng ý (trong search) | | `app/services/query_split.py` |
| Nạp văn bản vào kho (script, chưa có API) | `scripts/ingest_file.py`, `scripts/ingest_batch.py` | `ingest.py`, `chunking.py`, `embedding.py`, `batch_ingest.py` |
| Đo chất lượng tìm kiếm | `scripts/eval_retrieval.py` | `scripts/eval_questions.json` |
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
| `app/api/v1/documents.py` | Chỉ có TODO, **chưa gắn vào app** |
| `app/api/deps.py` | `require_internal_token` (đang dùng); `get_current_user`, `require_admin` (chưa endpoint nào dùng) |
| `app/services/search.py` | Luồng tìm kiếm, `_diversify`, `_interleave`, cache embedding |
| `app/services/query_split.py` | Tách câu nhiều ý |
| `app/services/query_expansion.py` | Bảng `SYNONYMS` (~30 nhóm đất đai + kinh doanh): từ đời thường -> thuật ngữ luật |
| `app/services/chunking.py` | Cắt văn bản theo Điều/Khoản/Điểm, `embedding_text` |
| `app/services/embedding.py` | `GeminiEmbedder`, `FakeEmbedder`, `get_embedder()`, `EmbeddingError` |
| `app/services/ingest.py` | `ingest_document`, `reembed_document` (chưa có script gọi) |
| `app/services/batch_ingest.py` | Nạp nhiều văn bản theo CSV |
| `app/repositories/document.py` | Truy vấn DB: tạo văn bản, thay chunk, `semantic_search` |
| `app/models/`, `app/schemas/` | `LegalDocument`, `DocumentChunk`; `SearchRequest`, `ChunkResult` |
| `app/core/` | `config.py`, `database.py`, `auth.py` |
| `database/001_rag_schema.sql`, `check_rag_db.sql` | Schema Supabase và script kiểm tra |
| `scripts/` | `ingest_file`, `ingest_batch`, `search_cli`, `eval_retrieval`, `demo_embedding`, `eval_questions.json`, `eval_multi_questions.json`, `danh_sach_luat.mau.csv` |
| `openapi.yaml` | Hợp đồng API |

## 5. Dữ liệu

- `legal_documents`: `title`, `doc_type` (`luat`, `nghi_dinh`, `thong_tu`, `quyet_dinh`, `khac`), `doc_number` (unique), `issuing_body`, `issued_date`, `effective_date`, `status` (`active`/`superseded`/`draft`; chỉ `active` được tìm), `source_url`, `content` (**toàn văn gốc**), `chunk_count`, `is_processed`. Index `(status, doc_type)`.
- `document_chunks`: `document_id` (xóa văn bản thì xóa theo), `chunk_index`, `content` (đã kèm dòng "Điều X. ..."), `section_info` JSONB `{chuong, dieu, khoan, diem}`, `embedding vector(768)`, `embedding_model`. Unique `(document_id, chunk_index)`; **index HNSW cosine** trên `embedding`.
- Đổi schema: sửa `database/001_rag_schema.sql` (chạy lại nhiều lần được, dán vào Supabase SQL Editor) + `app/models/` + `check_rag_db.sql`. Thư mục `alembic/` mới chỉ có README, chưa có migration.
- Văn bản đã nạp (theo ghi nhận 21/10/2026, nên chạy `check_rag_db.sql` để xác nhận): Luật Đất đai 2024 (31/2024/QH15, bản gốc, chưa cập nhật Luật 43/2024), Luật Kinh doanh bất động sản 2023 (29/2023/QH15), Luật Doanh nghiệp 2020 (59/2020/QH14), Luật Đầu tư (DB đang có 143/2025/QH15, CSV mẫu còn ghi 61/2020), Luật Thương mại 2005 (36/2005/QH11), Luật Hợp tác xã 2023 (17/2023/QH15). **Chưa có nghị định, thông tư nào.**

## 6. Cấu hình

Giá trị mặc định lấy từ `app/core/config.py`. Trong Docker chỉ các biến có ở cột "Compose" mới được truyền; biến khác dùng mặc định trừ khi thêm vào `environment` của rag-service trong `docker-compose.yml`.

| Biến | Mặc định | Compose | Dùng ở đâu |
|---|---|---|---|
| `RAG_DATABASE_URL` | trống | có | `core/database.py` |
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
| `JWT_PUBLIC_KEY_PATH` | `keys/jwt_public.pem` | có | chưa endpoint nào dùng |
| `top_k` | do agent gửi (4) | | `SearchRequest`: 1 đến 20, mặc định 5 |

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
5. Chưa có API quản lý văn bản (`documents.py` chỉ có TODO); `reembed_document()` chưa có script gọi.
6. Cache embedding riêng từng tiến trình, mất khi restart; chuyển Redis nếu chạy nhiều bản.
7. Chưa có tìm theo từ khóa (hybrid); đã bỏ `min_score` vì điểm đúng/sai quá sát nhau (0.65 đến 0.79).
8. Chữ "và" trong cụm danh từ cũng bị cắt thành 2 ý (tốn thêm 1 lần tìm, không làm kết quả kém đi). `query_split.py`.
9. Comment cũ trong `search.py` và `embedding.py` ghi "agent chỉ chờ rag 10 giây"; hiện agent chờ 30 giây (`RAG_TIMEOUT_SECONDS`).
10. Luật Đất đai nạp là bản gốc, chưa cập nhật Luật 43/2024 (còn nhắc "cấp huyện"); Luật Doanh nghiệp chưa có sửa đổi 2022, 2025.

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

**Đổi model embedding (hoặc `EMBEDDING_DIM`, hoặc cách cắt đoạn) thì phải nạp lại TOÀN BỘ dữ liệu.** Vector của hai model không so sánh được, và `semantic_search` chỉ so với đoạn có cùng `embedding_model`: đổi `EMBEDDING_MODEL` mà chưa nạp lại thì tìm kiếm trả về **rỗng**. Đổi số chiều còn phải sửa `vector(768)` trong SQL, `EMBEDDING_DIM` trong `models/chunk.py` và `.env`, tạo lại cột + index HNSW. Hàm `reembed_document()` (`ingest.py`) làm việc này cho 1 văn bản nhưng chưa có script gọi; hiện cách an toàn là xóa văn bản rồi `ingest_batch.py` lại. Quota Gemini gói free có thể khiến việc nạp lại kéo dài nhiều lần chạy.

## 14. Số liệu đo

Công cụ: `scripts/eval_retrieval.py`. Bộ 1 ý: `eval_questions.json` (20 câu đời thường, mỗi câu ghi văn bản và các Điều được coi là đúng; đáp án do Claude soạn, **nhóm nên duyệt lại**). Bộ nhiều ý: `eval_multi_questions.json` (10 câu, 22 ý). Chỉ số: `hit@4` (số câu có Điều đúng trong 4 đoạn đầu), `MRR` (càng gần 1 càng tốt), với bộ nhiều ý là `đủ mọi ý` và `phủ được X/22 ý`.

```bash
python services/rag-service/scripts/eval_retrieval.py --compare      # so sánh tắt/bật đổi từ đời thường
python services/rag-service/scripts/eval_retrieval.py --compare -v   # in từng câu trúng/trượt
python services/rag-service/scripts/eval_retrieval.py --multi        # so sánh tắt/bật tách câu hỏi
# hoặc Docker:
docker compose run --rm -v ./services/rag-service/scripts:/app/scripts rag-service python scripts/eval_retrieval.py --compare
```
Cần Supabase + Gemini thật (tốn quota); chạy ở thư mục gốc.

**Mốc hiện tại (lần đo gần nhất, 19/10/2026, chưa đo lại sau các thay đổi bên dưới):**

| Ngày | Đo gì | Kết quả |
|---|---|---|
| 19/10/2026 | Bộ 20 câu, TẮT đổi từ | hit@4 **17/20 (85%)**, MRR **0.73**, 2.19s/câu. Trượt: tách sổ, gộp sổ, lên thổ cư |
| 19/10/2026 | Bộ 20 câu, BẬT đổi từ | hit@4 **20/20 (100%)**, MRR **0.90**, 2.49s/câu |
| 19/10/2026 | Khởi động sẵn (warmup) | `rag warmup xong sau 3.44s` |
| 16/10/2026 | Thời gian RAG trước khi sửa | câu đầu 5.57s, câu sau 1.61s |
| 20/10/2026 | Tra cứu nguyên văn qua Postman (Docker, Supabase thật) | "Điều 45 Luật Đất đai": 200, đủ 8 khoản; lần đầu 2.83s (tải toàn văn), lần sau 0.8s (đã cache) |
| 20/10/2026 | Tách câu hỏi, thử tay bằng `search_cli` (câu demo 3 ý, top_k=4) | ra Đ.172, Đ.220 k1, Đ.45 k1, Đ.220 k2: đủ cả 3 ý, không còn Luật Doanh nghiệp |

**Chưa có số đo cho:** `eval_retrieval.py --multi` (22 ý), `--compare` sau khi gộp đoạn trùng Điều (20/10) và tách câu (20/10), và `db=` sau khi thêm `defer` (trước đó `db=15s`). Việc đầu tiên khi có kết nối Supabase + Gemini: chạy `--compare` và `--multi`, thêm dòng mới **ở đầu bảng** (ngày, lệnh, hit@4, MRR, giây/câu) để các lần sửa sau có mốc so sánh. Nếu bộ 20 câu một ý tụt sau khi bật tách câu thì xem lại quy tắc tách.

Nhận xét lần đo 19/10: đổi từ sửa được cả 3 câu trượt (sang tên #3 -> #1, sổ ghi sai tên #4 -> #1, đóng công ty #2 -> #1). Điểm `score` đoạn đúng và sai sát nhau nên `min_score` không dùng được.
