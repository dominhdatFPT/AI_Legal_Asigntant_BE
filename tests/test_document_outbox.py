"""T7 real disposable SQL and synthetic files only (outbox trigger/schema; delivery moved to RabbitMQ)."""
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest

from test_document_foundation import foundation, config_env
from test_document_upload import upload_database, SERVICE, docx
from test_document_worker import MemoryStorage, Scanner, Processor


@pytest.fixture
def outbox(foundation, config_env, upload_database):
    from app.worker import Worker
    from app.repositories.files import Files
    from app.services.file_validation import inspect_file
    database = foundation[5](replace(foundation[1].from_env(), database_url=upload_database))
    with database.connection() as conn:
        if not conn.execute("SELECT to_regclass('public.document_notification_outbox') AS name").fetchone()['name']:
            # Terminal history predates migration005: do not replay old popups.
            from app.repositories.files import Files
            from app.services.file_validation import inspect_file
            legacy = Files(database, MemoryStorage()).upload(str(uuid4()), uuid4(), inspect_file('history.docx',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document', docx(), 'contract_template', 'contract'))
            # Upload commits on a separate connection; reset this transaction's
            # now() before completed_at, otherwise it predates job.created_at.
            conn.commit()
            conn.execute("UPDATE document_processing_jobs SET status='failed',completed_at=now(),error_code='TEST_HISTORY' WHERE id=%s", (legacy['processing_job_id'],))
            conn.commit()
            conn.execute((SERVICE / 'database/005_document_outbox.sql').read_text(encoding='utf-8'))
            assert conn.execute('SELECT count(*) AS n FROM document_notification_outbox').fetchone()['n'] == 0
    storage, scanner, processor = MemoryStorage(), Scanner(), Processor()
    worker = Worker(database, storage, scanner, processor)
    files = Files(database, storage)
    owner = str(uuid4())
    file = files.upload(owner, uuid4(), inspect_file('test.docx',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document', docx(), 'contract_template', 'contract'))
    def row():
        with database.connection(read_only=True) as conn:
            return conn.execute('SELECT * FROM document_notification_outbox WHERE job_id=%s', (file['processing_job_id'],)).fetchone()
    return database, worker, file, owner, row, scanner


def test_atomic_success_event_and_immutable_body(outbox):
    db, worker, file, owner, row, _ = outbox
    assert row() is None
    assert worker.run_once(file['document_id'])
    r = row()
    assert str(r['recipient_user_id']) == owner
    assert str(r['document_id']) == file['document_id'] and r['event_type'] == 'document.ocr_completed'
    assert not worker.run_once(file['document_id']) and row()['event_id'] == r['event_id']
    with pytest.raises(Exception):
        with db.connection() as conn:
            conn.execute('UPDATE document_notification_outbox SET recipient_user_id=%s WHERE event_id=%s', (uuid4(), r['event_id']))
    assert row()['recipient_user_id'] == r['recipient_user_id']


def test_failure_final_only_and_retry_no_event(outbox):
    db, worker, file, owner, row, scanner = outbox
    scanner.code = 'SCAN_UNAVAILABLE'
    assert worker.run_once(file['document_id']) and row() is None
    with db.connection() as conn:
        conn.execute('UPDATE document_processing_jobs SET retry_count=0 WHERE id=%s', (file['processing_job_id'],))
    scanner.code = 'MALWARE_DETECTED'
    assert worker.run_once(file['document_id'])
    assert row()['event_type'] == 'document.ocr_failed'
    with db.connection(read_only=True) as conn:
        assert conn.execute('SELECT file_status FROM user_files WHERE id=%s', (file['document_id'],)).fetchone()['file_status'] == 'rejected'


def test_terminal_rollback_has_no_event(outbox):
    db, _, file, _, row, _ = outbox
    with pytest.raises(RuntimeError):
        with db.connection() as conn:
            conn.execute("UPDATE document_processing_jobs SET status='failed',completed_at=now(),error_code='TEST_FAILURE' WHERE id=%s", (file['processing_job_id'],))
            assert conn.execute('SELECT 1 FROM document_notification_outbox WHERE job_id=%s', (file['processing_job_id'],)).fetchone()
            raise RuntimeError('rollback')
    assert row() is None


def test_schema_privileges(outbox):
    db, *_ = outbox
    import importlib.util
    spec = importlib.util.spec_from_file_location('outbox_setup', SERVICE / 'scripts/setup_document_outbox.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with db.connection(read_only=True) as conn:
        module.verify(conn)


def test_backend_role_enqueues_but_cannot_insert_or_delete(outbox):
    db, _, file, owner, row, _ = outbox
    with db.connection() as conn:
        conn.execute('SET LOCAL ROLE service_role')
        conn.execute("UPDATE document_processing_jobs SET status='failed',completed_at=now(),error_code='TEST_BACKEND' WHERE id=%s", (file['processing_job_id'],))
    assert str(row()['recipient_user_id']) == owner
    with pytest.raises(Exception):
        with db.connection() as conn:
            conn.execute('SET LOCAL ROLE service_role')
            conn.execute('DELETE FROM document_notification_outbox WHERE event_id=%s', (row()['event_id'],))
