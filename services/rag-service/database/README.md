# RAG database

Database riêng của RAG Service (Supabase PostgreSQL + **pgvector**), gồm văn bản pháp luật, các đoạn (chunk) và embedding. Không dùng chung bảng với service khác.

## Bảng

| Bảng | Nội dung |
|---|---|
| `legal_documents` | Một dòng = một văn bản (Luật, Nghị định...). Có `status` (`active` / `superseded` / `draft`), giữ toàn văn trong `content` để embedding lại khi đổi model. |
| `document_chunks` | Một dòng = một đoạn (thường là 1 Điều, Điều dài thì 1 Khoản hoặc 1 Điểm). `embedding vector(768)`, `section_info` (chương/điều/khoản/điểm) để trích dẫn, `embedding_model` để biết vector tạo bằng model nào. |

Index `idx_document_chunks_embedding_hnsw` (HNSW, `vector_cosine_ops`) giúp tìm gần đúng nhanh theo khoảng cách cosine.

## Tạo bảng trên Supabase

1. Supabase Dashboard → project của `RAG_DATABASE_URL` → **SQL Editor**.
2. Dán toàn bộ `001_rag_schema.sql` → **Run** (chạy lại nhiều lần không sao).
3. Dán `check_rag_db.sql` → **Run**. Dòng 1–9 phải là `OK`.

## Embedding

- Model: `gemini-embedding-001`, rút còn **768 chiều** (`output_dimensionality=768`), gói free của Gemini API.
- Nạp văn bản dùng `task_type=RETRIEVAL_DOCUMENT`, câu hỏi dùng `RETRIEVAL_QUERY`.
- Đổi số chiều = sửa `vector(768)` trong schema + `EMBEDDING_DIM` trong `app/models/chunk.py` và `.env`, rồi nạp lại toàn bộ.
- Đổi model (cùng số chiều) = chạy embedding lại các văn bản (`reembed_document` trong `app/services/ingest.py`). Tìm kiếm chỉ so với chunk có `embedding_model` trùng model đang dùng, nên không bị lẫn.

## Nạp văn bản

```powershell
# Xem trước cách cắt đoạn (không gọi API, không ghi database)
python services/rag-service/scripts/ingest_file.py van_ban.txt --title "Luật Đất đai 2024" --dry-run

# Nạp thật
python services/rag-service/scripts/ingest_file.py van_ban.txt --title "Luật Đất đai 2024" `
    --doc-type luat --doc-number 31/2024/QH15 --issuing-body "Quốc hội" --effective-date 2025-01-01

# Tìm thử
python services/rag-service/scripts/search_cli.py "Chuyển nhượng đất cần điều kiện gì?"
```
