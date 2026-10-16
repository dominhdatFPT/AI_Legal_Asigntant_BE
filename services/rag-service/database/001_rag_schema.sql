-- RAG Service: bảng văn bản pháp luật + đoạn văn bản (chunk) có embedding (pgvector).
-- Chạy được nhiều lần (idempotent): đã có bảng / index thì bỏ qua.
-- Cách chạy: Supabase Dashboard (project của RAG_DATABASE_URL) → SQL Editor → dán toàn bộ file → Run.
-- Sau đó chạy check_rag_db.sql để kiểm tra.

create extension if not exists pgcrypto;  -- gen_random_uuid()
create extension if not exists vector;    -- pgvector (Supabase có sẵn, chỉ cần bật)

create table if not exists legal_documents (
    id             uuid primary key default gen_random_uuid(),
    title          varchar(500) not null,
    doc_type       varchar(20)  not null
                   check (doc_type in ('luat', 'nghi_dinh', 'thong_tu', 'quyet_dinh', 'khac')),
    doc_number     varchar(100) unique,            -- VD 31/2024/QH15
    issuing_body   varchar(255),
    issued_date    date,
    effective_date date,
    status         varchar(20)  not null default 'active'
                   check (status in ('active', 'superseded', 'draft')),
    source_url     text,
    content        text         not null,          -- toàn văn gốc, để cắt + embedding lại khi đổi model
    chunk_count    integer      not null default 0,
    is_processed   boolean      not null default false,
    created_at     timestamptz  not null default now(),
    updated_at     timestamptz  not null default now()
);

-- Lọc văn bản còn hiệu lực theo loại khi tìm kiếm
create index if not exists idx_legal_documents_status_type
    on legal_documents (status, doc_type);

create table if not exists document_chunks (
    id              uuid primary key default gen_random_uuid(),
    document_id     uuid not null references legal_documents(id) on delete cascade,
    chunk_index     integer not null,                -- thứ tự trong văn bản, từ 0
    content         text not null,                   -- đã kèm dòng "Điều X. ..." ở đầu
    section_info    jsonb,                           -- {"chuong","dieu","khoan","diem"} để trích dẫn
    embedding       vector(768) not null,            -- gemini-embedding-001, output_dimensionality=768
    embedding_model varchar(100) not null,           -- tìm kiếm chỉ so với chunk cùng model
    created_at      timestamptz not null default now(),
    unique (document_id, chunk_index)
);

create index if not exists idx_document_chunks_document_id
    on document_chunks (document_id);

-- Index HNSW cho tìm kiếm gần đúng theo khoảng cách cosine (toán tử <=>)
create index if not exists idx_document_chunks_embedding_hnsw
    on document_chunks using hnsw (embedding vector_cosine_ops);

-- Chặn truy cập qua Supabase REST API (anon key). rag-service kết nối bằng user postgres
-- (chủ bảng) nên không bị ảnh hưởng.
alter table legal_documents enable row level security;
alter table document_chunks enable row level security;
