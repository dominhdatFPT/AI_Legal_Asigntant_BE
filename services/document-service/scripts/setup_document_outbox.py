"""Guarded additive migration005; default read-only, no historical event backfill."""
import argparse
from pathlib import Path
import sys

from dotenv import load_dotenv

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from app.core.config import Settings, require_session_database
from app.repositories.database import Database


def verify(conn):
    row = conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass('public.document_notification_outbox')").fetchone()
    if not row or not row['relrowsecurity']:
        raise ValueError('Missing outbox/RLS')
    constraints = conn.execute("SELECT conname,contype,confdeltype FROM pg_constraint WHERE conrelid='public.document_notification_outbox'::regclass").fetchall()
    names = {r['conname'] for r in constraints}
    required = {'document_notification_outbox_pkey', 'document_notification_outbox_job_id_key',
                'document_notification_outbox_job_id_fkey', 'document_notification_outbox_document_id_fkey'}
    if not required <= names or len([r for r in constraints if r['contype'] == 'c']) < 4:
        raise ValueError('Outbox constraints missing')
    if any(r['confdeltype'] != 'r' for r in constraints if r['contype'] == 'f'):
        raise ValueError('Outbox FK must restrict deletion')
    function = conn.execute("SELECT prosecdef,proconfig FROM pg_proc WHERE oid='document_private.enqueue_notification()'::regprocedure").fetchone()
    if not function['prosecdef'] or 'search_path=""' not in (function['proconfig'] or []):
        raise ValueError('Unsafe enqueue function')
    for role in ('anon', 'authenticated'):
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
            if conn.execute("SELECT has_table_privilege(%s,'public.document_notification_outbox',%s) AS allowed", (role, privilege)).fetchone()['allowed']:
                raise ValueError('Browser privilege')
        if conn.execute("SELECT has_function_privilege(%s,'document_private.enqueue_notification()','EXECUTE') AS allowed", (role,)).fetchone()['allowed']:
            raise ValueError('Browser function privilege')
    for privilege in ('SELECT', 'UPDATE'):
        if not conn.execute("SELECT has_table_privilege('service_role','public.document_notification_outbox',%s) AS allowed", (privilege,)).fetchone()['allowed']:
            raise ValueError('Backend privilege missing')
    for privilege in ('INSERT', 'DELETE'):
        if conn.execute("SELECT has_table_privilege('service_role','public.document_notification_outbox',%s) AS allowed", (privilege,)).fetchone()['allowed']:
            raise ValueError('Backend immutable record privilege')
    triggers = conn.execute("SELECT tgname FROM pg_trigger WHERE tgenabled='O' AND NOT tgisinternal AND "
        "tgrelid IN ('public.document_notification_outbox'::regclass,'public.document_processing_jobs'::regclass)").fetchall()
    if not {'document_enqueue_notification', 'document_guard_outbox'} <= {r['tgname'] for r in triggers}:
        raise ValueError('Missing triggers')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-ref', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_dotenv(SERVICE.parents[1] / '.env', override=False)
    try:
        settings = Settings.from_env()
        require_session_database(settings)
        if settings.supabase_url != f'https://{args.project_ref}.supabase.co':
            raise ValueError('Project mismatch')
        with Database(settings).connection(read_only=not args.apply) as conn:
            exists = conn.execute("SELECT to_regclass('public.document_notification_outbox') AS name").fetchone()['name']
            if not exists:
                if not args.apply:
                    raise ValueError('Migration005 absent')
                conn.execute((SERVICE / 'database/005_document_outbox.sql').read_text(encoding='utf-8'))
            verify(conn)
        print('PASS: outbox/RLS/immutable event and backend privileges; no historical events backfilled.')
        return 0
    except Exception:
        print('FAIL: outbox setup/config/schema; no credentials or rows printed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
