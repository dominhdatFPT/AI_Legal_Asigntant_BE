BEGIN;

ALTER TABLE public.legal_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ingest_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_versions ENABLE ROW LEVEL SECURITY;

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

-- Staging candidates. Compare EXPLAIN (ANALYZE, BUFFERS, SETTINGS) on the
-- actual semantic_search query before and after each candidate. Use
-- representative vectors, top_k values, model values and doc_type filters.
-- For HNSW, compare result counts and recall as well as execution time.
-- Resolve vector_cosine_ops from the installed vector extension schema.
--
-- CREATE INDEX IF NOT EXISTS idx_document_chunks_gemini_hnsw
--     ON public.document_chunks USING hnsw (embedding vector_cosine_ops)
--     WHERE embedding_model = 'gemini-embedding-001';
--
-- CREATE INDEX IF NOT EXISTS idx_legal_documents_active_processed_id
--     ON public.legal_documents (id)
--     WHERE status = 'active' AND is_processed IS TRUE;

COMMIT;
