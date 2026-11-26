BEGIN;

DROP POLICY IF EXISTS "Users can view their own group notification reads"
    ON public.group_notification_reads;
DROP POLICY IF EXISTS "Users can manage their own group notification reads"
    ON public.group_notification_reads;

DROP POLICY IF EXISTS group_notification_reads_select_own
    ON public.group_notification_reads;
DROP POLICY IF EXISTS group_notification_reads_insert_own
    ON public.group_notification_reads;
DROP POLICY IF EXISTS group_notification_reads_update_own
    ON public.group_notification_reads;
DROP POLICY IF EXISTS group_notification_reads_delete_own
    ON public.group_notification_reads;

CREATE POLICY group_notification_reads_select_own
    ON public.group_notification_reads FOR SELECT TO authenticated
    USING ((SELECT auth.uid())::text = user_id::text);

CREATE POLICY group_notification_reads_insert_own
    ON public.group_notification_reads FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid())::text = user_id::text);

CREATE POLICY group_notification_reads_update_own
    ON public.group_notification_reads FOR UPDATE TO authenticated
    USING ((SELECT auth.uid())::text = user_id::text)
    WITH CHECK ((SELECT auth.uid())::text = user_id::text);

CREATE POLICY group_notification_reads_delete_own
    ON public.group_notification_reads FOR DELETE TO authenticated
    USING ((SELECT auth.uid())::text = user_id::text);

REVOKE ALL ON TABLE
    public.notifications,
    public.reports,
    public.report_attachments,
    public.report_status_history,
    public.group_notifications,
    public.group_notification_reads,
    public.broadcasts
FROM PUBLIC, anon;

REVOKE UPDATE ON TABLE public.notifications FROM authenticated;
GRANT UPDATE (is_read, read_at, deleted_at)
    ON TABLE public.notifications TO authenticated;

CREATE TABLE IF NOT EXISTS public.notification_outbox (
    id varchar(128) PRIMARY KEY,
    routing_key varchar(100) NOT NULL,
    payload_json text NOT NULL,
    created_at timestamptz NOT NULL,
    available_at timestamptz NOT NULL,
    published_at timestamptz
);

ALTER TABLE public.notification_outbox ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.notification_outbox
    FROM PUBLIC, anon, authenticated;

COMMIT;
