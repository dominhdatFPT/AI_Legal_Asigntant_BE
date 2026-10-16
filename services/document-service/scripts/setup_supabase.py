"""Apply Document migrations to an explicitly selected Supabase project.

Secrets come from backend/.env (or process environment), never command arguments.
Without --apply, only inspect the target and report readiness.
"""
import argparse
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict, make_conninfo

SERVICE = Path(__file__).resolve().parents[1]
BACKEND = SERVICE.parents[1]
TABLES = ('user_files', 'document_processing_jobs', 'document_extraction_results', 'user_identity_profiles')
MIME_TYPES = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              'image/jpeg', 'image/png']
BUCKET = 'document-files'


def database_url(project_ref, pooler_host=None):
    value = os.getenv('DOCUMENT_DATABASE_URL', '')
    value = value.replace('postgresql+psycopg://', 'postgresql://', 1)
    if not value:
        raise ValueError('Missing DOCUMENT_DATABASE_URL in backend/.env')
    try:
        info = conninfo_to_dict(value)
    except psycopg.Error:
        raise ValueError('Invalid DOCUMENT_DATABASE_URL; copy the URI from Supabase Connect') from None
    host, user = info.get('host', ''), info.get('user', '')
    direct = host == f'db.{project_ref}.supabase.co'
    pooled = host.endswith('.pooler.supabase.com') and user == f'postgres.{project_ref}'
    if not (direct or pooled):
        raise ValueError('DOCUMENT_DATABASE_URL does not match --project-ref; nothing was changed')
    if info.get('sslmode') and info['sslmode'] not in {'require', 'verify-ca', 'verify-full'}:
        raise ValueError('DOCUMENT_DATABASE_URL must use sslmode=require or certificate verification')
    if pooler_host:
        if not re.fullmatch(r'[a-z0-9-]+\.pooler\.supabase\.com', pooler_host):
            raise ValueError('Use the exact Session pooler host copied from Supabase Connect')
        return make_conninfo(value, host=pooler_host, hostaddr='', port='5432',
                             user=f'postgres.{project_ref}', sslmode=info.get('sslmode', 'require'))
    if not info.get('sslmode'):
        return make_conninfo(value, sslmode='require')
    return value


def storage_config(project_ref):
    base = os.getenv('DOCUMENT_SUPABASE_URL', '').rstrip('/')
    key = os.getenv('DOCUMENT_SUPABASE_SERVICE_ROLE_KEY', '')
    if base != f'https://{project_ref}.supabase.co' or urlsplit(base).query or not key:
        raise ValueError('Set DOCUMENT_SUPABASE_URL for this project and DOCUMENT_SUPABASE_SERVICE_ROLE_KEY')
    if os.getenv('DOCUMENT_STORAGE_BUCKET', BUCKET) != BUCKET:
        raise ValueError('DOCUMENT_STORAGE_BUCKET must be document-files for the configured Storage policy')
    return base, key


def storage_request(base, key, method, path, body=None):
    headers = {'apikey': key, 'Content-Type': 'application/json'}
    # New sb_secret keys belong in apikey, not in a JWT Bearer header.
    # Keep compatibility with the existing service-role variable / legacy JWT.
    if not key.startswith('sb_secret_'):
        headers['Authorization'] = f'Bearer {key}'
    request = Request(base + '/storage/v1' + path,
                      data=None if body is None else json.dumps(body).encode(), method=method,
                      headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        if method == 'GET' and error.code == 404:
            return None
        raise RuntimeError(f'Storage returned HTTP {error.code}; check the backend key and bucket settings') from None
    except URLError:
        raise RuntimeError('Cannot connect to Supabase Storage') from None


def ensure_bucket(base, key, apply):
    buckets = storage_request(base, key, 'GET', '/bucket')
    if not isinstance(buckets, list):
        raise RuntimeError('Unexpected Storage bucket-list response')
    existing = next((bucket for bucket in buckets if bucket.get('id') == BUCKET), None)
    if existing is not None:
        if (existing.get('public') is not False or existing.get('file_size_limit') != 9437184
                or set(existing.get('allowed_mime_types') or []) != set(MIME_TYPES)):
            raise ValueError('Existing document-files bucket differs from the required private/size/MIME configuration')
        print('Private document-files bucket verified')
    elif apply:
        storage_request(base, key, 'POST', '/bucket',
                        {'id': BUCKET, 'name': BUCKET, 'public': False,
                         'file_size_limit': 9437184, 'allowed_mime_types': MIME_TYPES})
        ensure_bucket(base, key, False)
    else:
        print('document-files bucket is absent; --apply --create-bucket will create it')


def verify(conn):
    rows = conn.execute('select c.relname, c.relrowsecurity from pg_class c '
                        "join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' "
                        'and c.relname=any(%s)', (list(TABLES),)).fetchall()
    if len(rows) != 4 or not all(row[1] for row in rows):
        raise ValueError('Expected four Document tables with RLS enabled')
    expected_triggers = {'document_guard_file', 'document_guard_job', 'document_guard_result',
                         'document_job_result_consistency', 'document_result_job_consistency',
                         'document_guard_profile'}
    triggers = conn.execute("select tgname from pg_trigger where tgrelid=any(%s::regclass[]) "
                            'and not tgisinternal', (['public.' + table for table in TABLES],)).fetchall()
    if not expected_triggers.issubset({row[0] for row in triggers}):
        raise ValueError('Document integrity triggers missing; inspect existing schema')
    for role in ('anon', 'authenticated'):
        for table in TABLES:
            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                if conn.execute('select has_table_privilege(%s, %s, %s)', (role, 'public.' + table, privilege)).fetchone()[0]:
                    raise ValueError('Unexpected browser privileges on Document tables')
    policy = conn.execute("select permissive, roles::text, cmd, qual, with_check from pg_policies "
                          "where schemaname='storage' and tablename='objects' "
                          "and policyname='document_files_backend_only'").fetchone()
    if (not policy or policy[0] != 'RESTRICTIVE' or policy[2] != 'ALL'
            or 'anon' not in policy[1] or 'authenticated' not in policy[1]
            or "bucket_id <> 'document-files'" not in (policy[3] or '')
            or "bucket_id <> 'document-files'" not in (policy[4] or '')):
        raise ValueError('Storage restrictive policy missing or different; inspect check_document_db.sql')
    print('Four Document tables, RLS, browser privileges and Storage policy verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-ref', required=True)
    parser.add_argument('--env-file', type=Path, default=BACKEND / '.env')
    parser.add_argument('--pooler-host', help='Session pooler host from Connect; reuses the saved database password')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--create-bucket', action='store_true')
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    try:
        url = database_url(args.project_ref, args.pooler_host or os.getenv('DOCUMENT_DATABASE_POOLER_HOST'))
        storage = storage_config(args.project_ref) if args.create_bucket else None
        # Inspect the bucket BEFORE migrations; don't overwrite an existing bucket.
        if storage:
            ensure_bucket(*storage, False)
        with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
            existing = [table for table in TABLES if conn.execute(
                'select to_regclass(%s)', ('public.' + table,)).fetchone()[0]]
            if existing:
                verify(conn)
                print('Existing Document schema verified; migrations skipped')
            elif args.apply:
                for filename in ('001_document_schema.sql', '002_document_storage_access.sql'):
                    conn.execute((SERVICE / 'database' / filename).read_text(encoding='utf-8'))
                    print(filename + ' applied')
                verify(conn)
            else:
                print('Target matches project; Document tables are absent. No changes made')
        if storage and args.apply:
            ensure_bucket(*storage, True)
        print('Upload/OCR APIs are still planned; database setup does not implement them')
    except ValueError as error:
        parser.exit(1, str(error) + '\n')
    except psycopg.Error:
        # Do not emit connection strings or row contents (could contain PII).
        parser.exit(1, 'Database connection/migration failed. Check credentials, connectivity and existing objects.\n')
    except RuntimeError as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
