"""User contract provided by teammate; synthetic HTTP only, no cloud or live account."""
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from test_document_foundation import foundation, config_env
from test_document_upload import upload_database, docx
from test_document_worker import runtime


def envelope(can=True, reason=None, message=None):
    return {'data': {'can_create_dossier': can, 'block_reason': reason, 'block_message': message},
            'error': None, 'request_id': 'synthetic-user-status'}


def client_adapter(foundation, handler):
    from app.services.user_status import UserStatus
    return UserStatus('http://user-service:8000', 'synthetic-internal-token', transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('body,status', [
    (envelope(), None),
    (envelope(False, 'PROFILE_NOT_SUBMITTED', 'Chưa nộp hồ sơ.'), None),
    (envelope(False, 'ACCOUNT_LOCKED', 'Tài khoản bị khóa.'), 403),
    (envelope(True, 'ACCOUNT_LOCKED', 'Tài khoản bị khóa.'), 403),
    (envelope(False, None), 503),
    (envelope(False, 'UNKNOWN_BLOCK'), 503),
    (envelope(True, 'PROFILE_NOT_SUBMITTED'), 503),
    (envelope('true'), 503),
    (envelope(1), 503),
    (envelope(False, 'ACCOUNT_LOCKED', ''), 503),
    (envelope(False, 'ACCOUNT_LOCKED', 'x' * 2001), 503),
    (envelope(False, 'ACCOUNT_LOCKED', 'bad\x00message'), 503),
    ({'data': {}, 'error': None, 'request_id': 'synthetic'}, 503),
    ({'data': envelope()['data'], 'error': {'code': 'PRIVATE_FAILURE'}, 'request_id': 'synthetic'}, 503),
    ({'data': envelope()['data'], 'error': None}, 503),
    ({'data': envelope()['data'], 'request_id': 'synthetic'}, 503),
])
def test_status_policy_and_upstream_header(foundation, body, status):
    owner = str(uuid4()); calls = []
    def handler(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.url == 'http://user-service:8000/internal/v1/users/' + owner + '/status'
        assert request.headers['X-Internal-Token'] == 'synthetic-internal-token'
        assert request.headers['X-Request-ID'] == 'document-test'
        assert 'Authorization' not in request.headers and 'Cookie' not in request.headers
        return httpx.Response(200, json=body)
    adapter = client_adapter(foundation, handler)
    if status:
        with pytest.raises(foundation[3]) as exc: adapter.ensure_document_write(owner, 'document-test')
        assert exc.value.status == status
        assert exc.value.code == ('ACCOUNT_LOCKED' if status == 403 else 'USER_SERVICE_UNAVAILABLE')
        if status == 403: assert exc.value.message == body['data']['block_message']
        else: assert 'synthetic-internal-token' not in exc.value.message
    else:
        adapter.ensure_document_write(owner, 'document-test')
    assert len(calls) == 1


@pytest.mark.parametrize('mode', ['timeout','network','401','403','404','429','500','302','large','invalid_json','duplicate','deep'])
def test_upstream_unavailable_never_allows_or_follows_redirect(foundation, mode):
    calls = []
    def handler(request):
        calls.append(request)
        if mode == 'timeout': raise httpx.ReadTimeout('private upstream diagnostic')
        if mode == 'network': raise httpx.ConnectError('private upstream diagnostic')
        if mode.isdigit(): return httpx.Response(int(mode), headers={'Location': 'https://outside.invalid'}, content=b'private')
        if mode == 'large': return httpx.Response(200, content=b'x' * 16385)
        if mode == 'invalid_json': return httpx.Response(200, content=b'private upstream diagnostic')
        if mode == 'deep': return httpx.Response(200, content=b'[' * 2000 + b']' * 2000)
        return httpx.Response(200, content=b'{"data":{"can_create_dossier":false,"can_create_dossier":true},"error":null,"request_id":"test"}')
    with pytest.raises(foundation[3]) as exc:
        client_adapter(foundation, handler).ensure_document_write(str(uuid4()), 'test')
    assert exc.value.status == 503 and exc.value.code == 'USER_SERVICE_UNAVAILABLE'
    assert 'private' not in exc.value.message and len(calls) == 1


@pytest.mark.parametrize('body,status,code', [
    (b'{"data":null,"error":{"code":"USER_NOT_FOUND","message":"x"},"request_id":"r"}', 401, 'UNAUTHORIZED'),
    (b'{"detail":"Not Found"}', 503, 'USER_SERVICE_UNAVAILABLE'),
    (b'private', 503, 'USER_SERVICE_UNAVAILABLE'),
    (b'{"error":{"code":"USER_NOT_FOUND"},"error":null}', 503, 'USER_SERVICE_UNAVAILABLE'),
])
def test_deleted_user_is_401_but_other_404_stays_unavailable(foundation, body, status, code):
    adapter = client_adapter(foundation, lambda request: httpx.Response(404, content=body))
    with pytest.raises(foundation[3]) as exc:
        adapter.ensure_document_write(str(uuid4()), 'test')
    assert exc.value.status == status and exc.value.code == code


@pytest.mark.parametrize('base,token', [('', 'secret'), ('http://outside.invalid', 'secret'),
    ('https://user:pass@outside.invalid', 'secret'), ('http://user-service:8000/api/v1', 'secret'),
    ('http://user-service:8000?token=secret', 'secret'), ('http://user-service:8000', ''),
    ('http://user-service:8000', 'bad\nsecret')])
def test_config_is_fail_closed_sanitized(foundation, base, token):
    from app.services.user_status import UserStatus
    with pytest.raises(foundation[3]) as exc: UserStatus(base, token)
    assert exc.value.status == 503 and 'secret' not in exc.value.message


@pytest.mark.parametrize('path,method', [('/api/v1/documents/','post'),
    ('/api/v1/documents/identity-ocr','post'),('/api/v1/documents/identity-profile/me','put')])
@pytest.mark.parametrize('reason,expected', [('ACCOUNT_LOCKED',403),('UPSTREAM_FAILURE',503)])
def test_mutations_block_before_storage_ocr_profile(foundation, service_auth, path, method, reason, expected):
    from app.core.security import get_user_status
    from app.api.files import get_files
    from app.api.identity_ocr import get_identity_ocr
    from app.api.identity_profile import get_identity_profile
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == '/internal/v1/users/11111111-1111-4111-8111-111111111111/status'
        if reason == 'UPSTREAM_FAILURE': return httpx.Response(503)
        return httpx.Response(200, json=envelope(False, reason, 'Tài khoản đã bị khóa.'))
    status = client_adapter(foundation, handler)
    foundation[0].dependency_overrides[get_user_status] = lambda: status
    class NoBusinessSideEffects:
        def __getattr__(self, name): raise AssertionError('Guard must block business method')
    for dependency in (get_files,get_identity_ocr,get_identity_profile):
        foundation[0].dependency_overrides[dependency] = NoBusinessSideEffects
    response = getattr(TestClient(foundation[0]), method)(path,
        headers={'Authorization':'Bearer ' + service_auth(), 'X-User-Id': str(uuid4()), 'X-Request-ID':'doc-gate-test'},
        json={'owner_user_id': str(uuid4())})
    assert response.status_code == expected and len(calls) == 1
    assert response.json()['error']['code'] == (reason if expected == 403 else 'USER_SERVICE_UNAVAILABLE')
    assert response.headers['Cache-Control'] == 'no-store'


def test_profile_not_submitted_can_upload_real_sql_and_same_key_rechecks_status(runtime, foundation, service_auth):
    from app.api.files import get_files
    from app.core.security import get_user_status
    worker, files, processing, *_ = runtime
    calls = []; locked = [False]
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=envelope(False,
            'ACCOUNT_LOCKED' if locked[0] else 'PROFILE_NOT_SUBMITTED', 'Thông báo cho user.'))
    status = client_adapter(foundation, handler)
    foundation[0].dependency_overrides[get_user_status] = lambda: status
    foundation[0].dependency_overrides[get_files] = lambda: files
    client = TestClient(foundation[0]); token = service_auth(); key = str(uuid4())
    args = {'headers':{'Authorization':'Bearer '+token,'Idempotency-Key':key},
            'files':{'file':('test.docx',docx(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')},
            'data':{'purpose':'supporting_document','document_type':'other'}}
    first = client.post('/api/v1/documents/', **args)
    assert first.status_code == 202
    locked[0] = True
    replay = client.post('/api/v1/documents/', **args)
    assert replay.status_code == 403 and len(calls) == 2
    # Finish just the synthetic fixture job so no test leaves unrelated queue work.
    worker.run_once(first.json()['data']['document_id'])


def test_auth_and_csrf_fail_before_upstream(foundation, service_auth):
    from app.core.security import get_user_status
    class MustNotCall:
        def ensure_document_write(self, *args): raise AssertionError('Auth/CSRF before User gate')
    foundation[0].dependency_overrides[get_user_status] = MustNotCall
    client = TestClient(foundation[0])
    assert client.post('/api/v1/documents/identity-ocr').status_code == 401
    client.cookies.set('access_token',service_auth())
    assert client.post('/api/v1/documents/identity-ocr').status_code == 403


@pytest.mark.parametrize('operation', ['ocr','profile'])
def test_unsubmitted_profile_does_not_deadlock_ocr_or_confirmation(foundation, service_auth, operation):
    from app.core.security import get_user_status
    from app.api.identity_ocr import get_identity_ocr
    from app.api.identity_profile import get_identity_profile
    from test_document_identity_images import picture
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=envelope(False, 'PROFILE_NOT_SUBMITTED', 'Chưa nộp hồ sơ.'))
    status = client_adapter(foundation, handler)
    foundation[0].dependency_overrides[get_user_status] = lambda: status
    class FakeOcr:
        def create(self, owner, key, images):
            assert owner == '11111111-1111-4111-8111-111111111111' and len(images) == 3
            assert images[0].side == 'front' and images[1].side == 'back' and len(images[2]) == 64
            return {'session_id': str(uuid4()), 'status':'completed'}
    class FakeProfile:
        def confirm(self, owner, body):
            assert owner == '11111111-1111-4111-8111-111111111111'
            assert body.expected_revision == 0
            return {'revision':1, 'fields':body.fields.model_dump(mode='json')}
    foundation[0].dependency_overrides[get_identity_ocr] = FakeOcr
    foundation[0].dependency_overrides[get_identity_profile] = FakeProfile
    client = TestClient(foundation[0]); headers = {'Authorization':'Bearer '+service_auth()}
    if operation == 'ocr':
        headers['Idempotency-Key'] = str(uuid4())
        response = client.post('/api/v1/documents/identity-ocr', headers=headers,
            files={'front':('front.png',picture('red'),'image/png'),'back':('back.png',picture('blue'),'image/png')})
        assert response.status_code == 201
    else:
        response = client.put('/api/v1/documents/identity-profile/me', headers=headers,
            json={'source_session_id':str(uuid4()),'expected_revision':0,'fields':{
                'full_name':'TEST ONLY','identity_number':None,'date_of_birth':None,
                'issue_date':None,'issue_place':None,'permanent_address':None}})
        assert response.status_code == 200
    assert len(calls) == 1


def test_read_endpoints_do_not_require_user_status(foundation, service_auth):
    from app.core.security import get_user_status
    class MustNotCall:
        def ensure_document_write(self, *args): raise AssertionError('Reads should remain owner-only without User call')
    foundation[0].dependency_overrides[get_user_status] = MustNotCall
    client = TestClient(foundation[0])
    assert client.get('/api/v1/documents/health').status_code == 200
    assert client.get('/api/v1/documents/me',headers={'Authorization':'Bearer '+service_auth()}).status_code == 200


def test_compose_passes_internal_config_only_to_api():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[1]
    base = yaml.safe_load((root/'docker-compose.yml').read_text(encoding='utf-8'))
    environment = base['services']['document-service']['environment']
    assert environment['USER_SERVICE_URL'] == '${USER_SERVICE_URL:-http://user-service:8000}'
    assert environment['INTERNAL_TOKEN'] == '${INTERNAL_TOKEN:-}'
    worker = yaml.safe_load((root/'docker-compose.document-worker.yml').read_text(encoding='utf-8'))
    assert 'INTERNAL_TOKEN' not in worker['services']['document-worker']['environment']
