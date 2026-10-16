-- KIỂM TRA DATABASE AGENT-SERVICE (chỉ đọc, không sửa gì)
-- Supabase → SQL Editor → dán toàn bộ → Run. Cột ket_qua phải toàn "OK" (dòng thống kê thì là số).
with
t as (
  select to_regclass('public.conversations') is not null as has_conv,
         to_regclass('public.messages') is not null as has_msg
),
cols as (
  select table_name, column_name
  from information_schema.columns
  where table_schema = 'public' and table_name in ('conversations', 'messages')
)
select 1 as stt, 'Bảng conversations' as hang_muc,
       case when has_conv then 'OK' else 'THIẾU → chạy 001_agent_schema.sql' end as ket_qua from t
union all
select 2, 'Bảng messages',
       case when has_msg then 'OK' else 'THIẾU → chạy 001_agent_schema.sql' end from t
union all
select 3, 'Cột conversations (id, user_id, title, created_at, updated_at)',
       case when (select count(*) from cols where table_name = 'conversations'
                  and column_name in ('id', 'user_id', 'title', 'created_at', 'updated_at')) = 5
            then 'OK' else 'THIẾU → chạy 001_agent_schema.sql' end
union all
select 4, 'Cột messages (id, conversation_id, role, content, metadata, created_at)',
       case when (select count(*) from cols where table_name = 'messages'
                  and column_name in ('id', 'conversation_id', 'role', 'content', 'metadata', 'created_at')) = 6
            then 'OK' else 'THIẾU → chạy 001_agent_schema.sql' end
union all
select 5, 'Xóa hội thoại thì xóa luôn tin nhắn (ON DELETE CASCADE)',
       case when exists (select 1 from information_schema.referential_constraints rc
                         join information_schema.table_constraints tc on tc.constraint_name = rc.constraint_name
                         where tc.table_name = 'messages' and rc.delete_rule = 'CASCADE')
            then 'OK' else 'THIẾU → chạy 001_agent_schema.sql' end
union all
select 6, 'Index idx_conversations_user_updated, idx_messages_conversation_created',
       case when (select count(*) from pg_indexes where schemaname = 'public'
                  and indexname in ('idx_conversations_user_updated', 'idx_messages_conversation_created')) = 2
            then 'OK' else 'THIẾU → chạy 001_agent_schema.sql' end
union all
select 7, 'Số hội thoại hiện có',
       case when (select has_conv from t)
            then (xpath('/row/c/text()', query_to_xml('select count(*) as c from conversations', false, true, '')))[1]::text
            else '-' end
union all
select 8, 'Số tin nhắn hiện có',
       case when (select has_msg from t)
            then (xpath('/row/c/text()', query_to_xml('select count(*) as c from messages', false, true, '')))[1]::text
            else '-' end
order by stt;
