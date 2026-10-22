# README-RAG: hiện trạng rag-service

File này ghi lại **hiện trạng** của rag-service sau mỗi lần sửa, để người sau (hoặc phiên chat sau) nắm nhanh mà không phải đọc lại toàn bộ code. Mỗi lần sửa: cập nhật mục "Hiện trạng" và thêm 1 mục vào "Nhật ký thay đổi".

Cập nhật lần cuối: 19/10/2026

---

## 1. RAG làm gì trong hệ thống

```
Người dùng hỏi trên web
  -> agent-service (/api/v1/agent/chat hoặc /conversations/...)
       1. lọc câu vô nghĩa (agent-service/app/services/query_filter.py)
       2. gọi rag-service POST /internal/v1/rag/search  {query, top_k=4}
             a. đổi từ đời thường sang thuật ngữ luật (query_expansion.py)   <- mới
             b. Gemini embedding câu hỏi (RETRIEVAL_QUERY, 768 chiều)
             c. pgvector tìm top_k đoạn gần nhất (cosine, index HNSW) trên Supabase
       3. gọi LLM (Groq Qwen, lỗi thì Gemini) với 4 đoạn luật vừa tìm
  -> trả câu trả lời
```

rag-service chỉ **tìm đoạn luật**, không tự trả lời. AI trả lời ở agent-service.

## 2. Hiện trạng (sau lần sửa 19/10/2026)

### Dữ liệu
- Database: Supabase riêng của rag-service (`RAG_DATABASE_URL` trong `.env` gốc), bảng `legal_documents` và `document_chunks` (`database/001_rag_schema.sql`).
- Văn bản đã nạp (theo `scripts/danh_sach_luat.mau.csv`, file gốc ở `D:\AI_03\BE\data`):

| Văn bản | Số hiệu | Ghi chú |
|---|---|---|
| Luật Đất đai 2024 | 31/2024/QH15 | Bản gốc, chưa cập nhật Luật 43/2024; còn nhắc "cấp huyện" |
| Luật Kinh doanh bất động sản 2023 | 29/2023/QH15 | |
| Luật Doanh nghiệp 2020 | 59/2020/QH14 | Bản gốc, chưa có sửa đổi 2022, 2025 |
| Luật Đầu tư | 143/2025/QH15 | DB đang có bản 2025 (thấy trong kết quả bộ đo 19/10); csv mẫu còn ghi 61/2020 |
| Luật Thương mại 2005 | 36/2005/QH11 | |
| Luật Hợp tác xã 2023 | 17/2023/QH15 | |

- **Chưa có nghị định, thông tư nào.** Đây là lý do chính chatbot hay trả lời "chưa đủ căn cứ" khi hỏi giấy tờ, hồ sơ, lệ phí (những thứ này nằm ở nghị định, không nằm ở luật). Nên bổ sung trước: Nghị định 101/2024/NĐ-CP (đăng ký đất đai, cấp Giấy chứng nhận).

### Cách chia đoạn (chunking, `app/services/chunking.py`, chưa sửa)
- Mỗi Điều là 1 đoạn; Điều dài hơn 2500 ký tự thì tách theo Khoản, rồi theo Điểm.
- Đoạn con luôn có dòng "Điều X. Tên điều" ở đầu.
- Khi tạo embedding, tên văn bản được thêm vào trước nội dung (`embedding_text`).

### Tìm kiếm (`app/services/search.py`)
- Câu hỏi được **thêm thuật ngữ luật** trước khi embedding (bật mặc định, tắt bằng `QUERY_EXPANSION_ENABLED=false`).
  Ví dụ: `mua đất cần gì` -> `mua đất cần gì (chuyển nhượng quyền sử dụng đất, nhận chuyển nhượng quyền sử dụng đất)`.
  Bảng từ ở `app/services/query_expansion.py` (khoảng 30 nhóm, đất đai + kinh doanh). Câu có dấu so có dấu, câu gõ không dấu so không dấu.
- Chỉ tìm theo ngữ nghĩa (vector). Chưa có tìm theo từ khóa (hybrid), chưa lọc theo điểm (`min_score`).
- Lọc theo loại văn bản (`doc_types`: `luat`, `nghi_dinh`, `thong_tu`, `quyet_dinh`, `khac`) đã hỗ trợ — agent-service truyền qua field `doc_types` trong request body. Chưa lọc theo số hiệu cụ thể (`doc_numbers` như `31/2024/QH15`).
- Điểm `score` = 1 - khoảng cách cosine, trong khoảng 0–1.
- Log mỗi lần tìm: `rag timing embed=..s db=..s results=.. query='...'` (xem bằng `docker compose logs rag-service`).

### Khởi động (`app/main.py`)
- Khi service bật, tự mở sẵn kết nối Supabase (`SELECT 1`) và gọi thử 1 lần Gemini embedding (`warmup`), để câu hỏi đầu tiên không bị chậm. Lỗi thì chỉ ghi log `rag warmup lỗi (bỏ qua)`, service vẫn chạy. Tắt bằng `WARMUP_ON_STARTUP=false`.
- Log khi xong: `rag warmup xong sau ..s`.

### Cấu hình (`app/core/config.py`)
| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `EMBEDDING_PROVIDER` | gemini | `fake` để test không gọi mạng |
| `EMBEDDING_MODEL` | gemini-embedding-001 | Đổi model thì phải nạp lại toàn bộ văn bản |
| `EMBEDDING_DIM` | 768 | Phải khớp `vector(768)` trong SQL |
| `QUERY_EXPANSION_ENABLED` | true | Thêm thuật ngữ luật vào câu hỏi |
| `WARMUP_ON_STARTUP` | true | Mở sẵn kết nối khi khởi động |
| `top_k` | do agent gửi | agent-service đang gửi 4 (`RAG_TOP_K`) |

### Số liệu đã đo
| Ngày | Đo gì | Kết quả |
|---|---|---|
| 16/10/2026 | Thời gian RAG (log agent, trước khi sửa) | Câu đầu 5.57s, câu sau 1.61s |
| 19/10/2026 | Khởi động sẵn (warmup) | `rag warmup xong sau 3.44s` (khoảng thời gian này không còn rơi vào câu hỏi đầu tiên) |
| 19/10/2026 | Bộ 20 câu, TẮT đổi từ đời thường | hit@4 **17/20 (85%)**, MRR **0.73**, 2.19s/câu. Trượt: tách sổ, gộp sổ, lên thổ cư |
| 19/10/2026 | Bộ 20 câu, BẬT đổi từ đời thường | hit@4 **20/20 (100%)**, MRR **0.90**, 2.49s/câu |

Nhận xét lần đo 19/10:
- Đổi từ sửa được cả 3 câu trượt và đẩy nhiều câu lên vị trí #1 (sang tên: #3 -> #1, sổ ghi sai tên: #4 -> #1, đóng công ty: #2 -> #1).
- Điểm `score` của đoạn đúng và đoạn sai rất sát nhau (khoảng 0.65–0.79), nên `min_score` **không** tách được đúng/sai, không nên làm.
- Nhiều câu có 4 đoạn cùng 1 Điều (ví dụ sang tên: 4 đoạn Đ.133, làm sổ lần đầu: 4 đoạn Đ.138), vì Điều dài bị chia theo Khoản. Như vậy LLM chỉ thấy 1 Điều, mất các Điều liên quan khác.
- Bộ câu hỏi và bảng từ cùng do Claude soạn, nên 100% có thể cao hơn thực tế. Nên thêm câu hỏi thật của người dùng (lấy từ log) vào bộ đo.

## 3. Cách đo chất lượng tìm kiếm

Bộ câu hỏi: `scripts/eval_questions.json` (20 câu đời thường, mỗi câu ghi văn bản và các Điều được coi là đúng). Đáp án do Claude soạn dựa trên mục lục các luật, **nhóm nên duyệt lại**.

```bash
# ở thư mục gốc legal-assistant-ai, đã bật venv
python services/rag-service/scripts/eval_retrieval.py --compare      # so sánh tắt/bật đổi từ
python services/rag-service/scripts/eval_retrieval.py --compare -v   # in từng câu trúng/trượt

# hoặc chạy bằng Docker
docker compose run --rm -v ./services/rag-service/scripts:/app/scripts rag-service python scripts/eval_retrieval.py --compare
```

Kết quả in ra: `hit@4` (số câu có Điều đúng trong 4 đoạn đầu) và `MRR` (càng gần 1 càng tốt). Thêm câu hỏi: thêm 1 dòng vào `eval_questions.json`.

## 4. Vấn đề đã biết / việc tiếp theo

1. **Thiếu nghị định** (quan trọng nhất): bổ sung Nghị định 101/2024 rồi nạp bằng `scripts/ingest_batch.py`.
2. ~~`min_score`~~: đã đo 19/10, điểm đúng/sai quá sát nhau, bỏ ý này.
3. Gộp các đoạn trùng Điều trong kết quả (ví dụ lấy dư rồi giữ tối đa 2 đoạn mỗi Điều) để 4 đoạn gửi LLM bao được nhiều Điều hơn.
4. Thêm câu hỏi thật của người dùng vào `eval_questions.json` để bộ đo khách quan hơn.
5. Lọc theo số hiệu văn bản cụ thể (`doc_numbers`, ví dụ `31/2024/QH15`) khi kho có nhiều văn bản hơn. (Lọc theo loại — `doc_types` — đã xong.)
6. Tìm theo từ khóa (hybrid) để sau: tốn công, ít lợi ích với câu hỏi đời thường.

## 5. Nhật ký thay đổi

### 19/10/2026: đổi từ đời thường, khởi động sẵn, bộ đo
- Thêm `app/services/query_expansion.py`: bảng từ đời thường -> thuật ngữ luật; `search_chunks` gọi `expand()` trước khi embedding.
- `app/services/search.py`: thêm log thời gian `rag timing`.
- `app/main.py`: thêm `lifespan` + `warmup()`.
- `app/core/config.py`: thêm `query_expansion_enabled`, `warmup_on_startup`.
- Thêm `scripts/eval_questions.json`, `scripts/eval_retrieval.py`.
- Thêm `tests/test_query_expansion.py`. Toàn bộ test rag-service: 80 đạt, 2 bỏ qua (test pgvector cần DB thật).
- Không đổi database, không đổi API (`/internal/v1/rag/search` giữ nguyên request/response).
- Sửa lỗi `eval_retrieval.py` chạy trong Docker (`IndexError` khi tìm `.env`).
- Kết quả đo: hit@4 17/20 -> 20/20, MRR 0.73 -> 0.90 (chi tiết ở bảng "Số liệu đã đo").
