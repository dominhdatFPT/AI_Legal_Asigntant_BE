-- C4 additive migration. Apply once after 001, on the Contract project only.
begin;
alter table public.contract_templates add column last_version integer not null default 0 check (last_version >= 0);
update public.contract_templates t set last_version = coalesce((select max(v.version) from public.contract_template_versions v where v.template_id=t.id),0);

create or replace function contract_private.guard_version() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare t public.contract_templates;
begin
 if tg_op='DELETE' then
  select * into t from public.contract_templates where id=old.template_id for update;
  if old.status<>'draft' or t.kind<>'system' or t.deleted_at is not null then
   raise check_violation using message='CONTRACT_VERSION_DELETE_DENIED'; end if;
  -- FK checks additionally prevent deleting any referenced version.
  return old;
 end if;
 if tg_op='UPDATE' and (old.status='published' or (new.id,new.template_id,new.version,new.created_at) is distinct from (old.id,old.template_id,old.version,old.created_at)) then
  raise check_violation using message='CONTRACT_VERSION_IMMUTABLE'; end if;
 if tg_op='UPDATE' and old.snapshot_storage_key is not null and
  (new.snapshot_storage_key,new.source_sha256,new.source_size_bytes,new.source_document_id,new.source_extraction_id,new.extraction_snapshot,new.source_format)
  is distinct from (old.snapshot_storage_key,old.source_sha256,old.source_size_bytes,old.source_document_id,old.source_extraction_id,old.extraction_snapshot,old.source_format) then
  raise check_violation using message='CONTRACT_SOURCE_SNAPSHOT_IMMUTABLE'; end if;
 select * into t from public.contract_templates where id=new.template_id for update;
 if not found or t.deleted_at is not null then raise check_violation using message='CONTRACT_TEMPLATE_UNAVAILABLE'; end if;
 if new.status='published' and t.kind='personal' and (new.snapshot_storage_key is null or new.extraction_snapshot is null) then
  raise check_violation using message='CONTRACT_PERSONAL_SNAPSHOT_REQUIRED'; end if;
 return new;
end $$;
commit;
