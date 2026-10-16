-- KIỂM TRA DATABASE RAG-SERVICE (chỉ đọc, không sửa gì)
-- Supabase → SQL Editor → dán toàn bộ → Run. Cột ket_qua phải toàn "OK" (dòng thống kê thì là số).
with
t as (
  select to_regclass('public.legal_documents') is not null as has_doc,
         to_regclass('public.document_chunks') is not null as has_chunk
),
cols as (
  select table_name, column_name, udt_name
  from information_schema.columns
  where table_schema = 'public' and table_name in ('legal_documents', 'document_chunks')
)
select 1 as stt, 'Extension vector (pgvector)' as hang_muc,
       case when exists (select 1 from pg_extension where extname = 'vector')
            then 'OK' else 'THIẾU → chạy 001_rag_schema.sql' end as ket_qua
union all
select 2, 'Bảng legal_documents',
       case when has_doc then 'OK' else 'THIẾU → chạy 001_rag_schema.sql' end from t
union all
select 3, 'Bảng document_chunks',
       case when has_chunk then 'OK' else 'THIẾU → chạy 001_rag_schema.sql' end from t
union all
select 4, 'Cột legal_documents (id, title, doc_type, doc_number, status, content, chunk_count, is_processed)',
       case when (select count(*) from cols where table_name = 'legal_documents'
                  and column_name in ('id', 'title', 'doc_type', 'doc_number', 'status',
                                      'content', 'chunk_count', 'is_processed')) = 8
            then 'OK' else 'THIẾU → chạy 001_rag_schema.sql' end
union all
select 5, 'Cột document_chunks (id, document_id, chunk_index, content, section_info, embedding, embedding_model)',
       case when (select count(*) from cols where table_name = 'document_chunks'
                  and column_name in ('id', 'document_id', 'chunk_index', 'content', 'section_info',
                                      'embedding', 'embedding_model')) = 7
            then 'OK' else 'THIẾU → chạy 001_rag_schema.sql' end
union all
select 6, 'Cột embedding là vector(768)',
       case when exists (select 1 from pg_attribute a
                         where a.attrelid = to_regclass('public.document_chunks')
                           and a.attname = 'embedding'
                           and format_type(a.atttypid, a.atttypmod) like '%vector(768)')
            then 'OK' else 'SAI → số chiều phải là 768 (khớp EMBEDDING_DIM)' end
union all
select 7, 'Xóa văn bản thì xóa luôn chunks (ON DELETE CASCADE)',
       case when exists (select 1 from information_schema.referential_constraints rc
                         join information_schema.table_constraints tc on tc.constraint_name = rc.constraint_name
                         where tc.table_name = 'document_chunks' and rc.delete_rule = 'CASCADE')
            then 'OK' else 'THIẾU → chạy 001_rag_schema.sql' end
union all
select 8, 'Index HNSW idx_document_chunks_embedding_hnsw (vector_cosine_ops)',
       case when exists (select 1 from pg_indexes where schemaname = 'public'
                         and indexname = 'idx_document_chunks_embedding_hnsw'
                         and indexdef ilike '%hnsw%vector_cosine_ops%')
            then 'OK' else 'THIẾU → chạy 001_rag_schema.sql' end
union all
select 9, 'Row Level Security bật trên 2 bảng',
       case when (select count(*) from pg_class
                  where oid in (to_regclass('public.legal_documents'), to_regclass('public.document_chunks'))
                    and relrowsecurity) = 2
            then 'OK' else 'THIẾU → chạy 001_rag_schema.sql' end
union all
select 10, 'Số văn bản hiện có',
       case when (select has_doc from t)
            -- query_to_xml chạy câu lệnh động: bảng chưa có thì không báo lỗi
            then (xpath('/row/c/text()', query_to_xml('select count(*) as c from legal_documents', false, true, '')))[1]::text
            else '0' end
union all
select 11, 'Số chunk hiện có',
       case when (select has_chunk from t)
            then (xpath('/row/c/text()', query_to_xml('select count(*) as c from document_chunks', false, true, '')))[1]::text
            else '0' end
order by stt;
