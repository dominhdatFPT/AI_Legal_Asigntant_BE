"""Integration tests on a disposable LOCAL PostgreSQL database, never Supabase."""
import os
import importlib.util
import io
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip('psycopg')
from psycopg.conninfo import conninfo_to_dict

SCHEMA = Path(__file__).resolve().parents[1] / 'database' / '001_document_schema.sql'
HASH = 'a' * 64


@pytest.fixture(scope='session')
def database_url():
    url = os.getenv('DOCUMENT_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set DOCUMENT_TEST_DATABASE_URL to a disposable local document_schema_test DB')
    info = conninfo_to_dict(url)
    if info.get('host') not in {'127.0.0.1', 'localhost', '::1'} or info.get('dbname') != 'document_schema_test':
        pytest.fail('Schema tests only accept localhost / document_schema_test')
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SCHEMA.read_text(encoding='utf-8'))
        # Minimal Storage table solely to exercise PostgreSQL policy semantics.
        conn.execute("create schema storage; create table storage.objects (id uuid primary key default gen_random_uuid(), bucket_id text not null); "
                     "alter table storage.objects enable row level security; "
                     "grant usage on schema storage to anon, authenticated; "
                     "grant select, insert, update, delete on storage.objects to anon, authenticated")
        conn.execute((SCHEMA.parent / '002_document_storage_access.sql').read_text(encoding='utf-8'))
    return url


@pytest.fixture
def db(database_url):
    with psycopg.connect(database_url) as conn:
        yield conn
        conn.rollback()


def upload(db, *, owner=None, **overrides):
    values = dict(id=uuid4(), owner_user_id=owner or uuid4(),
                  purpose='supporting_document', document_type='identity_card',
                  original_filename='identity.pdf', media_type='application/pdf',
                  uploaded_size_bytes=100, uploaded_sha256=HASH, storage_key=str(uuid4()),
                  idempotency_key=uuid4(), request_hash=HASH)
    values.update(overrides)
    cols = ', '.join(values)
    placeholders = ', '.join(['%s'] * len(values))
    db.execute(f'insert into public.user_files ({cols}) values ({placeholders})', list(values.values()))
    job = uuid4()
    db.execute('insert into public.document_processing_jobs (id, document_id) values (%s, %s)',
               (job, values['id']))
    return values, job


def complete(db, file, job):
    db.execute("update public.user_files set file_status='available', size_bytes=100, sha256=%s where id=%s",
               (HASH, file['id']))
    result = uuid4()
    db.execute("insert into public.document_extraction_results (id, job_id, source_sha256, source_kind, engine_version) "
               "values (%s, %s, %s, 'pdf_text', 'test')", (result, job, HASH))
    db.execute("update public.document_processing_jobs set status='completed', completed_at=now() where id=%s", (job,))
    db.execute('set constraints all immediate')
    db.execute('set constraints all deferred')
    return result


def profile(db, file, result, *, owner=None):
    db.execute('insert into public.user_identity_profiles '
               '(owner_user_id, source_document_id, source_result_id, full_name, confirmed_at) '
               'values (%s, %s, %s, %s, now())',
               (owner or file['owner_user_id'], file['id'], result, 'Test Name'))


@pytest.mark.parametrize('override', [
    {'uploaded_size_bytes': 0}, {'uploaded_size_bytes': 9437185},
    {'uploaded_sha256': 'bad-hash'}, {'media_type': 'text/html'},
    {'purpose': 'contract_template', 'document_type': 'other'},
    {'purpose': 'contract_template', 'document_type': 'contract', 'media_type': 'image/png'},
    {'media_type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
    {'file_status': 'available'}, {'file_status': 'rejected', 'error_code': ''},
])
def test_invalid_upload_is_rejected(db, override):
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        upload(db, **override)


def test_idempotency_is_scoped_to_owner_and_retained_after_delete(db):
    file, job = upload(db)
    complete(db, file, job)
    db.execute('update public.user_files set deleted_at=now() where id=%s', (file['id'],))
    with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
        upload(db, owner=file['owner_user_id'], idempotency_key=file['idempotency_key'])
    upload(db, idempotency_key=file['idempotency_key'])


def test_one_active_job_and_terminal_error_invariants(db):
    file, job = upload(db)
    with pytest.raises(psycopg.errors.UniqueViolation), db.transaction():
        db.execute('insert into public.document_processing_jobs (document_id, attempt) values (%s, 2)', (file['id'],))
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute("update public.document_processing_jobs set status='failed', completed_at=now() where id=%s", (job,))
    db.execute("update public.document_processing_jobs set status='failed', error_code='OCR_FAILED', completed_at=now() where id=%s", (job,))
    db.execute('set constraints all immediate')


def test_completed_requires_result_in_same_transaction(db):
    file, job = upload(db)
    with pytest.raises(psycopg.errors.CheckViolation, match='DOCUMENT_JOB_RESULT_MISMATCH'), db.transaction():
        db.execute("update public.document_processing_jobs set status='completed', completed_at=now() where id=%s", (job,))
        db.execute('set constraints all immediate')
    complete(db, file, job)


def test_result_requires_completed_job_and_matching_hash(db):
    file, job = upload(db)
    db.execute("update public.user_files set file_status='available', size_bytes=100, sha256=%s where id=%s", (HASH, file['id']))
    with pytest.raises(psycopg.errors.CheckViolation, match='DOCUMENT_RESULT_SOURCE_INVALID'), db.transaction():
        db.execute("insert into public.document_extraction_results (job_id, source_sha256, source_kind, engine_version) "
                   "values (%s, %s, 'pdf_text', 'test')", (job, 'b' * 64))
    with pytest.raises(psycopg.errors.CheckViolation, match='DOCUMENT_JOB_RESULT_MISMATCH'), db.transaction():
        db.execute("insert into public.document_extraction_results (job_id, source_sha256, source_kind, engine_version) "
                   "values (%s, %s, 'pdf_text', 'test')", (job, HASH))
        db.execute('set constraints all immediate')


def test_file_and_result_are_immutable_after_completion(db):
    file, job = upload(db)
    result = complete(db, file, job)
    with pytest.raises(psycopg.errors.CheckViolation, match='DOCUMENT_CONTENT_IMMUTABLE'), db.transaction():
        db.execute('update public.user_files set sha256=%s where id=%s', ('b' * 64, file['id']))
    with pytest.raises(psycopg.errors.CheckViolation, match='DOCUMENT_RESULT_IMMUTABLE'), db.transaction():
        db.execute("update public.document_extraction_results set text='changed' where id=%s", (result,))


def test_delete_blocks_active_job_and_active_profile(db):
    file, job = upload(db)
    with pytest.raises(psycopg.errors.CheckViolation, match='DOCUMENT_PROCESSING_ACTIVE'), db.transaction():
        db.execute('update public.user_files set deleted_at=now() where id=%s', (file['id'],))
    result = complete(db, file, job)
    profile(db, file, result)
    with pytest.raises(psycopg.errors.CheckViolation, match='PROFILE_SOURCE_IN_USE'), db.transaction():
        db.execute('update public.user_files set deleted_at=now() where id=%s', (file['id'],))
    db.execute("update public.user_identity_profiles set status='deleted', deleted_at=now(), revision=2 where owner_user_id=%s", (file['owner_user_id'],))
    db.execute('update public.user_files set deleted_at=now() where id=%s', (file['id'],))


def test_profile_rejects_other_owner_wrong_result_and_stale_revision(db):
    file, job = upload(db)
    result = complete(db, file, job)
    other, other_job = upload(db, owner=file['owner_user_id'])
    other_result = complete(db, other, other_job)
    with pytest.raises(psycopg.errors.CheckViolation, match='IDENTITY_PROFILE_SOURCE_INVALID'), db.transaction():
        profile(db, file, result, owner=uuid4())
    with pytest.raises(psycopg.errors.CheckViolation, match='IDENTITY_PROFILE_RESULT_INVALID'), db.transaction():
        profile(db, file, other_result)
    profile(db, file, result)
    with pytest.raises(psycopg.errors.CheckViolation, match='IDENTITY_PROFILE_REVISION_INVALID'), db.transaction():
        db.execute("update public.user_identity_profiles set full_name='changed', revision=1 where owner_user_id=%s", (file['owner_user_id'],))
    with pytest.raises(psycopg.errors.CheckViolation, match='PROFILE_SOURCE_IN_USE'), db.transaction():
        db.execute('insert into public.document_processing_jobs (document_id, attempt) values (%s, 2)', (file['id'],))


def test_profile_reactivate_keeps_revision_and_leading_zero(db):
    file, job = upload(db)
    result = complete(db, file, job)
    profile(db, file, result)
    db.execute("update public.user_identity_profiles set status='deleted', deleted_at=now(), revision=2 where owner_user_id=%s", (file['owner_user_id'],))
    db.execute("update public.user_identity_profiles set status='active', deleted_at=null, identity_number='000000000001', revision=3 where owner_user_id=%s", (file['owner_user_id'],))
    assert db.execute('select revision, identity_number from public.user_identity_profiles where owner_user_id=%s', (file['owner_user_id'],)).fetchone() == (3, '000000000001')


@pytest.mark.parametrize('role', ['anon', 'authenticated'])
def test_browser_roles_have_no_direct_table_access(db, role):
    assert db.execute("select count(*) from pg_class where relnamespace='public'::regnamespace and relkind='r' and relrowsecurity").fetchone()[0] == 4
    db.execute(f'set local role {role}')
    for table in ('user_files', 'document_processing_jobs', 'document_extraction_results', 'user_identity_profiles'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
            db.execute(f'select * from public.{table}')


@pytest.mark.parametrize('role', ['anon', 'authenticated'])
def test_private_bucket_stays_blocked_under_broad_existing_policy(db, role):
    db.execute('create policy existing_broad_policy on storage.objects for all to public using (true) with check (true)')
    db.execute("insert into storage.objects (bucket_id) values ('document-files'), ('another-service')")
    db.execute(f'set local role {role}')
    assert db.execute('select bucket_id from storage.objects').fetchall() == [('another-service',)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
        db.execute("insert into storage.objects (bucket_id) values ('document-files')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
        db.execute("update storage.objects set bucket_id='document-files' where bucket_id='another-service'")
    db.execute("insert into storage.objects (bucket_id) values ('another-service')")


@pytest.fixture(scope='session')
def supabase_setup():
    spec = importlib.util.spec_from_file_location('document_supabase_setup',
                                                 SCHEMA.parents[1] / 'scripts' / 'setup_supabase.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_rejects_wrong_project_before_connecting(monkeypatch, supabase_setup):
    monkeypatch.setenv('DOCUMENT_DATABASE_URL', 'postgresql://postgres:fake@db.otherproject.supabase.co:5432/postgres?sslmode=require')
    with pytest.raises(ValueError, match='does not match'):
        supabase_setup.database_url('srrxrnohunfyyolbfsew')


@pytest.mark.parametrize('url', [
    'postgresql://postgres:fake@db.srrxrnohunfyyolbfsew.supabase.co:5432/postgres?sslmode=require',
    'postgresql+psycopg://postgres.srrxrnohunfyyolbfsew:fake@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require',
])
def test_setup_accepts_project_matching_direct_and_session_pooler(monkeypatch, supabase_setup, url):
    monkeypatch.setenv('DOCUMENT_DATABASE_URL', url)
    assert supabase_setup.database_url('srrxrnohunfyyolbfsew').startswith('postgresql://')


def test_setup_pooler_reuses_password_and_keeps_project_and_tls(monkeypatch, supabase_setup):
    monkeypatch.setenv('DOCUMENT_DATABASE_URL', 'postgresql://postgres:fake%40test@db.srrxrnohunfyyolbfsew.supabase.co:5432/postgres')
    info = conninfo_to_dict(supabase_setup.database_url('srrxrnohunfyyolbfsew', 'aws-0-ap-northeast-1.pooler.supabase.com'))
    assert info['password'] == 'fake@test'
    assert info['host'] == 'aws-0-ap-northeast-1.pooler.supabase.com'
    assert info['user'] == 'postgres.srrxrnohunfyyolbfsew'
    assert info['port'] == '5432' and info['sslmode'] == 'require'
    with pytest.raises(ValueError, match='Session pooler host'):
        supabase_setup.database_url('srrxrnohunfyyolbfsew', 'example.com')


def test_setup_requires_tls_and_project_specific_storage(monkeypatch, supabase_setup):
    monkeypatch.setenv('DOCUMENT_DATABASE_URL', 'postgresql://postgres:fake@db.srrxrnohunfyyolbfsew.supabase.co:5432/postgres')
    assert conninfo_to_dict(supabase_setup.database_url('srrxrnohunfyyolbfsew'))['sslmode'] == 'require'
    monkeypatch.setenv('DOCUMENT_DATABASE_URL', 'postgresql://postgres:fake@db.srrxrnohunfyyolbfsew.supabase.co:5432/postgres?sslmode=disable')
    with pytest.raises(ValueError, match='sslmode'):
        supabase_setup.database_url('srrxrnohunfyyolbfsew')
    monkeypatch.setenv('DOCUMENT_SUPABASE_URL', 'https://otherproject.supabase.co')
    monkeypatch.setenv('DOCUMENT_SUPABASE_SERVICE_ROLE_KEY', 'fake-test-only')
    with pytest.raises(ValueError, match='DOCUMENT_SUPABASE_URL'):
        supabase_setup.storage_config('srrxrnohunfyyolbfsew')
    monkeypatch.setenv('DOCUMENT_SUPABASE_URL', 'https://srrxrnohunfyyolbfsew.supabase.co')
    monkeypatch.setenv('DOCUMENT_STORAGE_BUCKET', 'another-bucket')
    with pytest.raises(ValueError, match='DOCUMENT_STORAGE_BUCKET'):
        supabase_setup.storage_config('srrxrnohunfyyolbfsew')


def test_setup_verifies_applied_schema(db, supabase_setup):
    supabase_setup.verify(db)


@pytest.mark.parametrize('key, uses_bearer', [('sb_secret_fake-test-only', False), ('eyJfake-test-only', True)])
def test_storage_request_supports_new_secret_and_legacy_key(monkeypatch, supabase_setup, key, uses_bearer):
    seen = {}

    def fake_urlopen(request, timeout):
        seen['request'] = request
        return io.StringIO('{}')

    monkeypatch.setattr(supabase_setup, 'urlopen', fake_urlopen)
    supabase_setup.storage_request('https://srrxrnohunfyyolbfsew.supabase.co', key, 'GET', '/bucket')
    assert seen['request'].get_header('Apikey') == key
    assert bool(seen['request'].get_header('Authorization')) is uses_bearer
