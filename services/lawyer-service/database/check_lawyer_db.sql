-- KIỂM TRA DATABASE LAWYER-SERVICE (chỉ đọc, không sửa gì)
-- Supabase → SQL Editor → dán toàn bộ → Run. Cột ket_qua phải toàn "OK" (dòng cuối chỉ là số liệu).
with
tables as (
  select c.relname, c.relrowsecurity
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relkind = 'r'
    and c.relname in ('specializations', 'lawyer_profiles', 'lawyer_profile_specializations', 'lawyer_applications')
),
idx as (
  select indexname from pg_indexes where schemaname = 'public'
)
select 1 as stt, 'Đủ 4 bảng' as hang_muc,
       case when (select count(*) from tables) = 4 then 'OK' else 'THIẾU → chạy 001_lawyer_schema.sql' end as ket_qua
union all
select 2, 'RLS bật cho cả 4 bảng',
       case when (select count(*) from tables where relrowsecurity) = 4 then 'OK'
            else 'CHƯA → chạy 001_lawyer_schema.sql' end
union all
select 3, 'Có lĩnh vực land, business',
       case when to_regclass('public.specializations') is null then 'THIẾU bảng specializations'
            when (xpath('/row/c/text()', query_to_xml(
                    'select count(*) as c from specializations where code in (''land'', ''business'')',
                    false, true, '')))[1]::text::int = 2 then 'OK'
            else 'THIẾU → chạy 001_lawyer_schema.sql' end
union all
select 4, 'Số thẻ không trùng (hồ sơ + đơn pending)',
       case when (select count(*) from idx where indexname in
                  ('lawyer_profiles_bar_card_key', 'lawyer_applications_pending_bar_card_key')) = 2
            then 'OK' else 'THIẾU → chạy 001_lawyer_schema.sql' end
union all
select 5, 'Mỗi người 1 đơn draft/pending',
       case when exists (select 1 from idx where indexname = 'lawyer_applications_one_open_key')
            then 'OK' else 'THIẾU → chạy 001_lawyer_schema.sql' end
union all
select 6, 'anon/authenticated không có quyền trên bảng',
       case when not exists (
              select 1 from information_schema.role_table_grants
              where table_schema = 'public' and grantee in ('anon', 'authenticated')
                and table_name in ('specializations', 'lawyer_profiles', 'lawyer_profile_specializations',
                                   'lawyer_applications'))
            then 'OK' else 'CÒN QUYỀN → chạy lại phần "Quyền" trong 001_lawyer_schema.sql' end
union all
select 7, 'Số hồ sơ luật sư theo trạng thái',
       case when to_regclass('public.lawyer_profiles') is null then '-'
            else coalesce((xpath('/row/s/text()', query_to_xml(
                   'select string_agg(status || ''='' || n, '', '' order by status) as s
                      from (select status, count(*) as n from lawyer_profiles group by status) t',
                   false, true, '')))[1]::text, '0 hồ sơ') end
order by stt;
