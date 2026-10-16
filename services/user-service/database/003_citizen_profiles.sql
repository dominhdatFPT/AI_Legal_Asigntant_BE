-- User Service: bảng citizen_profiles (hồ sơ cá nhân theo CCCD), quan hệ 1-1 với users.
-- Chạy được nhiều lần (idempotent). Cách chạy: Supabase Dashboard → SQL Editor → dán toàn bộ file → Run.
--
-- Họ tên lấy theo CCCD được ghi đè vào users.full_name; số điện thoại lưu ở users.phone (không lặp lại ở đây).
-- Tên cột trùng với các trường eKYC trả về (id → id_number, full_name, date_of_birth, gender, nationality,
-- place_of_origin, place_of_residence, issue_date, expiry_date) để FE chép thẳng sang form.

create table if not exists citizen_profiles (
    user_id            uuid primary key references users(id) on delete cascade,

    -- Thông tin trên CCCD
    id_number          varchar(12) not null,      -- số định danh cá nhân
    full_name          varchar     not null,
    date_of_birth      date        not null,
    gender             varchar(10) not null,      -- Nam | Nữ
    nationality        varchar     not null default 'Việt Nam',
    place_of_origin    varchar,                   -- quê quán / nơi đăng ký khai sinh
    place_of_residence varchar,                   -- nơi thường trú đúng như in trên thẻ (tham khảo)
    issue_date         date        not null,
    issue_place        varchar     not null,
    expiry_date        date,

    -- Nơi thường trú tách 3 phần theo mẫu đơn mới (Xã/Phường/Đặc khu + Tỉnh/Thành phố)
    permanent_detail   varchar     not null,      -- số nhà, đường, thôn/xóm
    permanent_ward     varchar     not null,
    permanent_province varchar     not null,

    -- Nơi ở hiện tại; current_same_as_permanent = true thì 3 cột dưới để trống
    current_same_as_permanent boolean not null default true,
    current_detail     varchar,
    current_ward       varchar,
    current_province   varchar,

    -- Nhập tay (CCCD không có)
    ethnicity          varchar,                   -- dân tộc
    tax_code           varchar,                   -- mã số thuế cá nhân (nếu có)

    -- Nguồn dữ liệu
    source             varchar(10) not null default 'manual',   -- ekyc | manual
    ekyc_id            uuid,                      -- mã bản đọc eKYC bên Document Service (ảnh + JSON gốc)
    declared_at        timestamptz not null,      -- lúc người dùng tick cam đoan thông tin đúng sự thật
    consented_at       timestamptz not null,      -- lúc người dùng tick đồng ý lưu thông tin cá nhân

    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

-- Mỗi số CCCD chỉ gắn với một tài khoản (trùng → 409 CCCD_ALREADY_USED).
create unique index if not exists citizen_profiles_id_number_key on citizen_profiles (id_number);

do $$ begin
    alter table citizen_profiles
        add constraint citizen_profiles_source_check check (source in ('ekyc', 'manual'));
exception when duplicate_object then null;
end $$;

-- Bảng đã tạo từ bản trước (chưa có ô "cam đoan"): bổ sung cột.
alter table citizen_profiles add column if not exists declared_at timestamptz;
