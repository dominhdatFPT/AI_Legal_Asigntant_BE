# README-RAG: rag-service

Tài liệu này mô tả rag-service **đang làm gì và làm như thế nào**, để người mới (hoặc phiên chat sau) nắm nhanh mà không phải đọc lại toàn bộ code.

- Muốn hiểu tổng quan: đọc mục 1 đến 3.
- Muốn sửa/thêm tính năng: đọc mục 4 (cách hoạt động), mục 8 (bản đồ file), mục 12 (sửa xong phải cập nhật gì).
- Cập nhật lần cuối: 21/10/2026 (khớp code nhánh `feature/legal-article-lookup`; test: 122 đạt, 2 bỏ qua).

---

## 1. Tóm tắt nhanh

rag-service là **kho văn bản pháp luật có tìm kiếm**. Nó **không tự trả lời** người dùng; việc trả lời bằng AI nằm ở agent-service.

| Việc rag-service làm | Dùng khi nào |
|---|---|
| **Tìm đoạn luật theo ngữ nghĩa** (`POST /internal/v1/rag/search`) | Người dùng hỏi tình huống ("sang tên sổ đỏ cần giấy tờ gì?"), agent lấy đoạn luật làm căn cứ cho AI |
| **Tra cứu nguyên văn 1 Điều/Khoản** (`POST /internal/v1/rag/articles`) | Người dùng gõ "Điều 45 Luật Đất đai", không cần AI diễn giải |
| **Nạp văn bản vào kho** (chạy script, chưa có API) | Dev/quản trị thêm luật mới |

Chỉ agent-service được gọi 2 endpoint trên, bằng header `X-Internal-Token`. Gateway chỉ mở `/api/rag/` tới `/api/v1/rag/...`, **không** mở `/internal/...` ra ngoài.

## 2. Vị trí trong hệ thống

```
Người dùng hỏi trên web
  -> gateway (nginx) /api/agent/...
  -> agent-service (POST /api/v1/agent/chat hoặc /conversations/{id}/messages)
       1. lọc câu vô nghĩa (query_filter.py)                         -> trả câu mặc định, hết
       2. câu tra cứu nguyên văn ("Điều 45 Luật Đất đai")
          -> rag POST /internal/v1/rag/articles                      -> trả nguyên văn, KHÔNG gọi AI
       3. còn lại: câu hỏi tình huống
          -> rag POST /internal/v1/rag/search {query, top_k=4}       -> 4 đoạn luật
          -> LLM (Groq, lỗi thì Gemini) trả lời dựa trên 4 đoạn đó
  -> trả câu trả lời + nguồn
```

rag-service dùng database Supabase riêng (`RAG_DATABASE_URL`) và Gemini embedding. Các service khác không đụng vào database này.

## 3. Chức năng chi tiết

| # | Chức năng | Điểm vào | Code chính | Trạng thái |
|---|---|---|---|---|
| 1 | Tìm đoạn luật theo ngữ nghĩa (vector) | `POST /internal/v1/rag/search` | `app/services/search.py` | Đang dùng |
| 1a | Đổi từ đời thường sang thuật ngữ luật | (trong chức năng 1) | `app/services/query_expansion.py` | Đang dùng, bật mặc định |
| 1b | Tách câu hỏi nhiều ý, tìm theo từng ý | (trong chức năng 1) | `app/services/query_split.py` | Đang dùng, bật mặc định |
| 1c | Gộp đoạn trùng Điều, cache embedding câu hỏi | (trong chức năng 1) | `search.py` (`_diversify`, `_embed_query_cached`) | Đang dùng |
| 1d | Lọc theo loại văn bản (`doc_types`) | field trong body search | `repositories/document.py` | rag nhận, agent **chưa truyền** |
| 2 | Tra cứu nguyên văn Điều/Khoản | `POST /internal/v1/rag/articles` | `app/services/article_lookup.py` | Đang dùng |
| 3 | Nạp 1 văn bản (cắt đoạn, embedding, lưu) | `scripts/ingest_file.py` | `services/ingest.py`, `chunking.py`, `embedding.py` | Dùng bằng script |
| 4 | Nạp nhiều văn bản theo file CSV | `scripts/ingest_batch.py` | `services/batch_ingest.py` | Dùng bằng script |
| 5 | Tìm thử, đo chất lượng tìm kiếm | `scripts/search_cli.py`, `scripts/eval_retrieval.py` | | Dùng bằng script |
| 6 | Health check | `GET /api/v1/rag/health` | `app/main.py` | Đang dùng (Docker healthcheck) |
| 7 | Endpoint demo cũ | `POST /api/v1/rag/search` | `app/main.py` | Trả kết quả mẫu cố định, chỉ để không vỡ test hệ thống. **Không phải tìm kiếm thật** |

**Chưa có:** API quản lý văn bản (`app/api/v1/documents.py` mới chỉ có TODO và **chưa được gắn vào app**; các hàm `get_current_user`, `require_admin` trong `app/api/deps.py` chưa endpoint nào dùng), lọc theo số hiệu văn bản (`doc_numbers`), tìm theo từ khóa (hybrid), `min_score`.

## 4. Cách hoạt động

### 4.1. Nạp văn bản (ingest)

`scripts/ingest_file.py` hoặc `scripts/ingest_batch.py` gọi `ingest_document()` (`app/services/ingest.py`):

1. Đọc file `.txt` UTF-8 (toàn văn). Kiểm tra trùng số hiệu (`doc_number`), trùng thì báo `DocumentExistsError`.
2. **Cắt đoạn** (`chunking.py`):
   - Chuẩn hóa chữ (NFC, bỏ khoảng trắng thừa, gộp dòng trống). Phần trước Điều đầu tiên (Quốc hiệu, "Căn cứ...") bị bỏ.
   - Mỗi **Điều** là 1 đoạn. Điều dài hơn 2500 ký tự (`DEFAULT_MAX_CHARS`) thì tách theo **Khoản**, Khoản dài thì tách theo **Điểm**, vẫn dài thì cắt theo dòng.
   - Đoạn con luôn có dòng "Điều X. Tên điều" ở đầu (và câu dẫn của Khoản khi tách theo Điểm) để đứng một mình vẫn hiểu.
   - Nhận Điều bằng mẫu `Điều <số>[chữ]. hoặc :` (bắt buộc có dấu `.` hoặc `:` để không nhầm câu "Điều 5 của Luật này...").
   - Văn bản không có "Điều ..." thì cắt theo đoạn văn.
   - Mỗi đoạn mang `section_info = {chuong, dieu, khoan, diem}` để agent tạo trích dẫn.
3. **Embedding** (`embedding.py`): gửi `embedding_text()` = tên văn bản + chương + nội dung đoạn, cho Gemini `gemini-embedding-001` (task `RETRIEVAL_DOCUMENT`, 768 chiều, tự chuẩn hóa L2). Gửi theo lô 20 đoạn; tự giảm tốc ở 25.000 token/phút (gói free); lỗi 429/500/503/504 thì thử lại sau 5, 15, 30, 60, 60 giây.
4. **Lưu** (`repositories/document.py`): tạo bản ghi `legal_documents` (giữ **toàn văn gốc** ở cột `content`) và các `document_chunks` trong 1 transaction. Embedding xong hết mới ghi DB, nên lỗi giữa chừng không để lại dữ liệu dở.

Nạp hàng loạt (`batch_ingest.py`, file CSV mẫu `scripts/danh_sach_luat.mau.csv`, cột bắt buộc `file,title,doc_type,doc_number`):
- Văn bản đã có (trùng số hiệu) thì bỏ qua, chạy lại bao nhiêu lần cũng được.
- Hết quota Gemini thì dừng gọn, lần sau chạy lại nạp tiếp phần còn thiếu.
- Dòng có cột `file` bắt đầu bằng `#` thì bỏ qua (tạm tắt 1 văn bản).
- `--dry-run`: chỉ cắt đoạn và in thống kê, không gọi Gemini, không ghi DB. Cảnh báo nếu có đoạn cắt theo "đoạn văn" (thường do các dòng "Điều ..." bị dính nhau).

`reembed_document()` (cắt và embedding lại 1 văn bản đã có, dùng khi đổi model) có trong `ingest.py` nhưng **chưa có script nào gọi**.

### 4.2. Tìm đoạn luật (`POST /internal/v1/rag/search`)

Hàm `search_chunks()` trong `app/services/search.py`, theo thứ tự:

1. **Tách ý** (`query_split.py`, tắt bằng `QUERY_SPLIT_ENABLED=false`). Câu nhiều ý thì danh sách tìm = [câu gốc, ý 1, ý 2...] (tối đa 3 ý). Câu 1 ý thì chỉ tìm câu gốc. Chi tiết quy tắc ở mục 4.2.1.
2. **Đổi từ đời thường** (`query_expansion.py`, tắt bằng `QUERY_EXPANSION_ENABLED=false`). Mỗi câu trong danh sách được **thêm** thuật ngữ luật vào cuối (không xóa từ gốc). Ví dụ: `mua đất cần gì` thành `mua đất cần gì (chuyển nhượng quyền sử dụng đất, nhận chuyển nhượng quyền sử dụng đất)`. Bảng từ `SYNONYMS` có khoảng 30 nhóm (đất đai + kinh doanh). Câu có dấu so có dấu, câu gõ không dấu so không dấu.
3. **Embedding câu hỏi** (Gemini, task `RETRIEVAL_QUERY`, thử lại ít vì agent chỉ chờ 10 giây). Có cache `lru_cache` 1024 câu (sau khi đã mở rộng). Nhiều câu thì gọi Gemini **song song**.
4. **Tìm pgvector** (`repositories/document.py: semantic_search`): với mỗi vector lấy `top_k x 5` đoạn gần nhất theo cosine (`<=>`, index HNSW). Chỉ xét văn bản `status = 'active'`, `is_processed = true`, và đoạn tạo bởi **cùng model embedding** với hiện tại. Có `doc_types` thì lọc thêm theo loại văn bản.
5. **Xếp xen kẽ** (`_interleave`): 1 đoạn câu gốc, 1 đoạn ý 1, 1 đoạn ý 2..., bỏ đoạn trùng. Nhờ vậy ý nào cũng có đoạn gần nhất của nó.
6. **Đa dạng hóa** (`_diversify`): mỗi cặp (văn bản, Điều) ưu tiên tối đa 2 đoạn (`MAX_PER_DIEU`); thiếu `top_k` thì bù bằng các đoạn đã bỏ qua, **không bao giờ trả ít hơn**. Đoạn không có số Điều thì không giới hạn. Kết quả sắp theo độ gần.
7. **Điểm** `score = 1 - khoảng cách cosine`, kẹp trong 0 đến 1.

Log mỗi lần tìm: `rag timing embed=..s db=..s results=.. parts=N query='...'` (xem bằng `docker compose logs rag-service`). `parts` là số lần tìm (câu gốc + các ý).

Lưu ý: `top_k x 5` nên **<= 40** (`hnsw.ef_search` mặc định của pgvector), quá thì pgvector chỉ trả khoảng 40 đoạn. Agent đang gửi `top_k = 4` nên an toàn.

#### 4.2.1. Tách câu hỏi nhiều ý (`query_split.py`)

- **Vì sao:** "Tôi muốn tách thửa đất rồi tặng cho con một phần, cần điều kiện gì và thời hạn sử dụng đất thế nào?" có 3 ý, nhưng tìm 1 lần theo cả câu thì cả 4 đoạn dồn vào 1 ý, 2 ý còn lại AI không có căn cứ.
- **Cách tách** (chỉ dùng quy tắc, không gọi AI): cắt ở `? ; !`, xuống dòng, dấu chấm hết câu và từ nối `và / rồi / sau đó / đồng thời / ngoài ra`. Dấu phẩy chỉ cắt khi đứng sau từ để hỏi (`không, gì, nào, sao, đâu, chưa, bao nhiêu, bao lâu`), nên câu kể bối cảnh ("Đất ở Bình Dương, diện tích 100m2, tách sổ được không") vẫn là 1 ý.
- Ý quá ít nội dung ("cần điều kiện gì", "phí bao nhiêu", "xin chào") ghép vào ý bên cạnh. Tối đa 3 ý (`MAX_PARTS`).
- Ý không còn từ chỉ lĩnh vực (đất, sổ đỏ, nhà ở, bất động sản, công ty, doanh nghiệp, hộ kinh doanh, hợp tác xã) thì mượn từ lĩnh vực của câu gốc, ghi trong ngoặc. Lý do (thử thật 20/10): ý "tặng cho con một phần, cần điều kiện gì" đứng riêng tìm ra Đ.53 Luật Doanh nghiệp (tặng cho phần vốn góp) thay vì luật đất đai.
- Ví dụ: câu trên thành `Tôi muốn tách thửa đất` | `tặng cho con một phần, cần điều kiện gì (thửa đất)` | `thời hạn sử dụng đất thế nào`.
- Chi phí: mỗi ý tốn thêm 1 lượt embedding (gói free tính theo request) và 1 truy vấn Supabase. Một ý lỗi Gemini thì cả lần tìm báo 503, không trả kết quả nửa vời.
- Hạn chế: chữ "và" trong cụm danh từ cũng bị cắt (vd "quyền sở hữu nhà ở **và** tài sản gắn liền với đất" thành 2 ý). Không làm kết quả kém đi vì câu gốc vẫn được tìm và xếp trước, chỉ tốn thêm 1 lần tìm.
- Lỗi đã gặp: so khớp từ sau khi bỏ dấu làm "sổ" trùng "sơ" (hồ sơ), "đỏ" trùng "đó", "con" trùng "còn". Cách xử lý: câu có dấu so có dấu; danh sách không dấu bỏ các từ dễ nhầm (con, ban, so, do, dang).

### 4.3. Tra cứu nguyên văn Điều/Khoản (`POST /internal/v1/rag/articles`)

Hàm `find_article()` trong `app/services/article_lookup.py`. **Không dùng embedding, không gọi Gemini.**

1. **Tìm văn bản** theo tên hoặc số hiệu trong `legal_documents`: không phân biệt hoa thường, có dấu hay không, "năm 2024" = "2024", "31/2024/QH15" so khớp theo số hiệu. Khớp nhiều bản thì lấy bản `active`; vẫn nhiều thì báo 409 kèm danh sách. Có năm mà kho không có năm đó thì thử bỏ năm, dùng bản đang có và ghi vào `note` ("Kho không có bản năm 2020, đang hiển thị ...").
2. **Cắt Điều/Khoản** từ **toàn văn gốc** (`legal_documents.content`) bằng chính bộ tách của `chunking.py` (`_split_articles`, `_group_by`), nên đúng từng chữ và không lặp dòng tiêu đề như chunk. Kết quả cắt được cache theo (id văn bản, `updated_at`) tối đa 16 văn bản; nạp lại văn bản thì tự cắt lại. Cache riêng từng tiến trình, mất khi restart.
3. Trùng số Điều trong cùng văn bản (do trích dẫn luật khác) thì giữ Điều đầu tiên.

Agent-service quyết định câu nào là "tra cứu" (xem README-AGENT.md, mục "Tra cứu nguyên văn").

### 4.4. Khởi động (`app/main.py`)

Khi service bật, `warmup()` mở sẵn kết nối Supabase (`SELECT 1`) và gọi thử 1 lần Gemini embedding, để câu hỏi đầu tiên không bị chậm. Lỗi thì chỉ ghi log `rag warmup lỗi (bỏ qua)`, service vẫn chạy. Tắt bằng `WARMUP_ON_STARTUP=false`. Log khi xong: `rag warmup xong sau ..s`.

### 4.5. Xác thực và lỗi chung

- 2 endpoint `/internal/v1/rag/*` yêu cầu header `X-Internal-Token` khớp `INTERNAL_TOKEN` (`app/api/deps.py`, so sánh an toàn thời gian). Chưa cấu hình `INTERNAL_TOKEN` thì trả 500; sai token trả 401.
- Mọi phản hồi theo khung `{"data": ..., "error": ..., "request_id": ...}`; `X-Request-ID` được giữ nguyên hoặc tự sinh.

## 5. API

Hợp đồng đầy đủ ở `openapi.yaml`.

### `POST /internal/v1/rag/search`

Request:
```json
{ "query": "Sang tên sổ đỏ cần giấy tờ gì?", "top_k": 4, "doc_types": ["luat"] }
```
- `query`: 1 đến 1000 ký tự. `top_k`: 1 đến 20, mặc định 5. `doc_types` (không bắt buộc): `luat`, `nghi_dinh`, `thong_tu`, `quyet_dinh`, `khac`.

Response 200:
```json
{ "data": { "chunks": [ {
    "id": "...", "content": "Điều 133. ...", "score": 0.78,
    "section_info": { "chuong": "XII", "dieu": "133", "khoan": null, "diem": null },
    "document": { "id": "...", "title": "Luật Đất đai 2024", "doc_number": "31/2024/QH15", "doc_type": "luat" }
} ] }, "error": null, "request_id": "..." }
```

### `POST /internal/v1/rag/articles`

Request:
```json
{ "law": "Luật Đất đai 2024", "dieu": "45", "khoan": "2" }
```
- `law`: 1 đến 200 ký tự (tên hoặc số hiệu). `dieu`: số Điều (`45`, `10a`). `khoan`: không bắt buộc.

Response 200: `data` gồm `document {id, title, doc_number, doc_type, status}`, `chuong`, `dieu`, `khoan`, `content` (nguyên văn), `note` (ghi chú nếu dùng bản khác năm).

### Mã lỗi

| HTTP | `error.code` | Khi nào |
|---|---|---|
| 401 | `UNAUTHORIZED` | Thiếu/sai `X-Internal-Token` |
| 422 | `VALIDATION_ERROR` | Body sai (kèm `details`) |
| 503 | `EMBEDDING_SERVICE_UNAVAILABLE` | Gemini embedding lỗi/bận (search) |
| 503 | `RAG_DATABASE_UNAVAILABLE` | Không truy cập được Supabase |
| 404 | `DOCUMENT_NOT_FOUND` | Không có văn bản (câu báo kèm danh sách văn bản đang có) |
| 404 | `ARTICLE_NOT_FOUND` | Không có Điều (vd "... không có Điều 999 (văn bản này có 260 Điều)") |
| 404 | `KHOAN_NOT_FOUND` | Không có Khoản |
| 409 | `DOCUMENT_AMBIGUOUS` | Tên khớp nhiều văn bản |
| 500 | `INTERNAL_ERROR` | Chưa cấu hình `INTERNAL_TOKEN` hoặc lỗi không lường trước |

## 6. Dữ liệu

Database: Supabase riêng của rag-service (`RAG_DATABASE_URL` trong `.env` gốc). Schema ở `database/001_rag_schema.sql` (chạy trong Supabase SQL Editor), kiểm tra bằng `database/check_rag_db.sql`.

- `legal_documents`: `title`, `doc_type`, `doc_number` (không trùng), `issuing_body`, `issued_date`, `effective_date`, `status` (`active`/`superseded`/`draft`; chỉ `active` được tìm), `source_url`, `content` (toàn văn gốc), `chunk_count`, `is_processed`.
- `document_chunks`: `document_id`, `chunk_index`, `content`, `section_info` (JSONB), `embedding vector(768)` (index HNSW cosine), `embedding_model`.

Văn bản đã nạp (theo `scripts/danh_sach_luat.mau.csv`, file gốc ở `D:\AI_03\BE\data`):

| Văn bản | Số hiệu | Ghi chú |
|---|---|---|
| Luật Đất đai 2024 | 31/2024/QH15 | Bản gốc, chưa cập nhật Luật 43/2024; còn nhắc "cấp huyện" |
| Luật Kinh doanh bất động sản 2023 | 29/2023/QH15 | |
| Luật Doanh nghiệp 2020 | 59/2020/QH14 | Bản gốc, chưa có sửa đổi 2022, 2025 |
| Luật Đầu tư | 143/2025/QH15 | DB đang có bản 2025 (thấy trong kết quả bộ đo 19/10); csv mẫu còn ghi 61/2020 |
| Luật Thương mại 2005 | 36/2005/QH11 | |
| Luật Hợp tác xã 2023 | 17/2023/QH15 | |

**Chưa có nghị định, thông tư nào.** Đây là lý do chính chatbot hay trả lời "chưa đủ căn cứ" khi hỏi giấy tờ, hồ sơ, lệ phí (những thứ này nằm ở nghị định, không nằm ở luật). Nên bổ sung trước: Nghị định 101/2024/NĐ-CP (đăng ký đất đai, cấp Giấy chứng nhận).

Đổi model embedding = phải nạp lại toàn bộ văn bản (vector của 2 model không so sánh được; tìm kiếm chỉ so với đoạn cùng `embedding_model`).

## 7. Cấu hình (`app/core/config.py`)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `RAG_DATABASE_URL` | (trống) | Chuỗi kết nối Supabase của rag-service |
| `INTERNAL_TOKEN` | (trống) | Phải khớp `INTERNAL_TOKEN` của agent-service |
| `GEMINI_API_KEY` | (trống) | Khóa Gemini dùng cho embedding |
| `JWT_PUBLIC_KEY_PATH` | `keys/jwt_public.pem` | Public key verify JWT (chưa endpoint nào dùng) |
| `EMBEDDING_PROVIDER` | `gemini` | `fake` để test không gọi mạng |
| `EMBEDDING_MODEL` | `gemini-embedding-001` | Đổi model thì phải nạp lại toàn bộ văn bản |
| `EMBEDDING_DIM` | `768` | Phải khớp `vector(768)` trong SQL và `EMBEDDING_DIM` trong `app/models/chunk.py` |
| `EMBEDDING_BATCH_SIZE` | `20` | Số đoạn mỗi request embedding khi nạp |
| `EMBEDDING_TOKENS_PER_MINUTE` | `25000` | Giảm tốc khi nạp để không vượt giới hạn gói free (0 = tắt) |
| `QUERY_EXPANSION_ENABLED` | `true` | Thêm thuật ngữ luật vào câu hỏi |
| `QUERY_SPLIT_ENABLED` | `true` | Câu nhiều ý thì tìm thêm theo từng ý |
| `WARMUP_ON_STARTUP` | `true` | Mở sẵn kết nối khi khởi động |
| `top_k` | do agent gửi | Agent đang gửi 4 (`RAG_TOP_K` bên agent-service) |

Lưu ý Docker: `docker-compose.yml` chỉ truyền cho rag-service các biến `RAG_DATABASE_URL`, `INTERNAL_TOKEN`, `GEMINI_API_KEY`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`. Muốn tắt/bật `QUERY_*`, `WARMUP_ON_STARTUP`... trong Docker phải thêm vào mục `environment` của rag-service. Đổi `.env` xong phải `docker compose up -d --force-recreate rag-service` (`restart` không nạp lại `.env`).

## 8. Bản đồ file

```
services/rag-service/
  app/
    main.py                  khởi tạo FastAPI, warmup, health, endpoint demo cũ, xử lý lỗi chung
    api/deps.py              X-Internal-Token (đang dùng), JWT/admin (chưa dùng)
    api/v1/internal.py       /internal/v1/rag/search và /articles
    api/v1/documents.py      chỉ có TODO, chưa gắn vào app
    services/search.py       luồng tìm kiếm (tách ý, embedding, pgvector, xen kẽ, đa dạng hóa)
    services/query_split.py  tách câu nhiều ý
    services/query_expansion.py  bảng từ đời thường -> thuật ngữ luật
    services/article_lookup.py   tra cứu nguyên văn Điều/Khoản
    services/chunking.py     cắt văn bản theo Điều/Khoản/Điểm
    services/embedding.py    GeminiEmbedder, FakeEmbedder, get_embedder()
    services/ingest.py       nạp 1 văn bản, reembed_document
    services/batch_ingest.py nạp theo CSV
    repositories/document.py truy vấn DB (tạo văn bản, thay chunk, semantic_search)
    models/                  LegalDocument, DocumentChunk
    schemas/                 SearchRequest/ChunkResult, ArticleRequest
    core/                    config.py, database.py, auth.py
  scripts/                   ingest_file, ingest_batch, search_cli, eval_retrieval, demo_embedding,
                             eval_questions.json, eval_multi_questions.json, danh_sach_luat.mau.csv
  database/                  001_rag_schema.sql, check_rag_db.sql
  tests/                     test từng module (chunking, ingest, search, article_lookup, ...)
  openapi.yaml               hợp đồng API
```

## 9. Cách chạy, test, đo

Chạy lệnh ở thư mục gốc `legal-assistant-ai`, dùng venv `.venv` của repo.

```powershell
# Test (cần venv; test pgvector bị bỏ qua vì cần DB thật)
cd services/rag-service
..\..\.venv\Scripts\python -m pytest -q

# Chạy bằng Docker và xem log
docker compose up -d rag-service
docker compose logs rag-service
```

Nạp văn bản (cần `RAG_DATABASE_URL` + `GEMINI_API_KEY` trong `.env`; thêm `--fake-embedding` để thử không gọi Gemini):
```bash
python services/rag-service/scripts/ingest_file.py duong_dan/van_ban.txt --title "Luật Đất đai 2024" --doc-number 31/2024/QH15 --dry-run
python services/rag-service/scripts/ingest_batch.py duong_dan/danh_sach_luat.csv --dry-run   # bỏ --dry-run để nạp thật
```

Tìm thử (không qua HTTP):
```bash
python services/rag-service/scripts/search_cli.py "Chuyển nhượng đất cần giấy tờ gì?" --top-k 5
```

### Đo chất lượng tìm kiếm

Bộ câu hỏi: `scripts/eval_questions.json` (20 câu đời thường, mỗi câu ghi văn bản và các Điều được coi là đúng). Đáp án do Claude soạn dựa trên mục lục các luật, **nhóm nên duyệt lại**.

```bash
python services/rag-service/scripts/eval_retrieval.py --compare      # so sánh tắt/bật đổi từ
python services/rag-service/scripts/eval_retrieval.py --compare -v   # in từng câu trúng/trượt

# hoặc chạy bằng Docker
docker compose run --rm -v ./services/rag-service/scripts:/app/scripts rag-service python scripts/eval_retrieval.py --compare
```

Kết quả in ra: `hit@4` (số câu có Điều đúng trong 4 đoạn đầu) và `MRR` (càng gần 1 càng tốt). Thêm câu hỏi: thêm 1 dòng vào `eval_questions.json`.

**Câu nhiều ý** (đo tính năng tách câu hỏi): `scripts/eval_multi_questions.json`, 10 câu ghép từ các câu trong `eval_questions.json` (8 câu 2 ý, 2 câu 3 ý, tổng 22 ý). Đáp án Điều của từng ý lấy nguyên từ `eval_questions.json`.

```bash
python services/rag-service/scripts/eval_retrieval.py --multi       # so sánh tắt/bật tách câu hỏi
python services/rag-service/scripts/eval_retrieval.py --multi -v    # in từng câu: phủ mấy ý, tìm được Điều nào
```

Kết quả in ra: `đủ mọi ý` (số câu mà ý nào cũng có đoạn đúng trong top_k) và `phủ được X/22 ý`.

## 10. Số liệu đã đo

| Ngày | Đo gì | Kết quả |
|---|---|---|
| 16/10/2026 | Thời gian RAG (log agent, trước khi sửa) | Câu đầu 5.57s, câu sau 1.61s |
| 19/10/2026 | Khởi động sẵn (warmup) | `rag warmup xong sau 3.44s` (khoảng thời gian này không còn rơi vào câu hỏi đầu tiên) |
| 19/10/2026 | Bộ 20 câu, TẮT đổi từ đời thường | hit@4 **17/20 (85%)**, MRR **0.73**, 2.19s/câu. Trượt: tách sổ, gộp sổ, lên thổ cư |
| 19/10/2026 | Bộ 20 câu, BẬT đổi từ đời thường | hit@4 **20/20 (100%)**, MRR **0.90**, 2.49s/câu |
| 20/10/2026 | Tra cứu nguyên văn qua Postman (Docker, Supabase thật) | "Điều 45 Luật Đất đai": 200, đủ 8 khoản; lần đầu 2.83s (tải toàn văn), lần sau 0.8s (đã cache) |

Nhận xét lần đo 19/10:
- Đổi từ sửa được cả 3 câu trượt và đẩy nhiều câu lên vị trí #1 (sang tên: #3 -> #1, sổ ghi sai tên: #4 -> #1, đóng công ty: #2 -> #1).
- Điểm `score` của đoạn đúng và đoạn sai rất sát nhau (khoảng 0.65 đến 0.79), nên `min_score` **không** tách được đúng/sai, không nên làm.
- Nhiều câu có 4 đoạn cùng 1 Điều (ví dụ sang tên: 4 đoạn Đ.133, làm sổ lần đầu: 4 đoạn Đ.138), vì Điều dài bị chia theo Khoản. Việc này dẫn tới tính năng "gộp đoạn trùng Điều" (đã làm 20/10, chưa đo lại).
- Bộ câu hỏi và bảng từ cùng do Claude soạn, nên 100% có thể cao hơn thực tế. Nên thêm câu hỏi thật của người dùng (lấy từ log) vào bộ đo.

## 11. Vấn đề đã biết / việc tiếp theo

1. **Thiếu nghị định** (quan trọng nhất): bổ sung Nghị định 101/2024 rồi nạp bằng `scripts/ingest_batch.py`.
2. **Chạy lại bộ đo** `eval_retrieval.py --compare` (sau khi gộp đoạn trùng Điều) và `--multi` (đo tách câu hỏi), ghi số vào mục 10. Nếu bộ 20 câu một ý bị giảm sau khi bật tách câu thì xem lại quy tắc tách.
3. Thêm câu hỏi thật của người dùng vào `eval_questions.json` để bộ đo khách quan hơn.
4. Lọc theo số hiệu văn bản cụ thể (`doc_numbers`, ví dụ `31/2024/QH15`) khi kho có nhiều văn bản hơn. (Lọc theo loại, `doc_types`, rag đã xong nhưng agent chưa truyền.)
5. API quản lý văn bản (`documents.py`) và script gọi `reembed_document()` nếu cần đổi model.
6. Cache embedding và cache cắt Điều đều riêng từng tiến trình, mất khi restart; chuyển Redis nếu chạy nhiều bản rag-service.
7. Tìm theo từ khóa (hybrid) để sau: tốn công, ít lợi ích với câu hỏi đời thường.
8. Đã bỏ: `min_score` (đo 19/10, điểm đúng/sai quá sát nhau).

## 12. Khi sửa code, cập nhật tài liệu này thế nào

1. Sửa mục liên quan trong phần 3 đến 7 cho khớp code mới (chức năng, cách hoạt động, API, cấu hình, bản đồ file).
2. Thêm 1 mục **đầu tiên** vào "Nhật ký thay đổi" bên dưới: ngày, file đã sửa, test (số đạt/bỏ qua), có đổi database/API không, lỗi gặp khi làm.
3. Chạy test, ghi số test đạt ở dòng "Cập nhật lần cuối" trên cùng. Có chạy bộ đo thì thêm 1 dòng vào mục 10.
4. Đổi API thì sửa cả `openapi.yaml` và README-AGENT.md nếu agent-service bị ảnh hưởng.

## 13. Nhật ký thay đổi

### 21/10/2026: viết lại README-RAG
- Sắp xếp lại theo chức năng, cách hoạt động, API, dữ liệu, cấu hình, bản đồ file, cách chạy/đo. Nội dung nhật ký và số liệu đo giữ nguyên từ bản cũ. Bổ sung đối chiếu với code: endpoint demo cũ, `documents.py` chưa gắn vào app, `reembed_document()` chưa có script, các biến cấu hình `docker-compose.yml` thực sự truyền. Không đổi code.
- Test lúc viết: rag-service 122 đạt, 2 bỏ qua; agent-service 104 đạt.

### 20/10/2026: tra cứu nguyên văn điều luật (nhánh `feature/legal-article-lookup`, nối tiếp `feature/rag-multi-question`)
- rag-service: thêm `app/services/article_lookup.py`, `app/schemas/article.py`, endpoint `POST /internal/v1/rag/articles` trong `app/api/v1/internal.py`, mục mới trong `openapi.yaml` (`getArticle`, `ArticleRequest`, `ArticleResponse`). Thêm `tests/test_article_lookup.py` (20 test). Toàn bộ test rag-service: 122 đạt, 2 bỏ qua.
- agent-service: thêm `app/services/article_lookup.py` (nhận diện câu tra cứu, định dạng nguyên văn); `rag_client.py` thêm `get_article()` + `ArticleNotFound`, gom phần gọi HTTP chung vào `_post()`; `agent.py` rẽ nhánh tra cứu ngay sau bộ lọc câu vô nghĩa. Thêm `tests/test_article_lookup.py` (25 test). Toàn bộ test agent-service: 104 đạt.
- Test chung của repo: `tests/test_openapi.py` 31 đạt; `tests/test_api.py` + `tests/test_gateway_config.py` phần rag/agent 11 đạt.
- Không đổi database. API cũ giữ nguyên. Không cần build lại frontend.
- Đã thử thật 20/10 qua Postman (Docker, Supabase thật): `POST /api/agent/conversations/{id}/messages` với "Điều 45 Luật Đất đai" -> 200, nguyên văn đủ 8 khoản Điều 45, metadata `type=article_lookup`, `model=null`, câu hỏi + trả lời được lưu. Lần đầu 2.83s (tải toàn văn từ Supabase), lần sau 0.8s (đã cache).
- Đã thử offline trên toàn văn thật trong `D:\AI_03\BE\data`: Luật Đất đai Đ.45 k1, Đ.220 k2, Đ.260 (Điều cuối), Luật Doanh nghiệp Đ.38 đều đúng; Đ.999 và "luật hôn nhân" báo lỗi đúng.

### 20/10/2026: tách câu hỏi nhiều ý (nhánh `feature/rag-multi-question`)
- Thêm `app/services/query_split.py`: `split_question()` tách câu nhiều ý theo quy tắc (không gọi AI).
- `app/services/search.py`: tìm cho câu gốc + từng ý, embedding song song (`ThreadPoolExecutor`), xếp xen kẽ bằng `_interleave()` rồi qua `_diversify()` như cũ; log thêm `parts=`.
- `app/core/config.py`: thêm `query_split_enabled` (biến `QUERY_SPLIT_ENABLED`, mặc định true).
- `scripts/eval_retrieval.py`: thêm `--multi`; thêm `scripts/eval_multi_questions.json` (10 câu, 22 ý).
- Thêm `tests/test_query_split.py` (13 test) và `tests/test_search_multi.py` (4 test). Toàn bộ test rag-service: 102 đạt, 2 bỏ qua.
- Lỗi gặp khi viết: so khớp từ sau khi bỏ dấu làm "sổ" trùng "sơ" (hồ sơ), "đỏ" trùng "đó", "con" trùng "còn", nên "tách sổ đỏ cho con" bị coi là rỗng. Đã sửa: câu có dấu so có dấu, danh sách không dấu bỏ các từ dễ nhầm (con, ban, so, do, dang).
- Thử thật 1 câu (search_cli, câu demo 3 ý): bản đầu tìm đúng Đ.220 (tách thửa) và Đ.172 (thời hạn) nhưng ý tặng cho ra Đ.53 Luật Doanh nghiệp. Đã sửa bằng cách cho ý thiếu lĩnh vực mượn từ lĩnh vực của câu gốc (`_domains()`). Chạy lại search_cli (top_k=4): Đ.172, Đ.220 k1, **Đ.45 k1** (điều kiện tặng cho quyền sử dụng đất), Đ.220 k2, đủ cả 3 ý, không còn Luật Doanh nghiệp.
- Không đổi database, không đổi API, agent-service không phải sửa.
- Chưa đo trên dữ liệu thật (`--multi`, `--compare`): máy viết code không kết nối được Supabase/Gemini.

### 20/10/2026: gộp đoạn trùng Điều, cache embedding câu hỏi
- `app/services/search.py`: thêm `_diversify()` (ưu tiên mỗi Điều tối đa 2 đoạn, thiếu thì bù, lấy dư x5) và `_embed_query_cached()` (`lru_cache` 1024).
- Thêm `tests/test_search_diversify.py` (5 test). Toàn bộ test rag-service: 85 đạt, 2 bỏ qua.
- Lỗi gặp khi thử bản đầu (lấy dư x3, không bù): câu "sang tên sổ đỏ" chỉ ra 2 đoạn vì cả 12 đoạn đều thuộc Đ.133. Đã sửa bằng cách bù đủ `top_k` và lấy dư x5.
- Không đổi database, không đổi API, không đổi `repositories/document.py`.
- Sửa mô tả `doc_types` cho đúng code: agent-service chưa truyền field này.
- Chưa đo lại hit@4 / MRR sau thay đổi.

### 19/10/2026: đổi từ đời thường, khởi động sẵn, bộ đo
- Thêm `app/services/query_expansion.py`: bảng từ đời thường -> thuật ngữ luật; `search_chunks` gọi `expand()` trước khi embedding.
- `app/services/search.py`: thêm log thời gian `rag timing`.
- `app/main.py`: thêm `lifespan` + `warmup()`.
- `app/core/config.py`: thêm `query_expansion_enabled`, `warmup_on_startup`.
- Thêm `scripts/eval_questions.json`, `scripts/eval_retrieval.py`.
- Thêm `tests/test_query_expansion.py`. Toàn bộ test rag-service: 80 đạt, 2 bỏ qua (test pgvector cần DB thật).
- Không đổi database, không đổi API (`/internal/v1/rag/search` giữ nguyên request/response).
- Sửa lỗi `eval_retrieval.py` chạy trong Docker (`IndexError` khi tìm `.env`).
- Kết quả đo: hit@4 17/20 -> 20/20, MRR 0.73 -> 0.90 (chi tiết ở mục 10).
