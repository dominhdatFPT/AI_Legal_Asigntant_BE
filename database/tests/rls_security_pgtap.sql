BEGIN;

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgtap WITH SCHEMA extensions;
SET LOCAL search_path = pg_catalog, extensions, public;
SET LOCAL row_security = on;

CREATE TEMP TABLE rls_services AS
SELECT service
FROM (VALUES
    ('procedure',    'procedures'),
    ('user',         'users'),
    ('contract',     'contract_schema_versions'),
    ('document',     'user_files'),
    ('notification', 'notifications'),
    ('agent',        'conversations'),
    ('rag',          'legal_documents')
) AS services(service, anchor)
WHERE CASE
    WHEN nullif(current_setting('rls_test.service', true), '') IS NOT NULL
        THEN service = current_setting('rls_test.service', true)
    ELSE to_regclass(format('public.%I', anchor)) IS NOT NULL
END;

CREATE TEMP TABLE rls_targets AS
SELECT
    inventory.service,
    entry.table_name,
    entry.table_name = ANY (ARRAY[
        'land_procedure_records', 'business_procedure_records',
        'users', 'refresh_tokens', 'citizen_profiles',
        'user_files', 'conversations', 'notifications'
    ]) AS probe_anon,
    inventory.service <> 'notification'
        OR entry.table_name = 'notification_outbox' AS backend_only,
    inventory.service <> 'notification'
        OR entry.table_name NOT IN ('report_categories', 'report_reasons')
        AS anon_private
FROM (VALUES
    ('procedure', ARRAY[
        'procedures', 'procedure_domains', 'procedure_conditions',
        'procedure_fees', 'procedure_legal_bases', 'procedure_requirements',
        'land_procedure_records', 'business_procedure_records',
        'reference_classifications', 'dynamic_form_templates',
        'form_templates', 'custom_form_templates', 'custom_form_submissions',
        'form_template_preview_pages', 'procedure_form_templates'
    ]),
    ('user', ARRAY['users', 'refresh_tokens', 'citizen_profiles']),
    ('contract', ARRAY[
        'contract_schema_versions', 'contract_templates',
        'contract_template_versions', 'contract_template_fields',
        'contract_drafts', 'contract_draft_documents',
        'contract_generation_jobs', 'contract_generation_results',
        'contract_analysis_jobs', 'contract_notification_outbox',
        'contract_reference_uploads', 'contract_reference_catalog',
        'procedure_contract_forms', 'reference_classifications'
    ]),
    ('document', ARRAY[
        'user_files', 'document_processing_jobs', 'document_extraction_results',
        'user_identity_profiles', 'document_identity_ocr_sessions',
        'document_notification_outbox', 'procedure_document_dossiers'
    ]),
    ('notification', ARRAY[
        'notifications', 'report_categories', 'report_reasons', 'reports',
        'report_attachments', 'report_status_history', 'group_notifications',
        'group_notification_reads', 'broadcasts', 'notification_outbox'
    ]),
    ('agent', ARRAY['conversations', 'messages']),
    ('rag', ARRAY['legal_documents', 'document_chunks', 'ingest_log',
                  'document_versions'])
) AS inventory(service, table_names)
JOIN rls_services USING (service)
CROSS JOIN LATERAL unnest(inventory.table_names) AS entry(table_name);

CREATE TEMP TABLE rls_functions AS
SELECT expected.service, signature, required
FROM (VALUES
    ('contract', 'contract_private.immutable_result()', true),
    ('agent',    'public.update_updated_at()', false),
    ('rag',      'public.update_updated_at()', false)
) AS expected(service, signature, required)
JOIN rls_services USING (service);

CREATE TEMP TABLE rls_notification_fixture AS
SELECT gen_random_uuid()::text AS user_a, gen_random_uuid()::text AS user_b
FROM rls_services
WHERE service = 'notification';

DO $$
DECLARE
    fixture record;
    reason text;
BEGIN
    SELECT * INTO fixture FROM rls_notification_fixture;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    reason := 'TAP_' || replace(fixture.user_a, '-', '');

    INSERT INTO public.notifications (id, user_id, event_type, title, message)
    VALUES
        (fixture.user_a, fixture.user_a, 'rls.test', 'A', 'Fixture'),
        (fixture.user_b, fixture.user_b, 'rls.test', 'B', 'Fixture');

    INSERT INTO public.report_reasons (code, kind, name_vi)
    VALUES (reason, 'ticket', 'RLS fixture');

    INSERT INTO public.reports (id, code, kind, reporter_id, reporter_role, reason_code, title)
    VALUES
        (fixture.user_a, 'TAP-' || left(fixture.user_a, 20), 'ticket',
         fixture.user_a, 'citizen', reason, 'A'),
        (fixture.user_b, 'TAP-' || left(fixture.user_b, 20), 'ticket',
         fixture.user_b, 'citizen', reason, 'B');

    INSERT INTO public.report_attachments
        (id, report_id, uploader_id, storage_key, file_name, mime_type, size_bytes)
    VALUES
        (fixture.user_b, fixture.user_b, fixture.user_b,
         'rls-fixture/' || fixture.user_b, 'fixture.png', 'image/png', 1);

    -- Supply an explicit ID so the fixture does not advance the sequence.
    INSERT INTO public.report_status_history
        (id, report_id, to_status, actor_id, actor_role)
    VALUES
        (-1 - floor(random() * 1000000000000000)::bigint,
         fixture.user_b, 'pending', fixture.user_b, 'citizen');

    INSERT INTO public.group_notifications (id, audience, event_type, title, message)
    VALUES
        (fixture.user_a, 'all', 'rls.test', 'Group A', 'Fixture'),
        (fixture.user_b, 'all', 'rls.test', 'Group B', 'Fixture');

    INSERT INTO public.group_notification_reads (group_notification_id, user_id)
    VALUES (fixture.user_a, fixture.user_a), (fixture.user_a, fixture.user_b);
END
$$;

CREATE TEMP TABLE rls_notification_cases AS
SELECT
    cases.position,
    cases.description,
    format(cases.statement, f.user_a, f.user_b) AS statement,
    cases.expected::bigint[] AS expected
FROM rls_notification_fixture AS f
CROSS JOIN (VALUES
    (1, 'User A reads their own notification',
        $$SELECT id FROM public.notifications WHERE id = %1$L$$, ARRAY[1]),
    (2, 'User A cannot read User B notification',
        $$SELECT id FROM public.notifications WHERE id = %2$L$$, ARRAY[0]),
    (3, 'User A can mark their own notification as read',
        $$UPDATE public.notifications SET is_read = true WHERE id = %1$L$$, ARRAY[1]),
    (4, 'User A cannot mark User B notification as read',
        $$UPDATE public.notifications SET is_read = true WHERE id = %2$L$$, ARRAY[0]),
    (5, 'User A cannot edit notification content',
        $$UPDATE public.notifications SET title = 'changed' WHERE id = %1$L$$, ARRAY[-1]),
    (6, 'User A reads their own report',
        $$SELECT id FROM public.reports WHERE id = %1$L$$, ARRAY[1]),
    (7, 'User A cannot read User B report',
        $$SELECT id FROM public.reports WHERE id = %2$L$$, ARRAY[0]),
    (8, 'User A cannot update User B report',
        $$UPDATE public.reports SET title = 'changed' WHERE id = %2$L$$, ARRAY[0, -1]),
    (9, 'User A cannot read User B report attachment',
        $$SELECT id FROM public.report_attachments WHERE report_id = %2$L$$, ARRAY[0, -1]),
    (10, 'User A cannot read User B report history',
        $$SELECT id FROM public.report_status_history WHERE report_id = %2$L$$, ARRAY[0, -1]),
    (11, 'User A reads their own group receipt',
        $$SELECT user_id FROM public.group_notification_reads
          WHERE group_notification_id = %1$L AND user_id = %1$L$$, ARRAY[1]),
    (12, 'User A cannot read User B group receipt',
        $$SELECT user_id FROM public.group_notification_reads
          WHERE group_notification_id = %1$L AND user_id = %2$L$$, ARRAY[0]),
    (13, 'User A can update their own group receipt',
        $$UPDATE public.group_notification_reads SET hidden = true
          WHERE group_notification_id = %1$L AND user_id = %1$L$$, ARRAY[1]),
    (14, 'User A cannot update User B group receipt',
        $$UPDATE public.group_notification_reads SET hidden = true
          WHERE group_notification_id = %1$L AND user_id = %2$L$$, ARRAY[0]),
    (15, 'User A cannot insert a group receipt as User B',
        $$INSERT INTO public.group_notification_reads (group_notification_id, user_id)
          VALUES (%2$L, %2$L)$$, ARRAY[-1]),
    (16, 'User A can insert their own group receipt',
        $$INSERT INTO public.group_notification_reads (group_notification_id, user_id)
          VALUES (%2$L, %1$L)$$, ARRAY[1]),
    (17, 'User A cannot delete User B group receipt',
        $$DELETE FROM public.group_notification_reads
          WHERE group_notification_id = %1$L AND user_id = %2$L$$, ARRAY[0]),
    (18, 'User A can delete their own group receipt',
        $$DELETE FROM public.group_notification_reads
          WHERE group_notification_id = %1$L AND user_id = %1$L$$, ARRAY[1])
) AS cases(position, description, statement, expected);

CREATE FUNCTION pg_temp.rls_rows_or_denied(statement text)
RETURNS bigint
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
    affected bigint;
BEGIN
    EXECUTE statement;
    GET DIAGNOSTICS affected = ROW_COUNT;
    RETURN affected;
EXCEPTION WHEN insufficient_privilege THEN
    RETURN -1;
END
$$;

GRANT SELECT ON TABLE rls_targets TO anon;
GRANT SELECT ON TABLE rls_notification_cases TO authenticated;
GRANT EXECUTE ON FUNCTION pg_temp.rls_rows_or_denied(text) TO authenticated;

SELECT plan(
    2
    + (SELECT count(*) FROM rls_targets)
    + (SELECT count(*) FROM rls_targets WHERE anon_private)
    + 3 * (SELECT count(*) FROM rls_targets WHERE probe_anon)
    + (SELECT count(*) FROM rls_functions)
    + (SELECT count(*) FROM rls_notification_cases)
    + 2 * (SELECT count(*) FROM rls_services WHERE service IN ('procedure', 'notification'))
);

SELECT ok(EXISTS (SELECT 1 FROM rls_services), 'A supported service is selected');

SELECT is_empty(
    $$SELECT c.relname
      FROM pg_class AS c
      JOIN pg_namespace AS n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public'
        AND c.relkind IN ('r', 'p')
        AND NOT c.relrowsecurity$$,
    'Every public table has RLS enabled'
);

SELECT ok(
    c.oid IS NOT NULL AND c.relkind IN ('r', 'p') AND c.relrowsecurity
        AND (t.service <> 'user' OR NOT c.relforcerowsecurity),
    format('%s: RLS is enabled on %s and the user-service owner can bypass it',
           t.service, t.table_name)
)
FROM rls_targets AS t
LEFT JOIN pg_class AS c ON c.oid = to_regclass(format('public.%I', t.table_name))
ORDER BY t.service, t.table_name;

-- Effective privileges include inherited grants and column-level grants.
SELECT ok(
    c.oid IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM (VALUES ('anon'), ('authenticated')) AS r(role_name)
        WHERE (r.role_name = 'anon' OR t.backend_only)
          AND (
              has_table_privilege(r.role_name, c.oid,
                  'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN')
              OR has_any_column_privilege(r.role_name, c.oid,
                  'SELECT, INSERT, UPDATE, REFERENCES')
          )
    ),
    format('%s: no client grants on private table %s', t.service, t.table_name)
)
FROM rls_targets AS t
LEFT JOIN pg_class AS c ON c.oid = to_regclass(format('public.%I', t.table_name))
WHERE t.anon_private
ORDER BY t.service, t.table_name;

SELECT ok(
    to_regclass('public.backup_custom_form_templates') IS NULL,
    'procedure: backup table is outside public'
)
FROM rls_services WHERE service = 'procedure';

SELECT is_empty(
    $$SELECT tablename, policyname FROM pg_policies
      WHERE schemaname = 'public'
        AND tablename IN ('land_procedure_records', 'business_procedure_records',
                          'form_templates', 'custom_form_submissions')$$,
    'procedure: protected tables have zero policies'
)
FROM rls_services WHERE service = 'procedure';

SELECT CASE
    WHEN p.oid IS NULL AND NOT f.required THEN
        skip(format('%s: %s is absent from this database', f.service, f.signature))
    ELSE ok(
        p.oid IS NOT NULL
            AND p.prokind = 'f'
            AND p.prorettype = 'pg_catalog.trigger'::regtype
            AND p.proconfig IS NOT NULL
            AND EXISTS (
                SELECT 1
                FROM pg_options_to_table(p.proconfig) AS setting
                WHERE setting.option_name = 'search_path'
                  AND setting.option_value IN ('', '""')
            ),
        format('%s: %s has a pinned empty search_path', f.service, f.signature)
    )
END
FROM rls_functions AS f
LEFT JOIN pg_proc AS p ON p.oid = to_regprocedure(f.signature)
ORDER BY f.service;

SELECT ok(
    (SELECT count(*) = 4
         AND count(DISTINCT cmd) = 4
         AND bool_and(cmd IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
                      AND roles = ARRAY['authenticated']::name[])
     FROM pg_policies
     WHERE schemaname = 'public'
       AND tablename = 'group_notification_reads'
       AND permissive = 'PERMISSIVE'
       AND roles && ARRAY['authenticated', 'public']::name[]),
    'notification: one authenticated group receipt policy per command'
)
FROM rls_services WHERE service = 'notification';

SELECT is(
    ARRAY(
        SELECT a.attname::text
        FROM pg_attribute AS a
        WHERE a.attrelid = to_regclass('public.notifications')
          AND a.attnum > 0
          AND NOT a.attisdropped
          AND has_column_privilege('authenticated', a.attrelid, a.attnum, 'UPDATE')
        ORDER BY a.attname::text
    ),
    ARRAY['deleted_at', 'is_read', 'read_at'],
    'notification: authenticated can update exactly the three state columns'
)
FROM rls_services WHERE service = 'notification';

SET LOCAL ROLE anon;
SET LOCAL "request.jwt.claim.sub" = '';
SET LOCAL "request.jwt.claims" = '{"role":"anon"}';

SELECT ok(
    coalesce(row_security_active(to_regclass(format('public.%I', table_name))), false),
    format('%s: RLS applies to anon on %s', service, table_name)
)
FROM rls_targets
WHERE probe_anon
ORDER BY service, table_name;

SELECT throws_ok(
    format('SELECT 1 FROM public.%I LIMIT 1', table_name),
    '42501', NULL::text,
    format('%s: anon cannot SELECT from %s', service, table_name)
)
FROM rls_targets
WHERE probe_anon
ORDER BY service, table_name;

SELECT throws_ok(
    format('INSERT INTO public.%I DEFAULT VALUES', table_name),
    '42501', NULL::text,
    format('%s: anon cannot INSERT into %s', service, table_name)
)
FROM rls_targets
WHERE probe_anon
ORDER BY service, table_name;

RESET ROLE;

DO $$
DECLARE
    fixture_a text;
BEGIN
    SELECT user_a INTO fixture_a FROM rls_notification_fixture;
    IF FOUND THEN
        PERFORM set_config('request.jwt.claim.sub', fixture_a, true);
        PERFORM set_config('request.jwt.claims',
            jsonb_build_object('sub', fixture_a, 'role', 'authenticated')::text, true);
    END IF;
END
$$;

SET LOCAL ROLE authenticated;

SELECT ok(
    pg_temp.rls_rows_or_denied(statement) = ANY (expected),
    'notification: ' || description
)
FROM rls_notification_cases
ORDER BY position;

RESET ROLE;

SELECT * FROM finish();
ROLLBACK;
