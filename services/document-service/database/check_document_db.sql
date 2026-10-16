-- Read-only verification: run in the SAME Supabase project as the migration.
-- Expect four tables, all with RLS enabled, and zero browser privileges/policies.
select c.relname as table_name, c.relrowsecurity as rls_enabled
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname in (
    'user_files', 'document_processing_jobs', 'document_extraction_results', 'user_identity_profiles')
order by c.relname;

select tablename, indexname, indexdef from pg_indexes
where schemaname = 'public' and tablename in (
    'user_files', 'document_processing_jobs', 'document_extraction_results', 'user_identity_profiles')
order by tablename, indexname;

select event_object_table, trigger_name, event_manipulation
from information_schema.triggers
where trigger_schema = 'public' and event_object_table in (
    'user_files', 'document_processing_jobs', 'document_extraction_results', 'user_identity_profiles')
order by event_object_table, trigger_name, event_manipulation;

select grantee, table_name, privilege_type from information_schema.role_table_grants
where table_schema = 'public' and table_name in (
    'user_files', 'document_processing_jobs', 'document_extraction_results', 'user_identity_profiles')
    and grantee in ('PUBLIC', 'anon', 'authenticated') order by table_name;

select tablename, policyname, roles, cmd from pg_policies
where schemaname = 'public' and tablename in (
    'user_files', 'document_processing_jobs', 'document_extraction_results', 'user_identity_profiles');

-- Supabase only: expect exactly one private bucket, 9437184-byte limit, four MIME types.
select id, name, public, file_size_limit, allowed_mime_types from storage.buckets
where id = 'document-files';

-- Expect document_files_backend_only with permissive = RESTRICTIVE.
select policyname, permissive, roles, cmd, qual, with_check from pg_policies
where schemaname = 'storage' and tablename = 'objects';
