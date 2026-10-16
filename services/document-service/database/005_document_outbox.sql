-- Additive T7. No backfill: do not notify historical terminal jobs.
begin;
create table public.document_notification_outbox (
    event_id uuid primary key default gen_random_uuid(),
    job_id uuid not null unique references public.document_processing_jobs(id) on delete restrict,
    document_id uuid not null references public.user_files(id) on delete restrict,
    recipient_user_id uuid not null,
    event_type text not null check (event_type in ('document.ocr_completed','document.ocr_failed')),
    occurred_at timestamptz not null,
    status text not null default 'pending' check (status in ('pending','sent','failed')),
    attempts integer not null default 0 check (attempts between 0 and 12),
    next_attempt_at timestamptz not null default now(),
    last_error_code text check (last_error_code ~ '^[A-Z0-9_]{1,80}$'),
    sent_at timestamptz,
    created_at timestamptz not null default now(),
    check ((status='sent') = (sent_at is not null))
);
create index document_outbox_due on public.document_notification_outbox(next_attempt_at,event_id) where status='pending';

create function document_private.enqueue_notification() returns trigger
language plpgsql security definer set search_path='' as $$
declare source public.user_files;
begin
    if new.status in ('completed','failed') and old.status not in ('completed','failed') then
        select * into strict source from public.user_files where id=new.document_id;
        insert into public.document_notification_outbox(job_id,document_id,recipient_user_id,event_type,occurred_at)
        values (new.id,new.document_id,source.owner_user_id,
            case when new.status='completed' then 'document.ocr_completed' else 'document.ocr_failed' end,
            new.completed_at);
    end if;
    return null;
end;
$$;
create trigger document_enqueue_notification after update of status on public.document_processing_jobs
    for each row execute function document_private.enqueue_notification();

create function document_private.guard_outbox() returns trigger
language plpgsql set search_path='' as $$
begin
    if tg_op='DELETE' then
        raise exception 'DOCUMENT_OUTBOX_IMMUTABLE' using errcode='23514';
    end if;
    if row(new.event_id,new.job_id,new.document_id,new.recipient_user_id,new.event_type,new.occurred_at,new.created_at)
       is distinct from row(old.event_id,old.job_id,old.document_id,old.recipient_user_id,old.event_type,old.occurred_at,old.created_at)
       or old.status<>'pending' or new.attempts<>old.attempts+1 then
        raise exception 'DOCUMENT_OUTBOX_IMMUTABLE' using errcode='23514';
    end if;
    return new;
end;
$$;
create trigger document_guard_outbox before update or delete on public.document_notification_outbox
    for each row execute function document_private.guard_outbox();
alter table public.document_notification_outbox enable row level security;
revoke all on public.document_notification_outbox from public,anon,authenticated,service_role;
grant select,update on public.document_notification_outbox to service_role;
revoke all on function document_private.enqueue_notification(),document_private.guard_outbox() from public,anon,authenticated;
grant execute on function document_private.guard_outbox() to service_role;
commit;
