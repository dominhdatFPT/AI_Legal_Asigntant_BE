-- RAG Service: lưu bản nội dung CŨ của văn bản mỗi khi admin thay nội dung (POST /rag/documents/{id}/replace),
-- và thêm ba giá trị replace_requested, replace_done, replace_failed vào ràng buộc action của nhật ký nạp (ingest_log).
-- Chạy được nhiều lần (idempotent): đã có bảng / index thì bỏ qua; ràng buộc action được bỏ rồi tạo lại.
-- Cách chạy: Supabase Dashboard (project của RAG_DATABASE_URL) -> SQL Editor -> dán toàn bộ file -> Run.
--
-- !!! CHẠY FILE NÀY TRƯỚC KHI BUILD LẠI rag-service (sau 002_ingest_status.sql và 003_ingest_log.sql) !!!
-- Phần ingest_log cần bảng đó đã có (file 003), nếu chưa chạy 003 thì khối do $$ bên dưới báo lỗi "relation ingest_log does not exist".
-- Build lại khi chưa chạy file này thì thay nội dung văn bản báo lỗi ở giai đoạn ghi (không có bảng document_versions,
-- văn bản và chunk cũ giữ nguyên), và nhật ký replace_* bị từ chối bởi ràng buộc (chỉ cảnh báo trong log, không làm hỏng việc chính).

create table if not exists document_versions (
    id                  uuid primary key default gen_random_uuid(),
    document_id         uuid         not null,        -- KHÔNG có khóa ngoại: xóa văn bản thì các bản cũ vẫn còn
    created_at          timestamptz  not null default now(),
    replaced_by_user_id varchar(64),                  -- sub của token admin bấm thay
    content             text         not null,        -- toàn văn CŨ bị thay
    chunk_count         integer,                      -- số chunk của bản cũ
    content_length      integer      not null,        -- số ký tự của content
    note                text                          -- ghi chú ngắn, tùy chọn
);

create index if not exists idx_document_versions_document_id_created_at
    on document_versions (document_id, created_at desc);

-- Chặn truy cập qua Supabase REST API (anon key), giống 001 và 003. rag-service kết nối bằng user postgres
-- (chủ bảng) nên không bị ảnh hưởng.
alter table document_versions enable row level security;

-- ingest_log.action: bỏ ràng buộc kiểm tra cũ (tên mặc định của Postgres cho check nội tuyến ở 003 là ingest_log_action_check;
-- tìm theo nội dung ràng buộc để vẫn đúng nếu tên khác) rồi tạo lại với 8 giá trị.
do $$
declare
    c record;
begin
    for c in
        select conname
        from pg_constraint
        where conrelid = 'ingest_log'::regclass
          and contype = 'c'
          and pg_get_constraintdef(oid) like '%action%'
    loop
        execute format('alter table ingest_log drop constraint %I', c.conname);
    end loop;

    alter table ingest_log
        add constraint ingest_log_action_check
        check (action in (
            'create_draft', 'delete_draft', 'approve_requested', 'approve_done', 'approve_failed',
            'replace_requested', 'replace_done', 'replace_failed'
        ));
end
$$;
