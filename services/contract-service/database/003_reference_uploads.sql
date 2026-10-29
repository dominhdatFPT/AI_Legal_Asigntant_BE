-- Public blank PDF templates uploaded by administrators. Existing catalogs are unchanged.
BEGIN;
CREATE TABLE IF NOT EXISTS public.contract_reference_uploads (
    id UUID PRIMARY KEY,
    domain TEXT NOT NULL CHECK (domain IN ('land', 'business')),
    procedure_key TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    metadata JSONB NOT NULL,
    file_data BYTEA NOT NULL CHECK (octet_length(file_data) BETWEEN 1 AND 10485760),
    page_images BYTEA[] NOT NULL CHECK (cardinality(page_images) BETWEEN 1 AND 100),
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain, procedure_key, source_sha256)
);
ALTER TABLE public.contract_reference_uploads ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.contract_reference_uploads FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON public.contract_reference_uploads FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON public.contract_reference_uploads FROM authenticated;
    END IF;
END $$;
COMMIT;
