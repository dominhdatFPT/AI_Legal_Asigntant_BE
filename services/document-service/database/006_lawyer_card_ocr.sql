-- Additive migration for lawyer card OCR (Lawyer feature). No change to existing tables, bucket or User DB.
-- Run once in the Document project SQL Editor AFTER 001-005. Re-run is safe (IF NOT EXISTS); it does not alter
-- an existing table, so schema changes need a new numbered file.
-- One row per uploaded lawyer card image. The image is re-encoded server-side (fresh JPEG pixels, no EXIF or
-- original bytes) and stored private under safe/<owner>/lawyer-card/<id>.jpg so Lawyer Service can request a
-- 300 s signed URL for the admin reviewer. OCR failure keeps the row (status failed) so users can type the
-- card number by hand. owner_user_id is the JWT sub from User Service, no FK to User DB.
BEGIN;
CREATE TABLE IF NOT EXISTS public.document_lawyer_card_ocr (
    id uuid PRIMARY KEY,
    owner_user_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash varchar(64) NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    status text NOT NULL DEFAULT 'processing' CHECK (status IN ('processing','completed','failed')),
    storage_key text NOT NULL UNIQUE,
    sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    size_bytes integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 4194304),
    engine text NOT NULL CHECK (length(engine) BETWEEN 1 AND 100),
    raw jsonb,
    fields jsonb,
    warnings jsonb NOT NULL DEFAULT '[]',
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_user_id, idempotency_key),
    CHECK (storage_key = 'safe/' || owner_user_id::text || '/lawyer-card/' || id::text || '.jpg'),
    CHECK (raw IS NULL OR (jsonb_typeof(raw) = 'object' AND octet_length(raw::text) <= 2097152)),
    CHECK (jsonb_typeof(warnings) = 'array'),
    CHECK ((status = 'completed' AND fields IS NOT NULL AND jsonb_typeof(fields) = 'object' AND error_code IS NULL)
        OR (status = 'processing' AND fields IS NULL AND error_code IS NULL)
        OR (status = 'failed' AND fields IS NULL AND error_code IS NOT NULL))
);
ALTER TABLE public.document_lawyer_card_ocr ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.document_lawyer_card_ocr FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.document_lawyer_card_ocr TO service_role;
CREATE INDEX IF NOT EXISTS document_lawyer_card_ocr_owner_idx
    ON public.document_lawyer_card_ocr (owner_user_id, created_at DESC);
COMMIT;

-- Read-only verification: expect one row with rls_enabled = true, then zero browser grants.
select c.relname as table_name, c.relrowsecurity as rls_enabled
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname = 'document_lawyer_card_ocr';

select grantee, privilege_type from information_schema.role_table_grants
where table_schema = 'public' and table_name = 'document_lawyer_card_ocr'
  and grantee in ('PUBLIC', 'anon', 'authenticated');
