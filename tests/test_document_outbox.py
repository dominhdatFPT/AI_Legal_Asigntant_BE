"""T7 real disposable SQL, synthetic files and mock Notification transport only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import json
import threading
from uuid import uuid4

import httpx
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
    from app.notification_worker import event_body
    body = event_body(r)
    assert body['recipient_user_ids'] == [owner]
    assert body['resource_id'] == file['document_id']
    assert body['event_type'] == 'document.ocr_completed' and body['payload'] == {}
    assert not {'text', 'filename', 'identity_number', 'token'} & body.keys()
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


def isolate_event(db, event):
    with db.connection() as conn:
        conn.execute("UPDATE document_notification_outbox SET status='sent',sent_at=now(),attempts=attempts+1 WHERE status='pending' AND event_id<>%s", (event,))


def test_dispatcher_exhaustion_is_bounded(outbox):
    db, worker, file, _, row, _ = outbox
    worker.run_once(file['document_id'])
    from app.notification_worker import Dispatcher, event_body
    from app.services.notification_delivery import DeliveryError
    before = row(); isolate_event(db, before['event_id'])
    class Delivery:
        def send(self, event):
            assert event == event_body(before)
            raise DeliveryError('NOTIFICATION_UNAVAILABLE', True)
    dispatcher = Dispatcher(db, Delivery())
    for _ in range(12):
        with db.connection() as conn:
            conn.execute('ALTER TABLE document_notification_outbox DISABLE TRIGGER document_guard_outbox')
            conn.execute('UPDATE document_notification_outbox SET next_attempt_at=now() WHERE event_id=%s', (before['event_id'],))
            conn.execute('ALTER TABLE document_notification_outbox ENABLE TRIGGER document_guard_outbox')
        assert dispatcher.run_once()
    assert row()['status'] == 'failed' and row()['attempts'] == 12
    assert not dispatcher.run_once()


def test_dispatcher_crash_after_remote_commit_replays_same_event(outbox):
    from contextlib import contextmanager
    db, worker, file, _, row, _ = outbox
    worker.run_once(file['document_id'])
    from app.notification_worker import Dispatcher
    before = row(); isolate_event(db, before['event_id'])
    sent = []
    class Delivery:
        def send(self, event): sent.append(event)
    class CrashDatabase:
        @contextmanager
        def connection(self):
            with db.connection() as conn:
                class Proxy:
                    def execute(self, query, params=()):
                        if query.startswith('UPDATE document_notification_outbox SET status='):
                            raise RuntimeError('synthetic failure after receiver commit')
                        return conn.execute(query, params)
                    def __getattr__(self, key): return getattr(conn, key)
                yield Proxy()
    with pytest.raises(RuntimeError): Dispatcher(CrashDatabase(), Delivery()).run_once()
    assert row()['status'] == 'pending' and row()['attempts'] == 0
    assert Dispatcher(db, Delivery()).run_once()
    assert len(sent) == 2 and sent[0] == sent[1] and row()['status'] == 'sent'


def test_dispatcher_success_duplicate_and_concurrent_lock(outbox):
    db, worker, file, _, row, _ = outbox
    worker.run_once(file['document_id'])
    from app.notification_worker import Dispatcher
    started, release = threading.Event(), threading.Event()
    calls = []
    class Delivery:
        def send(self, event):
            # Scope this fixture without mutating unrelated events.
            calls.append(event); started.set(); assert release.wait(10)
    # Other synthetic events may exist; mark eligible rows sent using valid transition.
    with db.connection() as conn:
        conn.execute("UPDATE document_notification_outbox SET status='sent',sent_at=now(),attempts=attempts+1 WHERE status='pending' AND event_id<>%s", (row()['event_id'],))
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(Dispatcher(db, Delivery()).run_once)
        assert started.wait(10)
        assert not Dispatcher(db, Delivery()).run_once()
        release.set(); assert future.result()
    assert len(calls) == 1 and row()['status'] == 'sent'
    assert not Dispatcher(db, Delivery()).run_once()


@pytest.mark.parametrize('retryable,expected', [(True, 'pending'), (False, 'failed')])
def test_dispatcher_backoff_permanent_and_stable_id(outbox, retryable, expected):
    db, worker, file, _, row, _ = outbox
    worker.run_once(file['document_id'])
    from app.notification_worker import Dispatcher, event_body
    from app.services.notification_delivery import DeliveryError
    before = row(); body = event_body(before)
    with db.connection() as conn:
        conn.execute("UPDATE document_notification_outbox SET status='sent',sent_at=now(),attempts=attempts+1 WHERE status='pending' AND event_id<>%s", (before['event_id'],))
    class Delivery:
        def send(self, event):
            assert event == body
            raise DeliveryError('NOTIFICATION_TEST', retryable)
    assert Dispatcher(db, Delivery()).run_once()
    after = row()
    assert after['status'] == expected and after['attempts'] == 1
    assert after['next_attempt_at'] > before['next_attempt_at'] and event_body(after) == body
    assert not Dispatcher(db, Delivery()).run_once()


def fake_event():
    return {'event_id': str(uuid4()), 'event_type': 'document.ocr_completed', 'source_service': 'document-service',
        'occurred_at': datetime.now(timezone.utc).isoformat(), 'recipient_user_ids': [str(uuid4())],
        'resource_type': 'document', 'resource_id': str(uuid4()), 'payload': {}}


@pytest.mark.parametrize('status', [400, 401, 403, 404, 409, 422, 302])
def test_transport_permanent_no_retry(foundation, status):
    from app.services.notification_delivery import NotificationDelivery, DeliveryError
    calls = []
    def handler(request):
        calls.append(request); return httpx.Response(status, headers={'Location': 'https://outside.invalid'})
    d = NotificationDelivery('http://notification-service:8000', 'synthetic-token', transport=httpx.MockTransport(handler), wait=lambda _: None)
    with pytest.raises(DeliveryError) as exc:
        d.send(fake_event())
    assert not exc.value.retryable and len(calls) == 1


@pytest.mark.parametrize('mode', ['success', 'duplicate', 'timeout_then_success', 'retry', 'bad', 'large', 'wrong_count'])
def test_transport_validates_and_preserves_event(foundation, mode):
    from app.services.notification_delivery import NotificationDelivery, DeliveryError
    event = fake_event(); calls = []; waits = []
    def handler(request):
        calls.append(json.loads(request.content))
        assert request.headers['X-Internal-Token'] == 'synthetic-token'
        assert request.headers['X-Request-ID'] == 'doc-event-' + event['event_id']
        if mode == 'timeout_then_success' and len(calls) == 1:
            raise httpx.ReadTimeout('synthetic')
        if mode == 'retry': return httpx.Response(503)
        if mode == 'bad': return httpx.Response(200, json={'data': {}})
        if mode == 'large': return httpx.Response(200, content=b'x' * 20000)
        return httpx.Response(200, json={'data': {'event_id': event['event_id'], 'is_duplicate': mode == 'duplicate',
            'created_count': 0 if mode == 'duplicate' else (2 if mode == 'wrong_count' else 1)}, 'error': None, 'request_id': 'synthetic'})
    d = NotificationDelivery('http://notification-service:8000', 'synthetic-token', transport=httpx.MockTransport(handler), wait=waits.append)
    if mode in {'retry', 'bad', 'large', 'wrong_count'}:
        with pytest.raises(DeliveryError) as exc: d.send(event)
        assert exc.value.retryable and len(calls) == 3 and waits == [0.5, 1]
    else:
        d.send(event)
        assert len(calls) == (2 if mode == 'timeout_then_success' else 1)
    assert all(value == event for value in calls)


@pytest.mark.parametrize('base', ['http://outside.invalid', 'https://user:pass@outside.invalid', 'https://outside.invalid/api', 'https://outside.invalid?key=x', 'not-a-url'])
def test_notification_config_no_secret_leak(foundation, base):
    from app.services.notification_delivery import NotificationDelivery, DeliveryError
    with pytest.raises(DeliveryError) as exc: NotificationDelivery(base, 'secret-never-echo')
    assert 'secret-never-echo' not in str(exc.value) and base not in str(exc.value)


def test_delivery_opt_in_and_worker_session_port(foundation, config_env, monkeypatch):
    from app.services.notification_delivery import NotificationDelivery, DeliveryError
    from app.core.config import require_session_database
    monkeypatch.delenv('DOCUMENT_NOTIFICATIONS_ENABLED', raising=False)
    with pytest.raises(DeliveryError): NotificationDelivery.from_env()
    require_session_database(foundation[1].from_env())
    with pytest.raises(foundation[2]):
        require_session_database(replace(foundation[1].from_env(), database_url='postgresql://postgres:test@localhost:6543/test'))
