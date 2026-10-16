-- User Service: chức năng Luật sư.
--   1. refresh_tokens.revoked_reason: biết token bị thu hồi vì đổi vai trò → refresh trả 401 ROLE_CHANGED_RELOGIN.
--   2. Bỏ cờ users.created_as_lawyer: tài khoản admin tạo cho luật sư giờ có vai trò lawyer ngay.
--      Đổi mọi tài khoản created_as_lawyer = true sang role lawyer, rồi xóa cột.
-- Chạy được nhiều lần (idempotent). Chạy SAU 001 → 006.
-- Cách chạy: Supabase Dashboard → SQL Editor → dán toàn bộ file → Run.
--
-- QUAN TRỌNG: chỉ chạy khi deploy code giai đoạn 2 (Implementation_LawyerProfile). Code cũ của User Service
-- còn map cột created_as_lawyer, xóa cột trước sẽ làm mọi API đọc users lỗi 500.
-- Sau file này chạy 008_export_lawyer_profiles.sql để tạo hồ sơ luật sư "chưa hoàn thiện" bên Lawyer Service.

begin;

-- 1. Lý do thu hồi refresh token (null = đăng xuất / xoay token / khóa...; 'role_changed' = vai trò vừa đổi).
alter table refresh_tokens add column if not exists revoked_reason varchar(30);

do $$ begin
    alter table refresh_tokens
        add constraint refresh_tokens_revoked_reason_check
        check (revoked_reason is null or (revoked_at is not null and revoked_reason in ('role_changed')));
exception when duplicate_object then null;
end $$;

-- 2. created_as_lawyer → role lawyer. Token cũ mang vai trò citizen: thu hồi để họ đăng nhập lại nhận vai trò mới.
do $$ begin
    if exists (select 1 from information_schema.columns
               where table_schema = 'public' and table_name = 'users' and column_name = 'created_as_lawyer') then
        update refresh_tokens t
           set revoked_at = now(), revoked_reason = 'role_changed'
          from users u
         where t.user_id = u.id and u.created_as_lawyer and u.role = 'citizen' and t.revoked_at is null;

        update users set role = 'lawyer', updated_at = now()
         where created_as_lawyer and role = 'citizen';

        alter table users drop column created_as_lawyer;
    end if;
end $$;

commit;

-- Kiểm tra: dòng 1 phải là 1 (có cột revoked_reason), dòng 2 phải là 0 (đã xóa created_as_lawyer)
select 'refresh_tokens.revoked_reason' as cot, count(*) as co
from information_schema.columns
where table_schema = 'public' and table_name = 'refresh_tokens' and column_name = 'revoked_reason'
union all
select 'users.created_as_lawyer', count(*)
from information_schema.columns
where table_schema = 'public' and table_name = 'users' and column_name = 'created_as_lawyer';
