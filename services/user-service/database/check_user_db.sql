-- KIỂM TRA DATABASE USER-SERVICE (chỉ đọc, không sửa gì)
-- Supabase → SQL Editor → dán toàn bộ → Run. Cột ket_qua phải toàn "OK".
with
t as (
  select to_regclass('public.users') is not null as has_users,
         to_regclass('public.refresh_tokens') is not null as has_tokens
),
cols as (
  select column_name, is_nullable
  from information_schema.columns
  where table_schema = 'public' and table_name = 'users'
),
q(sql) as (
  select 'select count(*) as c from users where lower(username) = ''admin2026'' and role = ''admin'' and status = ''active'' and deleted_at is null'
  union all
  select 'select count(*) as c from users where lower(username) = ''admin2026'' and crypt(''123456'', overlay(password_hash placing ''2a'' from 2 for 2)) = overlay(password_hash placing ''2a'' from 2 for 2)'
  union all
  select 'select count(*) as c from users'
),
r as (
  select row_number() over () as i,
         case when (select has_users from t)
                   and exists (select 1 from cols where column_name = 'username')
                   and exists (select 1 from cols where column_name = 'password_hash')
                   and exists (select 1 from pg_proc where proname = 'crypt')
              then (xpath('/row/c/text()', query_to_xml(sql, false, true, '')))[1]::text::int
         end as n
  from q
)
select 1 as stt, 'Bảng users' as hang_muc,
       case when has_users then 'OK' else 'THIẾU → chạy 001_users_schema.sql' end as ket_qua from t
union all
select 2, 'Bảng refresh_tokens',
       case when has_tokens then 'OK' else 'THIẾU → chạy 001_users_schema.sql' end from t
union all
select 3, 'Cột users.username, users.password_hash',
       case when (select count(*) from cols where column_name in ('username', 'password_hash')) = 2
            then 'OK' else 'THIẾU → chạy 001_users_schema.sql' end
union all
select 4, 'users.firebase_uid cho phép NULL',
       coalesce((select case when is_nullable = 'YES' then 'OK' else 'CHƯA → chạy 001_users_schema.sql' end
                 from cols where column_name = 'firebase_uid'), 'THIẾU bảng users')
union all
select 5, 'Kiểu enum user_role có admin',
       case when exists (select 1 from pg_enum e join pg_type ty on ty.oid = e.enumtypid
                         where ty.typname = 'user_role' and e.enumlabel = 'admin')
            then 'OK' else 'THIẾU → chạy 001_users_schema.sql' end
union all
select 6, 'Tài khoản admin2026 (role admin, đang hoạt động)',
       case when (select n from r where i = 1) > 0 then 'OK'
            when (select n from r where i = 1) is null then 'Chưa kiểm tra được (thiếu bảng/cột ở trên)'
            else 'CHƯA CÓ → chạy 002_seed_admin.sql' end
union all
select 7, 'Mật khẩu admin2026 khớp 123456',
       case when (select n from r where i = 2) > 0 then 'OK'
            when (select n from r where i = 2) is null then 'Chưa kiểm tra được'
            else 'KHÔNG KHỚP → chạy lại 002_seed_admin.sql' end
union all
select 8, 'Tổng số user hiện có', coalesce((select n from r where i = 3)::text, '-')
union all
select 9, 'SĐT không trùng (index users_phone_key)',
       case when to_regclass('public.users_phone_key') is not null then 'OK'
            else 'THIẾU → chạy 006_users_phone_unique.sql' end
order by stt;
