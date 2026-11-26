BEGIN;
SET LOCAL lock_timeout = '2s';

CREATE INDEX IF NOT EXISTS ix_notifications_user_created_id
    ON public.notifications (user_id, created_at DESC, id DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_group_notif_audience_created_id
    ON public.group_notifications (audience, created_at DESC, id DESC)
    WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS ix_notifications_broadcast_reads
    ON public.notifications (source_id)
    WHERE is_read = true AND deleted_at IS NULL AND source_type = 'broadcast' AND source_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_broadcasts_created_desc
    ON public.broadcasts (created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS ix_broadcasts_status_created_desc
    ON public.broadcasts (status, created_at DESC, id DESC);

COMMIT;
