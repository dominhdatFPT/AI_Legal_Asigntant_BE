-- Task 5: extend existing profiles, preserving all legacy document/result rows.
-- Run after 001 and 003, once, only on the Document project.
BEGIN;
ALTER TABLE public.user_identity_profiles
    ADD COLUMN source_session_id uuid REFERENCES public.document_identity_ocr_sessions(id) ON DELETE RESTRICT,
    ALTER COLUMN source_document_id DROP NOT NULL,
    ALTER COLUMN source_result_id DROP NOT NULL,
    ADD CONSTRAINT document_profile_source_xor CHECK (
        (source_session_id IS NOT NULL AND source_document_id IS NULL AND source_result_id IS NULL)
        OR (source_session_id IS NULL AND source_document_id IS NOT NULL AND source_result_id IS NOT NULL)),
    ADD CONSTRAINT document_profile_address_limit CHECK (char_length(permanent_address) <= 2000);
CREATE INDEX document_profile_session_idx ON public.user_identity_profiles(source_session_id);

-- All application writers lock source(s), then profile. Existing file branch
-- remains supported for Task 6; no fake user_files/job/result for two-image OCR.
CREATE OR REPLACE FUNCTION document_private.guard_profile() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE source_file public.user_files; latest_job public.document_processing_jobs;
        source_session public.document_identity_ocr_sessions;
BEGIN
    IF tg_op = 'DELETE' THEN
        RAISE EXCEPTION 'IDENTITY_PROFILE_SOFT_DELETE_REQUIRED' USING errcode = '23514';
    END IF;
    IF new.source_session_id IS NOT NULL THEN
        SELECT * INTO source_session FROM public.document_identity_ocr_sessions WHERE id=new.source_session_id FOR UPDATE;
    ELSE
        SELECT * INTO source_file FROM public.user_files WHERE id=new.source_document_id FOR UPDATE;
    END IF;
    IF tg_op = 'INSERT' THEN
        IF new.revision <> 1 OR new.status <> 'active' THEN
            RAISE EXCEPTION 'IDENTITY_PROFILE_INITIAL_REVISION' USING errcode = '23514';
        END IF;
    ELSE
        IF new.owner_user_id <> old.owner_user_id OR new.revision <> old.revision + 1 THEN
            RAISE EXCEPTION 'IDENTITY_PROFILE_REVISION_INVALID' USING errcode = '23514';
        END IF;
        IF new.status = 'deleted' AND (
            old.status <> 'active' OR row(new.source_session_id,new.source_document_id,new.source_result_id,
                new.full_name,new.date_of_birth,new.identity_number,new.issue_date,new.issue_place,
                new.permanent_address,new.confirmed_at)
            IS DISTINCT FROM row(old.source_session_id,old.source_document_id,old.source_result_id,
                old.full_name,old.date_of_birth,old.identity_number,old.issue_date,old.issue_place,
                old.permanent_address,old.confirmed_at)) THEN
            RAISE EXCEPTION 'IDENTITY_PROFILE_UNLINK_INVALID' USING errcode = '23514';
        END IF;
    END IF;
    IF new.status = 'active' THEN
        IF new.source_session_id IS NOT NULL THEN
            IF source_session.id IS NULL OR source_session.owner_user_id <> new.owner_user_id
               OR source_session.status <> 'completed' THEN
                RAISE EXCEPTION 'IDENTITY_PROFILE_SESSION_INVALID' USING errcode = '23514';
            END IF;
        ELSE
            IF source_file.id IS NULL OR source_file.owner_user_id <> new.owner_user_id
               OR source_file.deleted_at IS NOT NULL OR source_file.file_status <> 'available'
               OR source_file.purpose <> 'supporting_document' OR source_file.document_type <> 'identity_card'
               OR source_file.media_type NOT IN ('application/pdf','image/jpeg','image/png') THEN
                RAISE EXCEPTION 'IDENTITY_PROFILE_SOURCE_INVALID' USING errcode = '23514';
            END IF;
            SELECT * INTO latest_job FROM public.document_processing_jobs
                WHERE document_id=new.source_document_id ORDER BY attempt DESC LIMIT 1;
            IF latest_job.id IS NULL OR latest_job.status <> 'completed' OR NOT EXISTS (
                SELECT 1 FROM public.document_extraction_results
                WHERE id=new.source_result_id AND job_id=latest_job.id AND source_sha256=source_file.sha256
            ) THEN
                RAISE EXCEPTION 'IDENTITY_PROFILE_RESULT_INVALID' USING errcode = '23514';
            END IF;
        END IF;
    END IF;
    new.updated_at := now();
    RETURN new;
END;
$$;

CREATE FUNCTION document_private.guard_identity_session() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    IF row(new.id,new.owner_user_id,new.idempotency_key,new.request_hash,new.front_storage_key,new.back_storage_key,
        new.front_sha256,new.back_sha256,new.front_size_bytes,new.back_size_bytes,new.created_at)
       IS DISTINCT FROM row(old.id,old.owner_user_id,old.idempotency_key,old.request_hash,old.front_storage_key,old.back_storage_key,
        old.front_sha256,old.back_sha256,old.front_size_bytes,old.back_size_bytes,old.created_at) THEN
        RAISE EXCEPTION 'IDENTITY_OCR_SOURCE_IMMUTABLE' USING errcode = '23514';
    END IF;
    IF old.status IN ('completed','failed') AND new IS DISTINCT FROM old THEN
        RAISE EXCEPTION 'IDENTITY_OCR_TERMINAL_IMMUTABLE' USING errcode = '23514';
    END IF;
    RETURN new;
END;
$$;
CREATE TRIGGER document_guard_identity_session BEFORE UPDATE ON public.document_identity_ocr_sessions
    FOR EACH ROW EXECUTE FUNCTION document_private.guard_identity_session();
REVOKE ALL ON FUNCTION document_private.guard_identity_session() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION document_private.guard_identity_session() TO service_role;
-- RLS, existing profile triggers and browser/backend privileges remain unchanged.
COMMIT;
