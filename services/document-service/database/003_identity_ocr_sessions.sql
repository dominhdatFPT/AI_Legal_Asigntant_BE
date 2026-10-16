-- Additive Task 4 migration. No change to existing confirmed profiles or User DB.
BEGIN;
CREATE TABLE IF NOT EXISTS public.document_identity_ocr_sessions (
    id uuid PRIMARY KEY,
    owner_user_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash varchar(64) NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
    status text NOT NULL DEFAULT 'processing' CHECK (status IN ('processing','completed','failed')),
    front_storage_key text NOT NULL UNIQUE,
    back_storage_key text NOT NULL UNIQUE,
    front_sha256 varchar(64) NOT NULL CHECK (front_sha256 ~ '^[a-f0-9]{64}$'),
    back_sha256 varchar(64) NOT NULL CHECK (back_sha256 ~ '^[a-f0-9]{64}$'),
    front_size_bytes integer NOT NULL CHECK (front_size_bytes BETWEEN 1 AND 4194304),
    back_size_bytes integer NOT NULL CHECK (back_size_bytes BETWEEN 1 AND 4194304),
    raw_front jsonb,
    raw_back jsonb,
    fields jsonb,
    warnings jsonb NOT NULL DEFAULT '[]',
    verification_status text NOT NULL DEFAULT 'not_performed' CHECK (verification_status = 'not_performed'),
    error_code text,
    error_status integer CHECK (error_status IN (422,502,503,504)),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_user_id,idempotency_key),
    CHECK (front_storage_key = 'quarantine/' || owner_user_id::text || '/identity/' || id::text || '/front.jpg'),
    CHECK (back_storage_key = 'quarantine/' || owner_user_id::text || '/identity/' || id::text || '/back.jpg'),
    CHECK (raw_front IS NULL OR (jsonb_typeof(raw_front) = 'object' AND octet_length(raw_front::text) <= 2097152)),
    CHECK (raw_back IS NULL OR (jsonb_typeof(raw_back) = 'object' AND octet_length(raw_back::text) <= 2097152)),
    CHECK (jsonb_typeof(warnings) = 'array'),
    CHECK ((status = 'completed' AND raw_front IS NOT NULL AND raw_back IS NOT NULL
             AND fields IS NOT NULL AND jsonb_typeof(fields) = 'object' AND error_code IS NULL AND error_status IS NULL)
        OR (status = 'processing' AND fields IS NULL AND error_code IS NULL AND error_status IS NULL)
        OR (status = 'failed' AND fields IS NULL AND error_code IS NOT NULL AND error_status IS NOT NULL))
);
ALTER TABLE public.document_identity_ocr_sessions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.document_identity_ocr_sessions FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.document_identity_ocr_sessions TO service_role;
CREATE INDEX IF NOT EXISTS document_identity_ocr_owner_idx ON public.document_identity_ocr_sessions(owner_user_id,created_at DESC);
COMMIT;
