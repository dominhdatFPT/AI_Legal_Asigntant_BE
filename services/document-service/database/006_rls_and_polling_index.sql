BEGIN;

ALTER TABLE IF EXISTS public.procedure_document_dossiers
    ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF to_regclass('public.procedure_document_dossiers') IS NOT NULL THEN
        REVOKE ALL ON TABLE public.procedure_document_dossiers
            FROM PUBLIC, anon, authenticated;
    END IF;
END
$$;

-- Worker._claim scans queued jobs eligible for retry and processing jobs
-- eligible for recovery. Its candidate order is created_at, id.
CREATE INDEX IF NOT EXISTS document_job_polling_idx
    ON public.document_processing_jobs (created_at, id)
    WHERE status = 'processing' OR status = 'queued';

CREATE INDEX IF NOT EXISTS document_outbox_document_id_idx
    ON public.document_notification_outbox (document_id);

COMMIT;
