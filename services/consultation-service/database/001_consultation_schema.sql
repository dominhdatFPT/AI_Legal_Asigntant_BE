-- Consultation Service: yêu cầu tư vấn, chat 1-1 citizen - luật sư, report, đồng ý điều khoản.
-- Chạy được nhiều lần (idempotent). Cách chạy: Supabase Dashboard (project của CONSULTATION_DATABASE_URL)
-- → SQL Editor → dán toàn bộ file → Run.
--
-- Không có khóa ngoại sang database khác: citizen_user_id, lawyer_user_id, closed_by (User Service) và
-- agent_session_id (conversations.id của Agent Service) chỉ là ID logic.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- Cuộc tư vấn
-- Một dòng đi từ yêu cầu đến cuộc chat: waiting → in_progress → closed.
-- Yêu cầu waiting bị xóa hẳn khi hết hạn hoặc khi một yêu cầu khác của citizen được nhận.
-- Cuộc đã closed giữ lại dòng để có cặp citizen - luật sư, thống kê và report; quá 10 ngày thì xóa nội dung.
create table if not exists consultations (
    id               uuid primary key default gen_random_uuid(),
    citizen_user_id  uuid        not null,
    lawyer_user_id   uuid,                                -- null khi còn waiting
    source           varchar(20) not null,                -- lobby | direct
    agent_session_id uuid,                                -- có giá trị khi đi từ chatbox AI, dùng để tóm tắt
    question         text,                                -- null sau khi đã xóa nội dung
    ai_summary       text,                                -- tóm tắt AI, tạo nền ngay sau khi gửi yêu cầu
    status           varchar(20) not null default 'waiting',  -- waiting | in_progress | closed
    close_reason     varchar(20),   -- completed | cancelled | locked | role_changed | deleted | expired
    closed_by        uuid,                                -- null khi hệ thống tự đóng
    accepted_at      timestamptz,
    closed_at        timestamptz,
    last_activity_at timestamptz not null default now(),  -- mốc 10 ngày: lúc tạo, lúc nhận hoặc tin nhắn cuối
    purged_at        timestamptz,                         -- lúc đã xóa question và tin nhắn
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),

    constraint consultations_source_check check (source in ('lobby', 'direct')),
    constraint consultations_status_check check (status in ('waiting', 'in_progress', 'closed')),
    constraint consultations_close_reason_check check (
        close_reason is null
        or close_reason in ('completed', 'cancelled', 'locked', 'role_changed', 'deleted', 'expired')
    ),
    constraint consultations_not_self_check check (citizen_user_id <> lawyer_user_id),
    -- Mỗi trạng thái chỉ có đúng các cột của nó.
    constraint consultations_state_check check (
        (status = 'waiting' and source = 'lobby' and lawyer_user_id is null and accepted_at is null
            and closed_at is null and close_reason is null)
        or (status = 'in_progress' and lawyer_user_id is not null and accepted_at is not null
            and closed_at is null and close_reason is null)
        or (status = 'closed' and lawyer_user_id is not null and accepted_at is not null
            and closed_at is not null and close_reason is not null)
    ),
    constraint consultations_purge_check check (
        (purged_at is null and question is not null)
        or (purged_at is not null and question is null and ai_summary is null and status = 'closed')
    )
);

-- Hàng chờ của luật sư, cũ nhất lên trước.
create index if not exists ix_consultations_lobby on consultations (created_at) where status = 'waiting';
-- Đếm tối đa 10 yêu cầu chờ, tìm luật sư cũ, danh sách của citizen.
create index if not exists ix_consultations_citizen on consultations (citizen_user_id, status);
-- Đếm tối đa 3 cuộc đang mở, danh sách của luật sư.
create index if not exists ix_consultations_lawyer on consultations (lawyer_user_id, status)
    where lawyer_user_id is not null;
-- Job dọn dữ liệu quá 10 ngày.
create index if not exists ix_consultations_cleanup on consultations (last_activity_at) where purged_at is null;
-- Một cặp citizen - luật sư chỉ có một cuộc đang mở.
create unique index if not exists consultations_open_pair_key on consultations (citizen_user_id, lawyer_user_id)
    where status = 'in_progress';

-- ---------------------------------------------------------------- Tin nhắn
create table if not exists consultation_messages (
    id              uuid primary key default gen_random_uuid(),
    consultation_id uuid        not null references consultations (id) on delete cascade,
    sender_id       uuid        not null,
    content         text        not null,
    is_read         boolean     not null default false,   -- người nhận đã đọc chưa, dùng cho chấm đỏ
    created_at      timestamptz not null default now(),

    constraint consultation_messages_content_check check (char_length(content) between 1 and 5000)
);

create index if not exists ix_consultation_messages_history
    on consultation_messages (consultation_id, created_at, id);
create index if not exists ix_consultation_messages_unread
    on consultation_messages (consultation_id, sender_id) where not is_read;

-- ---------------------------------------------------------------- Report
-- Admin chỉ thấy ai report ai và lý do, không đọc nội dung chat.
create table if not exists consultation_reports (
    id               uuid primary key default gen_random_uuid(),
    consultation_id  uuid         not null references consultations (id),
    reporter_user_id uuid         not null,
    reported_user_id uuid         not null,
    reason           varchar(500) not null,
    status           varchar(20)  not null default 'pending',  -- pending | resolved | dismissed
    handled_by       uuid,
    handled_at       timestamptz,
    created_at       timestamptz  not null default now(),

    constraint consultation_reports_not_self_check check (reporter_user_id <> reported_user_id),
    constraint consultation_reports_state_check check (
        (status = 'pending' and handled_by is null and handled_at is null)
        or (status in ('resolved', 'dismissed') and handled_by is not null and handled_at is not null)
    )
);

create index if not exists ix_consultation_reports_admin on consultation_reports (status, created_at);

-- ---------------------------------------------------------------- Đồng ý điều khoản
create table if not exists consultation_terms_acceptances (
    user_id       uuid        not null,
    terms_version varchar(20) not null,
    accepted_at   timestamptz not null default now(),

    primary key (user_id, terms_version)
);

-- ---------------------------------------------------------------- Quyền
-- Chỉ backend (kết nối Postgres bằng CONSULTATION_DATABASE_URL) đọc/ghi. Bật RLS, không policy, thu hồi quyền của
-- anon/authenticated để không ai đọc được qua Supabase REST bằng anon key.
alter table consultations enable row level security;
alter table consultation_messages enable row level security;
alter table consultation_reports enable row level security;
alter table consultation_terms_acceptances enable row level security;

revoke all on consultations, consultation_messages, consultation_reports, consultation_terms_acceptances
    from anon, authenticated;
