-- Supabase only. This does NOT edit Storage metadata or create a bucket.
-- A restrictive policy protects our bucket even if this project already has
-- broad permissive policies for other services' buckets. Other buckets are unaffected.
begin;
create policy document_files_backend_only on storage.objects
    as restrictive for all to anon, authenticated
    using (bucket_id <> 'document-files')
    with check (bucket_id <> 'document-files');
commit;
