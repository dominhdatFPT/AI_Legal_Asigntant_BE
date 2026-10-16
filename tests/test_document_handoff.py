"""Contract-facing adapter against real Document HTTP/SQL, Storage synthetic."""
import importlib.util
import hashlib
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from test_document_outbox import outbox
from test_document_foundation import foundation, config_env
from test_document_upload import upload_database


def adapter():
    path = Path(__file__).resolve().parents[1] / 'contracts/examples/document_handoff.py'
    import sys
    spec = importlib.util.spec_from_file_location('document_handoff_test', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def wire(foundation, outbox):
    from app.api.files import get_files
    from app.api.processing import get_processing
    from app.repositories.files import Files
    from app.repositories.processing import Processing
    db, worker, *_ = outbox
    app = foundation[0]
    app.dependency_overrides[get_files] = lambda: Files(db, worker.storage)
    app.dependency_overrides[get_processing] = lambda: Processing(db)
    return app


def test_real_api_handoff_owner_hash_and_no_jwt_persistence(foundation, outbox, service_auth):
    _, worker, file, owner, *_ = outbox
    app = wire(foundation, outbox)
    worker.run_once(file['document_id'])
    m = adapter()
    token = service_auth(sub=owner)
    try:
        snapshot = m.read_document_snapshot('http://document-service:8000', file['document_id'], owner, token, client=TestClient(app), wait=lambda _: None)
        assert hashlib.sha256(snapshot.content).hexdigest() == snapshot.sha256
        assert snapshot.job_id == file['processing_job_id'] and snapshot.extraction['source_kind'] == 'docx'
        assert token not in repr(snapshot) and not hasattr(snapshot, 'access_token')
        assert 'storage_key' not in snapshot.metadata
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize('mode,expected', [('pending', 409), ('stranger', 404), ('deleted', 404), ('expired', 401)])
def test_handoff_refuses_unready_private_deleted(foundation, outbox, service_auth, mode, expected):
    _, worker, file, owner, *_ = outbox
    app = wire(foundation, outbox)
    if mode != 'pending': worker.run_once(file['document_id'])
    if mode == 'deleted':
        from app.repositories.files import Files
        Files(worker.database, worker.storage).delete(owner, file['document_id'])
    from datetime import datetime, timezone, timedelta
    token = service_auth(sub=str(uuid4()) if mode == 'stranger' else owner,
                         exp=datetime.now(timezone.utc) + timedelta(minutes=-1 if mode == 'expired' else 15))
    m = adapter()
    try:
        with pytest.raises(m.HandoffError) as exc:
            m.read_document_snapshot('http://document-service:8000', file['document_id'], owner, token, client=TestClient(app), wait=lambda _: None)
        assert exc.value.status == expected
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize('mode', ['hash', 'job', 'size', 'kind', 'redirect', 'malformed'])
def test_handoff_rejects_inconsistent_response(foundation, outbox, service_auth, mode):
    _, worker, file, owner, *_ = outbox
    app = wire(foundation, outbox); worker.run_once(file['document_id'])
    actual = TestClient(app); token = service_auth(sub=owner)
    def handler(request):
        if mode == 'redirect': return httpx.Response(302, headers={'Location': 'https://outside.invalid'})
        if mode == 'malformed': return httpx.Response(200, content=b'not-json')
        response = actual.get(request.url.path, headers={'Authorization': request.headers['Authorization']})
        if request.url.path.endswith('/content'):
            content = response.content + (b'x' if mode == 'size' else b'')
            return httpx.Response(response.status_code, content=content, headers=dict(response.headers))
        body = response.json()
        if request.url.path.endswith('/extraction'):
            if mode == 'hash': body['data']['source_sha256'] = '0' * 64
            if mode == 'job': body['data']['job_id'] = str(uuid4())
            if mode == 'kind': body['data']['source_kind'] = 'image'
        return httpx.Response(response.status_code, json=body)
    m = adapter()
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(m.HandoffError) as exc:
                m.read_document_snapshot('http://document-service:8000', file['document_id'], owner, token, client=client, wait=lambda _: None)
        assert exc.value.status == 502
    finally:
        app.dependency_overrides.clear()
