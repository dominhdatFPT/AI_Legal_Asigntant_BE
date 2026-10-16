-- Agent Service: bảng conversations + messages (lưu hội thoại với AI).
-- Chạy được nhiều lần (idempotent): đã có bảng / index thì bỏ qua.
-- Cách chạy: Supabase Dashboard (project của AGENT_DATABASE_URL) → SQL Editor → dán toàn bộ file → Run.

create extension if not exists pgcrypto;

create table if not exists conversations (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null,                  -- lấy từ JWT `sub`; không có khóa ngoại sang user-service
    title      varchar(255),                   -- null cho tới khi có câu hỏi đầu tiên
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()  -- thời điểm có tin nhắn mới nhất, dùng để sắp xếp
);

-- Danh sách hội thoại của 1 user, mới nhất trước
create index if not exists idx_conversations_user_updated
    on conversations (user_id, updated_at desc);

create table if not exists messages (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(id) on delete cascade,
    role            varchar(20) not null check (role in ('user', 'assistant')),
    content         text not null,
    metadata        jsonb,                     -- chỉ tin nhắn assistant: rag_chunk_ids, model, tokens_used
    created_at      timestamptz not null default now()
);

-- Lấy tin nhắn của 1 hội thoại theo thứ tự thời gian
create index if not exists idx_messages_conversation_created
    on messages (conversation_id, created_at);

-- Chặn truy cập qua Supabase REST API (anon key). agent-service kết nối bằng user postgres
-- (chủ bảng) nên không bị ảnh hưởng.
alter table conversations enable row level security;
alter table messages enable row level security;
