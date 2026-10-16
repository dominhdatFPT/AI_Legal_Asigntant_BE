-- Lawyer Service: lĩnh vực, hồ sơ luật sư, đơn xin làm luật sư / đổi thông tin thẻ.
-- Chạy được nhiều lần (idempotent). Cách chạy: Supabase Dashboard (project của LAWYER_DATABASE_URL)
-- → SQL Editor → dán toàn bộ file → Run. Kiểm tra lại bằng check_lawyer_db.sql.
--
-- Không có khóa ngoại sang database khác: user_id (User Service), card_document_id (Document Service),
-- reviewed_by (admin, User Service) chỉ là ID logic, service kiểm tra qua API / JWT.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- Lĩnh vực tư vấn
create table if not exists specializations (
    code       varchar(30)  primary key,          -- land | business (thêm lĩnh vực = thêm dòng)
    name       varchar(100) not null,
    sort_order smallint     not null default 0
);

insert into specializations (code, name, sort_order) values
    ('land', 'Đất đai', 1),
    ('business', 'Hộ kinh doanh', 2)
on conflict (code) do update set name = excluded.name, sort_order = excluded.sort_order;

-- ---------------------------------------------------------------- Hồ sơ luật sư đang hiệu lực
-- Một dòng / người. Bị ẩn (hạ vai trò, xóa tài khoản) thì giữ dòng, status = hidden; xin lại được duyệt
-- thì dùng lại chính dòng này.
create table if not exists lawyer_profiles (
    id               uuid primary key default gen_random_uuid(),
    user_id          uuid        not null,
    source           varchar(20) not null,             -- admin_created | self_applied
    status           varchar(20) not null default 'incomplete',  -- incomplete | active | hidden
    bar_card_number  varchar(50),                      -- null = luật sư admin tạo chưa điền
    card_document_id uuid,                             -- ảnh thẻ ở Document Service (null với admin tạo)
    card_ocr_result  jsonb,                            -- JSON OCR lấy từ Document Service lúc gửi đơn
    law_firm         varchar(255),
    years_experience smallint,
    bio              text,
    is_online        boolean     not null default false,  -- chưa có API bật/tắt (sẽ làm bằng WebSocket)
    last_seen_at     timestamptz,
    hidden_at        timestamptz,
    hidden_reason    varchar(20),                      -- role_changed | deleted
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),

    constraint lawyer_profiles_user_id_key unique (user_id),
    constraint lawyer_profiles_source_check check (source in ('admin_created', 'self_applied')),
    constraint lawyer_profiles_status_check check (status in ('incomplete', 'active', 'hidden')),
    constraint lawyer_profiles_bar_card_check
        check (bar_card_number is null or length(btrim(bar_card_number)) between 1 and 50),
    constraint lawyer_profiles_years_check check (years_experience is null or years_experience between 0 and 70),
    constraint lawyer_profiles_bio_check check (bio is null or length(bio) <= 2000),
    -- active bắt buộc đã có số thẻ (lĩnh vực + CCCD do service kiểm tra trước khi chuyển active)
    constraint lawyer_profiles_active_check check (status <> 'active' or bar_card_number is not null),
    constraint lawyer_profiles_hidden_check check (
        (status = 'hidden' and hidden_at is not null and hidden_reason in ('role_changed', 'deleted'))
        or (status <> 'hidden' and hidden_at is null and hidden_reason is null)),
    constraint lawyer_profiles_ocr_check
        check (card_ocr_result is null or jsonb_typeof(card_ocr_result) = 'object')
);

-- Số thẻ không trùng giữa các hồ sơ chưa ẩn (không phân biệt hoa thường). Đơn pending có index riêng bên dưới;
-- trùng giữa hồ sơ và đơn do service kiểm tra trong cùng transaction.
create unique index if not exists lawyer_profiles_bar_card_key
    on lawyer_profiles (upper(bar_card_number))
    where bar_card_number is not null and status <> 'hidden';

-- GET /lawyers: chỉ hồ sơ active (lọc online sau này)
create index if not exists ix_lawyer_profiles_active on lawyer_profiles (is_online, created_at)
    where status = 'active';

-- Lĩnh vực của hồ sơ (bảng nối, lọc GET /lawyers?specialization=land bằng index)
create table if not exists lawyer_profile_specializations (
    profile_id          uuid        not null references lawyer_profiles(id) on delete cascade,
    specialization_code varchar(30) not null references specializations(code),
    primary key (profile_id, specialization_code)
);

create index if not exists ix_lawyer_profile_specializations_code
    on lawyer_profile_specializations (specialization_code);

-- ---------------------------------------------------------------- Đơn
-- type: new (xin làm luật sư) | card_change (đổi số thẻ / ảnh thẻ).
-- status: draft → pending → approved | rejected; draft/pending → withdrawn.
-- Các trường là ảnh chụp tại thời điểm gửi; duyệt xong mới ghi đè vào lawyer_profiles.
create table if not exists lawyer_applications (
    id                   uuid primary key default gen_random_uuid(),
    user_id              uuid        not null,
    type                 varchar(20) not null,
    status               varchar(20) not null default 'draft',
    bar_card_number      varchar(50),
    law_firm             varchar(255),
    years_experience     smallint,
    bio                  text,
    specialization_codes jsonb,                -- mảng mã lĩnh vực (null với card_change)
    card_document_id     uuid,
    ocr_result           jsonb,
    applicant_full_name  varchar(255),         -- họ tên CCCD lúc gửi (để admin xem danh sách)
    reject_reason        varchar(500),
    reviewed_by          uuid,
    reviewed_at          timestamptz,
    submitted_at         timestamptz,
    withdrawn_at         timestamptz,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now(),

    constraint lawyer_applications_type_check check (type in ('new', 'card_change')),
    constraint lawyer_applications_status_check
        check (status in ('draft', 'pending', 'approved', 'rejected', 'withdrawn')),
    constraint lawyer_applications_bar_card_check
        check (bar_card_number is null or length(btrim(bar_card_number)) between 1 and 50),
    constraint lawyer_applications_years_check check (years_experience is null or years_experience between 0 and 70),
    constraint lawyer_applications_bio_check check (bio is null or length(bio) <= 2000),
    constraint lawyer_applications_specs_check
        check (specialization_codes is null or jsonb_typeof(specialization_codes) = 'array'),
    constraint lawyer_applications_ocr_check check (ocr_result is null or jsonb_typeof(ocr_result) = 'object'),
    -- card_change chỉ mang thông tin thẻ
    constraint lawyer_applications_card_change_check check (
        type <> 'card_change'
        or (law_firm is null and years_experience is null and bio is null and specialization_codes is null)),
    -- Đã gửi duyệt thì phải có ảnh thẻ và thời điểm gửi
    constraint lawyer_applications_submitted_check check (
        status in ('draft', 'withdrawn') or (submitted_at is not null and card_document_id is not null)),
    constraint lawyer_applications_new_card_check check (
        type <> 'new' or status in ('draft', 'withdrawn') or bar_card_number is not null),
    -- Từ chối bắt buộc có lý do; đã duyệt / từ chối phải có người duyệt
    constraint lawyer_applications_reject_check check (
        (status = 'rejected') = (reject_reason is not null and length(btrim(reject_reason)) > 0)),
    constraint lawyer_applications_reviewed_check check (
        (status in ('approved', 'rejected')) = (reviewed_by is not null and reviewed_at is not null)),
    constraint lawyer_applications_withdrawn_check check ((status = 'withdrawn') = (withdrawn_at is not null))
);

-- Mỗi người chỉ một đơn draft/pending (mọi loại) tại một thời điểm.
create unique index if not exists lawyer_applications_one_open_key
    on lawyer_applications (user_id) where status in ('draft', 'pending');

-- Số thẻ không trùng giữa các đơn đang chờ duyệt.
create unique index if not exists lawyer_applications_pending_bar_card_key
    on lawyer_applications (upper(bar_card_number))
    where status = 'pending' and bar_card_number is not null;

-- Đơn của tôi (mới nhất trước) và thời gian chờ sau khi bị từ chối.
create index if not exists ix_lawyer_applications_user on lawyer_applications (user_id, created_at desc);
create index if not exists ix_lawyer_applications_user_rejected on lawyer_applications (user_id, reviewed_at desc)
    where status = 'rejected';

-- Danh sách / đếm của admin theo tab (không bao giờ có draft).
create index if not exists ix_lawyer_applications_admin on lawyer_applications (status, type, submitted_at)
    where status <> 'draft';

-- ---------------------------------------------------------------- Quyền
-- Chỉ backend (kết nối Postgres bằng LAWYER_DATABASE_URL) đọc/ghi. Bật RLS, không policy, thu hồi quyền của
-- anon/authenticated để không ai đọc được qua Supabase REST bằng anon key.
alter table specializations enable row level security;
alter table lawyer_profiles enable row level security;
alter table lawyer_profile_specializations enable row level security;
alter table lawyer_applications enable row level security;

revoke all on specializations, lawyer_profiles, lawyer_profile_specializations, lawyer_applications
    from anon, authenticated;
