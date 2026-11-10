CREATE TABLE IF NOT EXISTS public.form_template_preview_pages (
  template_id uuid NOT NULL REFERENCES public.form_templates(id) ON DELETE CASCADE,
  page_number integer NOT NULL CHECK (page_number BETWEEN 1 AND 100),
  image_data bytea NOT NULL CHECK (octet_length(image_data) BETWEEN 1 AND 10485760),
  PRIMARY KEY (template_id, page_number)
);
ALTER TABLE public.form_template_preview_pages ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.form_template_preview_pages FROM PUBLIC, anon, authenticated;
