BEGIN;

ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA public
    FROM PUBLIC, anon, authenticated;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_proc
        WHERE oid = to_regprocedure('public.update_updated_at()')
          AND prokind = 'f'
          AND prorettype = 'pg_catalog.trigger'::regtype
    ) THEN
        ALTER FUNCTION public.update_updated_at() SET search_path = '';
    END IF;
END
$$;

COMMIT;
