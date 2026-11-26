-- Agent Service: tự động xóa hội thoại (và toàn bộ tin nhắn ON DELETE CASCADE) khi quá 3 ngày không có tin nhắn mới.
-- Chạy được nhiều lần (idempotent), tương thích cả PostgreSQL chuẩn (Docker/E2E không có pg_cron) lẫn Supabase (có pg_cron).
-- Cách chạy trên Supabase: bật extension pg_cron (Database -> Extensions hoặc CREATE EXTENSION IF NOT EXISTS pg_cron),
-- sau đó chạy toàn bộ file này trong SQL Editor.

BEGIN;

-- Index phục vụ quét và xóa theo mốc updated_at mà không quét toàn bảng (Seq Scan).
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
    ON public.conversations (updated_at);

-- Hàm xóa theo từng lô ngắn (tránh khóa bảng lâu). Bảng messages (và conversation_summaries nếu có) tự xóa theo ON DELETE CASCADE.
CREATE OR REPLACE FUNCTION public.purge_inactive_conversations(
    retention_interval interval DEFAULT interval '3 days',
    batch_limit integer DEFAULT 200
)
RETURNS integer
LANGUAGE plpgsql
SET search_path = ''
AS $$
DECLARE
    v_limit integer := GREATEST(1, LEAST(COALESCE(batch_limit, 200), 5000));
    v_cutoff timestamptz := pg_catalog.now() - retention_interval;
    v_deleted integer := 0;
BEGIN
    IF retention_interval IS NULL OR retention_interval <= interval '0 seconds' THEN
        RETURN 0;
    END IF;

    WITH doomed AS (
        SELECT id
        FROM public.conversations
        WHERE updated_at <= v_cutoff
        ORDER BY updated_at ASC, id ASC
        LIMIT v_limit
    )
    DELETE FROM public.conversations AS c
    USING doomed
    WHERE c.id = doomed.id
      AND c.updated_at <= v_cutoff;

    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;

-- Khóa quyền gọi hàm qua Supabase REST API (anon/authenticated); chỉ owner / postgres / pg_cron được phép chạy.
REVOKE ALL ON FUNCTION public.purge_inactive_conversations(interval, integer) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.purge_inactive_conversations(interval, integer) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON FUNCTION public.purge_inactive_conversations(interval, integer) FROM authenticated;
    END IF;
END
$$;

-- Nếu môi trường (Supabase) đã bật pg_cron thì tự đăng ký lịch chạy mỗi 15 phút (idempotent).
DO $$
DECLARE
    v_job_id bigint;
BEGIN
    IF pg_catalog.to_regclass('cron.job') IS NOT NULL THEN
        FOR v_job_id IN
            SELECT jobid FROM cron.job WHERE jobname = 'purge_inactive_conversations'
        LOOP
            PERFORM cron.unschedule(v_job_id);
        END LOOP;

        PERFORM cron.schedule(
            'purge_inactive_conversations',
            '*/15 * * * *',
            $cron$SELECT public.purge_inactive_conversations(interval '3 days', 500);$cron$
        );
    END IF;
END
$$;

COMMIT;
