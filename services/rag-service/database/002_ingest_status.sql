-- RAG Service: thêm trạng thái nạp (ingest_status, ingest_error) cho legal_documents.
-- Phục vụ luồng admin: lưu bản nháp (pending) -> bấm duyệt (processing, cắt chunk + embedding ở nền) -> done / failed.
-- Chạy được nhiều lần (idempotent): đã có cột / ràng buộc thì bỏ qua.
-- Cách chạy: Supabase Dashboard (project của RAG_DATABASE_URL) -> SQL Editor -> dán toàn bộ file -> Run.
--
-- !!! PHẢI CHẠY FILE NÀY TRƯỚC KHI BUILD LẠI rag-service !!!
-- Code mới truy vấn hai cột này ở mọi nơi (kể cả tìm kiếm, vì ORM select cả hàng legal_documents).
-- Build lại khi chưa chạy file này thì tìm kiếm lỗi "column ingest_status does not exist" -> agent báo 503.
--
-- Văn bản đã có sẵn và văn bản nạp bằng script (ingest_file.py, ingest_batch.py) tự có ingest_status = 'done'
-- nhờ giá trị mặc định, nên không cần cập nhật dữ liệu cũ.

alter table legal_documents
    add column if not exists ingest_status varchar(20) not null default 'done';

alter table legal_documents
    add column if not exists ingest_error text;  -- thông báo lỗi ngắn (tối đa 500 ký tự) của lần duyệt gần nhất

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'legal_documents_ingest_status_check'
          and conrelid = 'legal_documents'::regclass
    ) then
        alter table legal_documents
            add constraint legal_documents_ingest_status_check
            check (ingest_status in ('pending', 'processing', 'done', 'failed'));
    end if;
end
$$;
