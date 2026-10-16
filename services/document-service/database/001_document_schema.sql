-- Document Service / PostgreSQL 15+ / Supabase SQL Editor.
-- Run ONCE on the Document project. A name collision aborts the entire transaction.
-- No FK to auth.users: owner_user_id is the UUID sub issued by user-service.
begin;

create schema document_private;
revoke all on schema document_private from public, anon, authenticated;

create type public.document_purpose as enum ('contract_template', 'supporting_document');
create type public.document_type as enum ('contract', 'identity_card', 'other');
create type public.document_file_status as enum ('pending_check', 'available', 'rejected');
create type public.document_job_status as enum ('queued', 'processing', 'completed', 'failed');
create type public.document_source_kind as enum ('docx', 'pdf_text', 'pdf_scan', 'pdf_mixed', 'image');
create type public.identity_profile_status as enum ('active', 'deleted');

create table public.user_files (
    id uuid primary key default gen_random_uuid(),
    owner_user_id uuid not null,
    purpose public.document_purpose not null,
    document_type public.document_type not null,
    original_filename varchar(255) not null check (btrim(original_filename) <> ''),
    media_type varchar(150) not null,
    uploaded_size_bytes bigint not null check (uploaded_size_bytes between 1 and 9437184),
    uploaded_sha256 varchar(64) not null check (uploaded_sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint check (size_bytes between 1 and 9437184),
    sha256 varchar(64) check (sha256 ~ '^[0-9a-f]{64}$'),
    storage_key text not null unique check (btrim(storage_key) <> ''),
    file_status public.document_file_status not null default 'pending_check',
    error_code varchar(100),
    idempotency_key uuid not null,
    request_hash varchar(64) not null check (request_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    unique (owner_user_id, idempotency_key),
    constraint document_allowed_media check (media_type in (
        'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'image/jpeg', 'image/png')),
    constraint document_template_scope check (purpose <> 'contract_template' or (
        document_type = 'contract' and media_type in (
            'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))),
    constraint document_identity_scope check (document_type <> 'identity_card' or (
        purpose = 'supporting_document' and media_type in ('application/pdf', 'image/jpeg', 'image/png'))),
    constraint document_available_content check (file_status <> 'available' or (
        size_bytes is not null and sha256 is not null and error_code is null)),
    constraint document_rejected_error check (file_status <> 'rejected' or (
        error_code is not null and btrim(error_code) <> ''))
);
create index document_owner_created_idx on public.user_files (owner_user_id, created_at desc, id desc)
    where deleted_at is null;

create table public.document_processing_jobs (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references public.user_files(id) on delete restrict,
    attempt integer not null default 1 check (attempt >= 1),
    status public.document_job_status not null default 'queued',
    retry_count integer not null default 0 check (retry_count >= 0),
    error_code varchar(100),
    created_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz,
    updated_at timestamptz not null default now(),
    unique (document_id, attempt),
    constraint document_job_terminal_time check (
        (status in ('completed', 'failed')) = (completed_at is not null)),
    constraint document_job_processing_time check (status <> 'processing' or started_at is not null),
    constraint document_job_error check (
        (status = 'failed' and error_code is not null and btrim(error_code) <> '')
        or (status <> 'failed' and error_code is null)),
    constraint document_job_time_order check (
        (started_at is null or started_at >= created_at)
        and (completed_at is null or completed_at >= coalesce(started_at, created_at)))
);
create unique index document_one_active_job_idx on public.document_processing_jobs (document_id)
    where status in ('queued', 'processing');
create index document_job_queue_idx on public.document_processing_jobs (status, created_at);

create table public.document_extraction_results (
    id uuid primary key default gen_random_uuid(),
    job_id uuid not null unique references public.document_processing_jobs(id) on delete restrict,
    schema_version varchar(20) not null default '1.0' check (schema_version = '1.0'),
    source_sha256 varchar(64) not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
    source_kind public.document_source_kind not null,
    text text not null default '' check (char_length(text) <= 1000000),
    pages jsonb not null default '[]'::jsonb check (jsonb_typeof(pages) = 'array'),
    extracted_fields jsonb not null default '[]'::jsonb check (jsonb_typeof(extracted_fields) = 'array'),
    warnings jsonb not null default '[]'::jsonb check (jsonb_typeof(warnings) = 'array'),
    engine_version varchar(100) not null check (btrim(engine_version) <> ''),
    created_at timestamptz not null default now(),
    constraint document_docx_pages check (source_kind <> 'docx' or pages = '[]'::jsonb),
    constraint document_page_limit check (jsonb_array_length(pages) <= 100),
    constraint document_result_json_size check (octet_length(jsonb_build_object(
        'schema_version', schema_version, 'source_sha256', source_sha256,
        'source_kind', source_kind, 'text', text, 'pages', pages,
        'extracted_fields', extracted_fields, 'warnings', warnings,
        'engine_version', engine_version)::text) <= 16777216)
);

create table public.user_identity_profiles (
    owner_user_id uuid primary key,
    source_document_id uuid not null references public.user_files(id) on delete restrict,
    source_result_id uuid not null references public.document_extraction_results(id) on delete restrict,
    revision integer not null default 1 check (revision >= 1),
    status public.identity_profile_status not null default 'active',
    full_name varchar(200) check (full_name is null or btrim(full_name) <> ''),
    date_of_birth date,
    identity_number varchar(12) check (identity_number ~ '^[0-9]{12}$'),
    issue_date date,
    issue_place varchar(500) check (issue_place is null or btrim(issue_place) <> ''),
    permanent_address text check (permanent_address is null or btrim(permanent_address) <> ''),
    confirmed_at timestamptz not null,
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    constraint document_profile_delete_state check ((status = 'deleted') = (deleted_at is not null)),
    constraint document_profile_has_field check (status <> 'active' or (
        full_name is not null or date_of_birth is not null or identity_number is not null
        or issue_date is not null or issue_place is not null or permanent_address is not null))
);
create index document_profile_source_idx on public.user_identity_profiles (source_document_id)
    where status = 'active';
create index document_profile_result_idx on public.user_identity_profiles (source_result_id);

-- All writers lock the source file first. The API must also follow this order
-- before locking a job/profile; SQLSTATE 40P01/40001 may require a transaction retry.
create function document_private.guard_file() returns trigger
language plpgsql set search_path = '' as $$
begin
    if new.id <> old.id or new.owner_user_id <> old.owner_user_id then
        raise exception 'DOCUMENT_OWNER_IMMUTABLE' using errcode = '23514';
    end if;
    if old.deleted_at is not null and new is distinct from old then
        raise exception 'DOCUMENT_DELETED' using errcode = '23514';
    end if;
    if old.file_status in ('available', 'rejected') and row(
        new.purpose, new.document_type, new.original_filename, new.media_type,
        new.uploaded_size_bytes, new.uploaded_sha256, new.size_bytes, new.sha256,
        new.storage_key, new.file_status, new.error_code, new.idempotency_key,
        new.request_hash, new.created_at
    ) is distinct from row(
        old.purpose, old.document_type, old.original_filename, old.media_type,
        old.uploaded_size_bytes, old.uploaded_sha256, old.size_bytes, old.sha256,
        old.storage_key, old.file_status, old.error_code, old.idempotency_key,
        old.request_hash, old.created_at
    ) then
        raise exception 'DOCUMENT_CONTENT_IMMUTABLE' using errcode = '23514';
    end if;
    if old.deleted_at is null and new.deleted_at is not null then
        if exists (select 1 from public.document_processing_jobs
                   where document_id = old.id and status in ('queued', 'processing')) then
            raise exception 'DOCUMENT_PROCESSING_ACTIVE' using errcode = '23514';
        end if;
        if exists (select 1 from public.user_identity_profiles
                   where source_document_id = old.id and status = 'active') then
            raise exception 'PROFILE_SOURCE_IN_USE' using errcode = '23514';
        end if;
    end if;
    new.updated_at := now();
    return new;
end;
$$;
create trigger document_guard_file before update on public.user_files
    for each row execute function document_private.guard_file();

create function document_private.guard_job() returns trigger
language plpgsql set search_path = '' as $$
declare source_file public.user_files;
begin
    select * into source_file from public.user_files where id = new.document_id for update;
    if not found or source_file.deleted_at is not null then
        raise exception 'DOCUMENT_NOT_FOUND' using errcode = '23514';
    end if;
    if tg_op = 'INSERT' then
        if exists (select 1 from public.user_identity_profiles
                   where source_document_id = new.document_id and status = 'active') then
            raise exception 'PROFILE_SOURCE_IN_USE' using errcode = '23514';
        end if;
    else
        if row(new.id, new.document_id, new.attempt, new.created_at)
           is distinct from row(old.id, old.document_id, old.attempt, old.created_at) then
            raise exception 'DOCUMENT_JOB_SOURCE_IMMUTABLE' using errcode = '23514';
        end if;
        if old.status in ('completed', 'failed') and new is distinct from old then
            raise exception 'DOCUMENT_JOB_TERMINAL' using errcode = '23514';
        end if;
    end if;
    new.updated_at := now();
    return new;
end;
$$;
create trigger document_guard_job before insert or update on public.document_processing_jobs
    for each row execute function document_private.guard_job();

create function document_private.guard_result() returns trigger
language plpgsql set search_path = '' as $$
declare source_file public.user_files;
begin
    if tg_op <> 'INSERT' then
        raise exception 'DOCUMENT_RESULT_IMMUTABLE' using errcode = '23514';
    end if;
    select f.* into source_file from public.user_files f
        join public.document_processing_jobs j on j.document_id = f.id
        where j.id = new.job_id for update of f;
    if not found or source_file.deleted_at is not null or source_file.file_status <> 'available'
       or new.source_sha256 <> source_file.sha256 then
        raise exception 'DOCUMENT_RESULT_SOURCE_INVALID' using errcode = '23514';
    end if;
    if not ((source_file.media_type = 'application/pdf' and new.source_kind in ('pdf_text', 'pdf_scan', 'pdf_mixed'))
        or (source_file.media_type in ('image/jpeg', 'image/png') and new.source_kind = 'image')
        or (source_file.media_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            and new.source_kind = 'docx')) then
        raise exception 'DOCUMENT_RESULT_KIND_INVALID' using errcode = '23514';
    end if;
    return new;
end;
$$;
create trigger document_guard_result before insert or update or delete on public.document_extraction_results
    for each row execute function document_private.guard_result();

-- Deferred: worker inserts result and marks job completed in the SAME transaction,
-- in either order. Neither half may commit independently.
create function document_private.check_job_result() returns trigger
language plpgsql set search_path = '' as $$
declare target_job uuid; current_status public.document_job_status; has_result boolean;
begin
    if tg_table_name = 'document_extraction_results' then
        target_job := new.job_id;
    else
        target_job := new.id;
    end if;
    select status into current_status from public.document_processing_jobs where id = target_job;
    select exists (select 1 from public.document_extraction_results where job_id = target_job) into has_result;
    if (current_status = 'completed') is distinct from has_result then
        raise exception 'DOCUMENT_JOB_RESULT_MISMATCH' using errcode = '23514';
    end if;
    return null;
end;
$$;
create constraint trigger document_job_result_consistency
    after insert or update on public.document_processing_jobs deferrable initially deferred
    for each row execute function document_private.check_job_result();
create constraint trigger document_result_job_consistency
    after insert on public.document_extraction_results deferrable initially deferred
    for each row execute function document_private.check_job_result();

create function document_private.guard_profile() returns trigger
language plpgsql set search_path = '' as $$
declare source_file public.user_files; latest_job public.document_processing_jobs;
begin
    if tg_op = 'DELETE' then
        raise exception 'IDENTITY_PROFILE_SOFT_DELETE_REQUIRED' using errcode = '23514';
    end if;
    select * into source_file from public.user_files where id = new.source_document_id for update;
    if tg_op = 'INSERT' then
        if new.revision <> 1 or new.status <> 'active' then
            raise exception 'IDENTITY_PROFILE_INITIAL_REVISION' using errcode = '23514';
        end if;
    else
        if new.owner_user_id <> old.owner_user_id or new.revision <> old.revision + 1 then
            raise exception 'IDENTITY_PROFILE_REVISION_INVALID' using errcode = '23514';
        end if;
        if new.status = 'deleted' and (
            old.status <> 'active' or row(new.source_document_id, new.source_result_id,
                new.full_name, new.date_of_birth, new.identity_number, new.issue_date,
                new.issue_place, new.permanent_address, new.confirmed_at)
            is distinct from row(old.source_document_id, old.source_result_id,
                old.full_name, old.date_of_birth, old.identity_number, old.issue_date,
                old.issue_place, old.permanent_address, old.confirmed_at)) then
            raise exception 'IDENTITY_PROFILE_UNLINK_INVALID' using errcode = '23514';
        end if;
    end if;
    if new.status = 'active' then
        if source_file.id is null or source_file.owner_user_id <> new.owner_user_id
           or source_file.deleted_at is not null or source_file.file_status <> 'available'
           or source_file.purpose <> 'supporting_document' or source_file.document_type <> 'identity_card'
           or source_file.media_type not in ('application/pdf', 'image/jpeg', 'image/png') then
            raise exception 'IDENTITY_PROFILE_SOURCE_INVALID' using errcode = '23514';
        end if;
        select * into latest_job from public.document_processing_jobs
            where document_id = new.source_document_id order by attempt desc limit 1;
        if latest_job.id is null or latest_job.status <> 'completed' or not exists (
            select 1 from public.document_extraction_results
            where id = new.source_result_id and job_id = latest_job.id and source_sha256 = source_file.sha256
        ) then
            raise exception 'IDENTITY_PROFILE_RESULT_INVALID' using errcode = '23514';
        end if;
    end if;
    new.updated_at := now();
    return new;
end;
$$;
create trigger document_guard_profile before insert or update or delete on public.user_identity_profiles
    for each row execute function document_private.guard_profile();

-- Backend-only access. Our JWT is NOT automatically a Supabase Auth session.
-- No browser CRUD policy; backend MUST validate JWT/owner/CSRF on each request.
alter table public.user_files enable row level security;
alter table public.document_processing_jobs enable row level security;
alter table public.document_extraction_results enable row level security;
alter table public.user_identity_profiles enable row level security;
revoke all on public.user_files, public.document_processing_jobs,
    public.document_extraction_results, public.user_identity_profiles from public, anon, authenticated;
grant select, insert, update on public.user_files, public.document_processing_jobs,
    public.user_identity_profiles to service_role;
grant select, insert on public.document_extraction_results to service_role;
revoke all on all functions in schema document_private from public, anon, authenticated;
grant usage on schema document_private to service_role;
grant execute on all functions in schema document_private to service_role;

commit;
