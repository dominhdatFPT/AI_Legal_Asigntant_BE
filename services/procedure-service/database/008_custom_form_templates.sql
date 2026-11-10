-- Reusable admin-authored forms and their Word/PDF source files.
-- Additive only: leaves the existing public.form_templates catalogue untouched.
CREATE TABLE IF NOT EXISTS public.custom_form_templates (
  id uuid PRIMARY KEY,
  template_group_id uuid NOT NULL,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','hidden','archived')),
  deleted_at timestamptz,
  name text NOT NULL,
  domain text NOT NULL CHECK (domain IN ('dat_dai','ho_kinh_doanh','doanh_nghiep')),
  procedure_id text,
  content jsonb NOT NULL,
  word_data bytea,
  word_filename text,
  reference_pdf bytea,
  reference_pdf_filename text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (template_group_id, version)
);
CREATE INDEX IF NOT EXISTS custom_form_templates_public_idx
  ON public.custom_form_templates (domain, procedure_id, status, updated_at DESC)
  WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS public.custom_form_submissions (
  id uuid PRIMARY KEY,
  template_id uuid NOT NULL REFERENCES public.custom_form_templates(id),
  user_id uuid NOT NULL,
  status text NOT NULL DEFAULT 'created' CHECK (status IN ('created','submitted')),
  form_values jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS custom_form_submissions_user_idx
  ON public.custom_form_submissions (user_id, created_at DESC);

ALTER TABLE public.custom_form_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.custom_form_submissions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.custom_form_templates, public.custom_form_submissions FROM anon, authenticated;
