-- PostgreSQL 15+ / Supabase. Run ONCE on the Contract project only.
-- Atomic name-collision failure; no seed, cross-service FK or Storage mutation.
begin;
create schema contract_private;
revoke all on schema contract_private from public, anon, authenticated;

create table public.contract_schema_versions (
    version integer primary key check (version = 1),
    installed_at timestamptz not null default now()
);
create table public.contract_templates (
    id uuid primary key default gen_random_uuid(),
    kind text not null check (kind in ('system','personal')),
    owner_user_id uuid,
    name varchar(200) not null check (btrim(name) <> ''),
    description varchar(2000) check (description is null or btrim(description) <> ''),
    is_visible boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    check ((kind = 'personal') = (owner_user_id is not null)),
    check (kind <> 'personal' or not is_visible)
);
create table public.contract_template_versions (
    id uuid primary key default gen_random_uuid(),
    template_id uuid not null references public.contract_templates(id) on delete restrict,
    version integer not null check (version >= 1),
    status text not null default 'draft' check (status in ('draft','published')),
    content text not null check (char_length(content) between 1 and 200000),
    source_format text not null default 'docx' check (source_format in ('docx','pdf')),
    snapshot_storage_key text check (snapshot_storage_key !~ '(^/|\.\.|[:?#])' and btrim(snapshot_storage_key) <> ''),
    source_sha256 varchar(64) check (source_sha256 ~ '^[0-9a-f]{64}$'),
    source_size_bytes bigint check (source_size_bytes between 1 and 9437184),
    source_document_id uuid,
    source_extraction_id uuid,
    extraction_snapshot jsonb check (jsonb_typeof(extraction_snapshot) = 'object' and octet_length(extraction_snapshot::text) <= 16777216),
    mapping_revision integer not null default 0 check (mapping_revision >= 0),
    warnings jsonb not null default '[]' check (jsonb_typeof(warnings) = 'array'),
    created_at timestamptz not null default now(),
    published_at timestamptz,
    unique (template_id, version),
    unique (id, template_id),
    check ((status = 'published') = (published_at is not null)),
    check ((snapshot_storage_key is null and source_sha256 is null and source_size_bytes is null)
        or (snapshot_storage_key is not null and source_sha256 is not null and source_size_bytes is not null))
);
create table public.contract_template_fields (
    version_id uuid not null references public.contract_template_versions(id) on delete restrict,
    name varchar(64) not null check (name ~ '^[a-z][a-z0-9_]*$'),
    label varchar(200) not null check (btrim(label) <> ''),
    type text not null check (type in ('text','date','number','boolean')),
    required boolean not null default false,
    identity_key text check (identity_key in ('full_name','date_of_birth','identity_number','issue_date','issue_place','permanent_address')),
    ordinal integer not null check (ordinal >= 0),
    placements jsonb not null default '[]' check (jsonb_typeof(placements) = 'array' and jsonb_array_length(placements) <= 20),
    confidence numeric check (confidence between 0 and 1),
    primary key (version_id, name), unique (version_id, ordinal)
);
create table public.contract_drafts (
    id uuid primary key default gen_random_uuid(),
    owner_user_id uuid not null,
    template_version_id uuid not null references public.contract_template_versions(id) on delete restrict,
    procedure_id uuid,
    title varchar(200) not null check (btrim(title) <> ''),
    field_values jsonb not null default '{}' check (jsonb_typeof(field_values) = 'object' and octet_length(field_values::text) <= 1048576),
    revision integer not null default 0 check (revision >= 0),
    created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    unique (id, owner_user_id), unique (id, template_version_id, owner_user_id)
);
create table public.contract_draft_documents (
    draft_id uuid not null, owner_user_id uuid not null, document_id uuid not null,
    primary key (draft_id, document_id),
    foreign key (draft_id, owner_user_id) references public.contract_drafts(id, owner_user_id) on delete restrict
);
create table public.contract_generation_jobs (
    id uuid primary key default gen_random_uuid(),
    draft_id uuid not null, template_version_id uuid not null, owner_user_id uuid not null,
    draft_revision integer not null check (draft_revision >= 0),
    input_snapshot jsonb not null check (jsonb_typeof(input_snapshot) = 'object' and octet_length(input_snapshot::text) <= 1048576),
    document_ids uuid[] not null default '{}' check (cardinality(document_ids) <= 100),
    kind text not null default 'final' check (kind in ('preview','final')),
    format text not null check (format in ('docx','pdf')),
    status text not null default 'queued' check (status in ('queued','processing','completed','failed')),
    idempotency_key uuid not null, request_hash varchar(64) not null check (request_hash ~ '^[0-9a-f]{64}$'),
    error_code varchar(100), warnings jsonb not null default '[]' check (jsonb_typeof(warnings) = 'array'),
    retry_count integer not null default 0 check (retry_count >= 0),
    created_at timestamptz not null default now(), started_at timestamptz, completed_at timestamptz,
    foreign key (draft_id, template_version_id, owner_user_id) references public.contract_drafts(id, template_version_id, owner_user_id) on delete restrict,
    unique (owner_user_id, kind, idempotency_key), unique (id, owner_user_id, format),
    check (kind <> 'preview' or format = 'pdf'),
    check ((status in ('completed','failed')) = (completed_at is not null)),
    check (status <> 'processing' or started_at is not null),
    check ((status = 'failed' and error_code is not null and btrim(error_code) <> '') or (status <> 'failed' and error_code is null)),
    check ((started_at is null or started_at >= created_at) and (completed_at is null or completed_at >= coalesce(started_at,created_at)))
);
create unique index contract_one_active_generation on public.contract_generation_jobs(draft_id) where status in ('queued','processing');
create table public.contract_generation_results (
    job_id uuid primary key, owner_user_id uuid not null, format text not null,
    storage_key text not null unique check (storage_key !~ '(^/|\.\.|[:?#])' and btrim(storage_key) <> ''),
    sha256 varchar(64) not null check (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint not null check (size_bytes between 1 and 52428800),
    created_at timestamptz not null default now(),
    foreign key (job_id, owner_user_id, format) references public.contract_generation_jobs(id, owner_user_id, format) on delete restrict
);
create table public.contract_analysis_jobs (
    id uuid primary key default gen_random_uuid(), template_id uuid not null, template_version_id uuid not null,
    owner_user_id uuid not null,
    status text not null default 'queued' check (status in ('queued','processing','needs_review','confirmed','failed')),
    idempotency_key uuid not null, request_hash varchar(64) not null check (request_hash ~ '^[0-9a-f]{64}$'),
    error_code varchar(100), created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
    foreign key (template_version_id, template_id) references public.contract_template_versions(id,template_id) on delete restrict,
    unique (owner_user_id,idempotency_key),
    check ((status = 'failed' and error_code is not null and btrim(error_code) <> '') or (status <> 'failed' and error_code is null))
);
create unique index contract_one_active_analysis on public.contract_analysis_jobs(template_version_id) where status in ('queued','processing','needs_review');
create table public.contract_notification_outbox (
    id uuid primary key default gen_random_uuid(), job_id uuid not null unique references public.contract_generation_jobs(id) on delete restrict,
    status text not null default 'pending' check (status in ('pending','sent','failed')),
    attempts integer not null default 0 check (attempts >= 0),
    created_at timestamptz not null default now(), next_attempt_at timestamptz not null default now(), sent_at timestamptz,
    check ((status = 'sent') = (sent_at is not null))
);
create index contract_catalog on public.contract_templates(created_at desc,id) where kind='system' and is_visible and deleted_at is null;
create index contract_personal_owner on public.contract_templates(owner_user_id,created_at desc,id) where kind='personal' and deleted_at is null;
create index contract_draft_owner on public.contract_drafts(owner_user_id,created_at desc,id) where deleted_at is null;
create index contract_generation_queue on public.contract_generation_jobs(status,created_at);
create index contract_analysis_queue on public.contract_analysis_jobs(status,created_at);
create index contract_outbox_queue on public.contract_notification_outbox(status,next_attempt_at);

create function contract_private.guard_template() returns trigger language plpgsql set search_path=pg_catalog,public as $$
begin
 if tg_op='DELETE' then raise check_violation using message='CONTRACT_SOFT_DELETE_ONLY'; end if;
 if (new.id,new.kind,new.owner_user_id,new.created_at) is distinct from (old.id,old.kind,old.owner_user_id,old.created_at) then
  raise check_violation using message='CONTRACT_TEMPLATE_IDENTITY_IMMUTABLE'; end if;
 new.updated_at=now(); return new;
end $$;
create trigger contract_template_guard before update or delete on public.contract_templates for each row execute function contract_private.guard_template();

create function contract_private.guard_version() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare t public.contract_templates;
begin
 if tg_op='DELETE' then raise check_violation using message='CONTRACT_VERSION_DELETE_DENIED'; end if;
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
create trigger contract_version_guard before insert or update or delete on public.contract_template_versions for each row execute function contract_private.guard_version();

create function contract_private.guard_field() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare v public.contract_template_versions; target uuid;
begin
 if tg_op='UPDATE' and new.version_id<>old.version_id then raise check_violation using message='CONTRACT_FIELD_VERSION_IMMUTABLE'; end if;
 target=case when tg_op='DELETE' then old.version_id else new.version_id end;
 select * into v from public.contract_template_versions where id=target for update;
 if not found or v.status='published' then raise check_violation using message='CONTRACT_FIELD_IMMUTABLE'; end if;
 if tg_op='DELETE' then return old; end if;
 return new;
end $$;
create trigger contract_field_guard before insert or update or delete on public.contract_template_fields for each row execute function contract_private.guard_field();

create function contract_private.guard_draft() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare v public.contract_template_versions; t public.contract_templates;
begin
 if tg_op='DELETE' then raise check_violation using message='CONTRACT_SOFT_DELETE_ONLY'; end if;
 if tg_op='UPDATE' then
  if (new.id,new.owner_user_id,new.template_version_id,new.created_at) is distinct from (old.id,old.owner_user_id,old.template_version_id,old.created_at) then
   raise check_violation using message='CONTRACT_DRAFT_IDENTITY_IMMUTABLE'; end if;
  if exists(select 1 from public.contract_generation_jobs where draft_id=old.id and status in ('queued','processing')) then
   raise check_violation using message='CONTRACT_GENERATION_ACTIVE'; end if;
  new.revision=old.revision+1; new.updated_at=now();
 else
  select * into v from public.contract_template_versions where id=new.template_version_id for update;
  select * into t from public.contract_templates where id=v.template_id for update;
  if v.status is distinct from 'published' or t.deleted_at is not null or
    (t.kind='system' and not t.is_visible) or (t.kind='personal' and t.owner_user_id<>new.owner_user_id) then
   raise check_violation using message='CONTRACT_VERSION_UNAVAILABLE'; end if;
 end if;
 return new;
end $$;
create trigger contract_draft_guard before insert or update or delete on public.contract_drafts for each row execute function contract_private.guard_draft();

create function contract_private.guard_document_link() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare d public.contract_drafts; target uuid;
begin
 if tg_op='UPDATE' then raise check_violation using message='CONTRACT_DOCUMENT_LINK_IMMUTABLE'; end if;
 target=case when tg_op='DELETE' then old.draft_id else new.draft_id end;
 select * into d from public.contract_drafts where id=target for update;
 if d.deleted_at is not null or exists(select 1 from public.contract_generation_jobs where draft_id=target and status in ('queued','processing')) then
  raise check_violation using message='CONTRACT_GENERATION_ACTIVE'; end if;
 if tg_op='INSERT' and (select count(*) from public.contract_draft_documents where draft_id=target)>=100 then
  raise check_violation using message='CONTRACT_DOCUMENT_LIMIT'; end if;
 if tg_op='DELETE' then return old; end if;
 return new;
end $$;
create trigger contract_document_guard before insert or update or delete on public.contract_draft_documents for each row execute function contract_private.guard_document_link();

create function contract_private.guard_generation() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare d public.contract_drafts;
begin
 if tg_op='DELETE' then raise check_violation using message='CONTRACT_JOB_DELETE_DENIED'; end if;
 if tg_op='INSERT' then
  select * into d from public.contract_drafts where id=new.draft_id for update;
  if d.deleted_at is not null or new.draft_revision<>d.revision or new.input_snapshot is distinct from d.field_values or new.status<>'queued'
   or new.document_ids is distinct from coalesce((select array_agg(document_id order by document_id) from public.contract_draft_documents where draft_id=new.draft_id),'{}'::uuid[]) then
   raise check_violation using message='CONTRACT_JOB_SNAPSHOT_INVALID'; end if;
 else
  if (new.id,new.draft_id,new.owner_user_id,new.template_version_id,new.draft_revision,new.input_snapshot,new.document_ids,new.kind,new.format,new.idempotency_key,new.request_hash,new.created_at)
    is distinct from (old.id,old.draft_id,old.owner_user_id,old.template_version_id,old.draft_revision,old.input_snapshot,old.document_ids,old.kind,old.format,old.idempotency_key,old.request_hash,old.created_at)
    or old.status in ('completed','failed') or not (new.status=old.status or (old.status='queued' and new.status in ('processing','failed')) or (old.status='processing' and new.status in ('completed','failed'))) then
   raise check_violation using message='CONTRACT_JOB_IMMUTABLE_OR_TRANSITION_INVALID'; end if;
 end if;
 return new;
end $$;
create trigger contract_generation_guard before insert or update or delete on public.contract_generation_jobs for each row execute function contract_private.guard_generation();

create function contract_private.guard_analysis() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare v public.contract_template_versions; t public.contract_templates;
begin
 if tg_op='DELETE' then raise check_violation using message='CONTRACT_ANALYSIS_DELETE_DENIED'; end if;
 select * into v from public.contract_template_versions where id=new.template_version_id for update;
 select * into t from public.contract_templates where id=v.template_id;
 if t.kind is distinct from 'personal' or t.owner_user_id is distinct from new.owner_user_id then
  raise check_violation using message='CONTRACT_ANALYSIS_OWNER_INVALID'; end if;
 if tg_op='INSERT' and new.status<>'queued' then raise check_violation using message='CONTRACT_ANALYSIS_TRANSITION_INVALID'; end if;
 if tg_op='UPDATE' and ((new.id,new.template_id,new.template_version_id,new.owner_user_id,new.idempotency_key,new.request_hash,new.created_at)
  is distinct from (old.id,old.template_id,old.template_version_id,old.owner_user_id,old.idempotency_key,old.request_hash,old.created_at)
  or old.status in ('confirmed','failed') or not (new.status=old.status or (old.status='queued' and new.status in ('processing','failed')) or (old.status='processing' and new.status in ('needs_review','failed')) or (old.status='needs_review' and new.status in ('confirmed','failed')))) then
  raise check_violation using message='CONTRACT_ANALYSIS_TRANSITION_INVALID'; end if;
 if new.status='confirmed' and v.status<>'published' then raise check_violation using message='CONTRACT_ANALYSIS_NOT_PUBLISHED'; end if;
 new.updated_at=now(); return new;
end $$;
create trigger contract_analysis_guard before insert or update or delete on public.contract_analysis_jobs for each row execute function contract_private.guard_analysis();

create function contract_private.immutable_result() returns trigger language plpgsql as $$
begin raise check_violation using message='CONTRACT_RESULT_IMMUTABLE'; end $$;
create trigger contract_result_guard before update or delete on public.contract_generation_results for each row execute function contract_private.immutable_result();
create function contract_private.check_result() returns trigger language plpgsql set search_path=pg_catalog,public as $$
declare target uuid; state text; present boolean;
begin
 if tg_table_name='contract_generation_jobs' then target=new.id; else target=new.job_id; end if;
 select status into state from public.contract_generation_jobs where id=target;
 select exists(select 1 from public.contract_generation_results where job_id=target) into present;
 if (state='completed')<>present then raise check_violation using message='CONTRACT_JOB_RESULT_MISMATCH'; end if;
 return null;
end $$;
create constraint trigger contract_job_result_consistency after insert or update on public.contract_generation_jobs deferrable initially deferred for each row execute function contract_private.check_result();
create constraint trigger contract_result_job_consistency after insert on public.contract_generation_results deferrable initially deferred for each row execute function contract_private.check_result();

-- Deny browser/client roles. Service JWT is NOT Supabase auth.uid().
-- Backend uses a private Postgres login; owner/admin enforcement remains mandatory in API queries.
do $$ declare item text; begin
 foreach item in array array['contract_schema_versions','contract_templates','contract_template_versions','contract_template_fields','contract_drafts','contract_draft_documents','contract_generation_jobs','contract_generation_results','contract_analysis_jobs','contract_notification_outbox'] loop
  execute format('alter table public.%I enable row level security',item);
  execute format('revoke all on public.%I from public, anon, authenticated',item);
  execute format('grant select, insert, update, delete on public.%I to service_role',item);
 end loop;
end $$;
revoke all on all functions in schema contract_private from public, anon, authenticated;
grant usage on schema contract_private to service_role;
grant execute on all functions in schema contract_private to service_role;
insert into public.contract_schema_versions(version) values(1);
commit;
