-- RAG Service: bảng nhật ký nạp (ai làm gì, lúc nào, kết quả) cho luồng admin nạp luật.
-- Mỗi dòng là một thao tác thành công của admin (tạo nháp, xóa nháp, yêu cầu duyệt) hoặc kết quả việc duyệt chạy nền (xong, lỗi).
-- Chạy được nhiều lần (idempotent): đã có bảng / index thì bỏ qua.
-- Cách chạy: Supabase Dashboard (project của RAG_DATABASE_URL) -> SQL Editor -> dán toàn bộ file -> Run.
--
-- !!! CHẠY FILE NÀY TRƯỚC KHI BUILD LẠI rag-service (sau 002_ingest_status.sql) !!!
-- Chưa chạy mà build lại thì việc nạp vẫn chạy bình thường (ghi nhật ký chỉ cảnh báo trong log khi lỗi),
-- nhưng nhật ký trống và GET /api/v1/rag/documents/logs báo lỗi cơ sở dữ liệu.

create table if not exists ingest_log (
    id            uuid primary key default gen_random_uuid(),
    created_at    timestamptz  not null default now(),
    actor_user_id varchar(64),                       -- sub của token người gọi
    actor_role    varchar(20),
    action        varchar(30)  not null
                  check (action in ('create_draft', 'delete_draft', 'approve_requested', 'approve_done', 'approve_failed')),
    document_id   uuid,                              -- KHÔNG có khóa ngoại: xóa văn bản thì nhật ký vẫn còn nguyên
    doc_number    varchar(100),
    title         varchar(500),
    detail        text,                              -- ngắn (code cắt còn 500 ký tự), đã che khóa và chuỗi kết nối; không chứa nội dung văn bản
    chunk_count   integer,
    duration_ms   integer
);

create index if not exists idx_ingest_log_created_at
    on ingest_log (created_at desc);

create index if not exists idx_ingest_log_document_id
    on ingest_log (document_id);

-- Chặn truy cập qua Supabase REST API (anon key), giống 001. rag-service kết nối bằng user postgres
-- (chủ bảng) nên không bị ảnh hưởng.
alter table ingest_log enable row level security;
