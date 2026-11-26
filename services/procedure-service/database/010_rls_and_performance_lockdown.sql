BEGIN;

DO $$
DECLARE
    pol record;
BEGIN
    FOR pol IN
        SELECT tablename, policyname
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename IN (
              'land_procedure_records',
              'business_procedure_records',
              'form_templates',
              'custom_form_submissions'
          )
        ORDER BY tablename, policyname
    LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON public.%I',
            pol.policyname, pol.tablename
        );
    END LOOP;
END
$$;

ALTER TABLE public.land_procedure_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.business_procedure_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.form_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.procedures ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.procedure_domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.procedure_conditions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.procedure_fees ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.procedure_legal_bases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.procedure_requirements ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dynamic_form_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.custom_form_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.custom_form_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.form_template_preview_pages ENABLE ROW LEVEL SECURITY;

-- These tables exist in the live Procedure project.
ALTER TABLE IF EXISTS public.reference_classifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.procedure_form_templates ENABLE ROW LEVEL SECURITY;

CREATE SCHEMA IF NOT EXISTS procedure_private;
REVOKE ALL ON SCHEMA procedure_private FROM PUBLIC, anon, authenticated;

ALTER TABLE IF EXISTS public.backup_custom_form_templates
    SET SCHEMA procedure_private;
ALTER TABLE IF EXISTS procedure_private.backup_custom_form_templates
    ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA procedure_private
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA public
    FROM PUBLIC, anon, authenticated;

CREATE INDEX IF NOT EXISTS idx_biz_records_procedure_id
    ON public.business_procedure_records (procedure_id);
CREATE INDEX IF NOT EXISTS idx_land_records_procedure_id
    ON public.land_procedure_records (procedure_id);
CREATE INDEX IF NOT EXISTS custom_form_submissions_template_id_idx
    ON public.custom_form_submissions (template_id);

COMMIT;
