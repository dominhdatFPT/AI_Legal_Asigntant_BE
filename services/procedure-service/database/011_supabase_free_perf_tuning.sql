BEGIN;
SET LOCAL lock_timeout = '2s';

ALTER TABLE public.procedures ADD COLUMN IF NOT EXISTS form_template_id uuid;

CREATE INDEX IF NOT EXISTS idx_biz_records_user_status_created
    ON public.business_procedure_records (user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_land_records_user_status_created
    ON public.land_procedure_records (user_id, status, created_at DESC);

-- Do not drop the old indexes if an existing namesake is invalid or has
-- a different definition. An exception rolls the entire migration back.
DO $$
DECLARE idx record;
BEGIN
    FOR idx IN
        SELECT c.relname, t.relname AS table_name, tn.nspname AS table_schema,
               i.indisvalid, i.indisready, pg_get_indexdef(i.indexrelid) AS definition
        FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_namespace tn ON tn.oid = t.relnamespace
        WHERE n.nspname = 'public' AND c.relname IN
            ('idx_biz_records_user_status_created', 'idx_land_records_user_status_created')
    LOOP
        IF idx.table_schema <> 'public' OR
           (idx.relname = 'idx_biz_records_user_status_created' AND idx.table_name <> 'business_procedure_records') OR
           (idx.relname = 'idx_land_records_user_status_created' AND idx.table_name <> 'land_procedure_records') OR
           NOT idx.indisvalid OR NOT idx.indisready OR
           idx.definition !~ 'USING btree \(user_id, status, created_at DESC\)$' THEN
            RAISE EXCEPTION 'Unexpected index definition or state: %', idx.relname;
        END IF;
    END LOOP;
END $$;
DROP INDEX IF EXISTS public.idx_biz_records_user_status;
DROP INDEX IF EXISTS public.idx_land_records_user_status;

CREATE INDEX IF NOT EXISTS idx_procedures_code_upper ON public.procedures (upper(code));
CREATE INDEX IF NOT EXISTS idx_procedures_active_domain_code
    ON public.procedures (domain, code) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_cft_live_domain_updated
    ON public.custom_form_templates (domain, updated_at DESC, id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_cft_published_domain_name
    ON public.custom_form_templates (domain, name, id)
    WHERE status = 'published' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_dft_published_proc_upper
    ON public.dynamic_form_templates (upper(procedure_id), version DESC, updated_at DESC, id DESC)
    WHERE status = 'published' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_dft_published_code_upper
    ON public.dynamic_form_templates (upper(code), version DESC, updated_at DESC, id DESC)
    WHERE status = 'published' AND deleted_at IS NULL;
COMMIT;
