BEGIN;

-- Base contract tables already have RLS in migrations 001 and 003.
-- The legacy catalog tables can predate the checked-in migrations.
ALTER TABLE IF EXISTS public.contract_reference_catalog ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.procedure_contract_forms ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.reference_classifications ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA public
    FROM PUBLIC, anon, authenticated;

ALTER FUNCTION contract_private.immutable_result() SET search_path = '';

CREATE INDEX IF NOT EXISTS contract_drafts_template_version_fk_idx
    ON public.contract_drafts (template_version_id);

CREATE INDEX IF NOT EXISTS contract_draft_documents_draft_owner_fk_idx
    ON public.contract_draft_documents (draft_id, owner_user_id);

CREATE INDEX IF NOT EXISTS contract_generation_jobs_draft_version_owner_fk_idx
    ON public.contract_generation_jobs (draft_id, template_version_id, owner_user_id);

CREATE INDEX IF NOT EXISTS contract_generation_results_job_owner_format_fk_idx
    ON public.contract_generation_results (job_id, owner_user_id, format);

CREATE INDEX IF NOT EXISTS contract_analysis_jobs_version_template_fk_idx
    ON public.contract_analysis_jobs (template_version_id, template_id);

COMMIT;
