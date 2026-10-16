-- User Service: cột cho chức năng quản lý tài khoản (admin khóa có thời hạn, xóa mềm, tạo tài khoản).
-- Chạy được nhiều lần (idempotent). Chạy SAU 001 → 003.
-- Cách chạy: Supabase Dashboard → SQL Editor → dán toàn bộ file → Run.
--
-- Bảng users đã có sẵn status, locked_reason, locked_by, locked_at, deleted_at từ 001; file này chỉ bổ sung.

-- Khóa có thời hạn: hết hạn thì tự mở (BE tự kiểm tra, không cần cron). NULL + status = locked = khóa vĩnh viễn.
alter table users add column if not exists locked_until timestamptz;

-- Ai xóa mềm tài khoản (deleted_at đã có từ 001).
alter table users add column if not exists deleted_by uuid references users(id);

-- Tài khoản do admin tạo: người tạo, và có phải cấp cho luật sư không
-- (vẫn là citizen cho tới khi được duyệt hồ sơ lên lawyer).
alter table users add column if not exists created_by uuid references users(id);
alter table users add column if not exists created_as_lawyer boolean not null default false;

-- Lọc danh sách theo vai trò / trạng thái trên màn hình quản lý.
create index if not exists ix_users_role_status on users (role, status) where deleted_at is null;

-- Kiểm tra: phải thấy đủ 4 cột
select column_name, data_type
from information_schema.columns
where table_schema = 'public' and table_name = 'users'
  and column_name in ('locked_until', 'deleted_by', 'created_by', 'created_as_lawyer')
order by column_name;
