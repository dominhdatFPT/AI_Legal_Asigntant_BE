"""Document ghi dữ liệu chỉ dựa vào JWT + revocation cache của common_auth, không gọi User Service."""
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from test_document_foundation import foundation, config_env

OWNER = "11111111-1111-4111-8111-111111111111"
WRITERS = [("post", "/api/v1/documents/"), ("post", "/api/v1/documents/identity-ocr"),
           ("put", "/api/v1/documents/identity-profile/me")]


@pytest.fixture(autouse=True)
def no_user_service_http(monkeypatch):
    """Mọi request HTTP thật đều raise và được ghi lại: test chứng minh không có request nào ra ngoài."""
    calls = []

    def blocked(self, request, *args, **kwargs):
        calls.append(str(request.url))
        raise AssertionError("Không được gọi HTTP tới user-service: " + str(request.url))

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    yield calls
    assert calls == []


@pytest.fixture
def revocation(monkeypatch):
    from common_auth import revocation

    monkeypatch.setattr(revocation, "CHECK_BLOCKED", True)  # user-service tắt cờ này khi import app
    revocation.clear()
    yield revocation
    revocation.clear()


@pytest.fixture
def writers_guarded(foundation):
    from app.api.files import get_files
    from app.api.identity_ocr import get_identity_ocr
    from app.api.identity_profile import get_identity_profile

    class NoBusinessSideEffects:
        def __getattr__(self, name):
            raise AssertionError("Revocation phải chặn trước khi chạm business")

    for dependency in (get_files, get_identity_ocr, get_identity_profile):
        foundation[0].dependency_overrides[dependency] = NoBusinessSideEffects
    return TestClient(foundation[0])


@pytest.mark.parametrize("method,path", WRITERS)
def test_blocked_after_token_issued_is_rejected_before_business(
        writers_guarded, service_auth, revocation, no_user_service_http, method, path):
    token = service_auth()
    revocation.block_user(OWNER, at=time.time() + 60)  # blocked_at > iat
    response = getattr(writers_guarded, method)(
        path, headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())},
        json={"owner_user_id": str(uuid4())})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert no_user_service_http == []


@pytest.mark.parametrize("method,path", WRITERS)
def test_logged_out_jti_is_rejected_before_business(writers_guarded, service_auth, revocation, method, path):
    token = service_auth(jti="logged-out-jti")
    revocation.revoke_jti("logged-out-jti", time.time() + 600)
    response = getattr(writers_guarded, method)(
        path, headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())}, json={})
    assert response.status_code == 401 and response.json()["error"]["code"] == "UNAUTHORIZED"


def test_token_issued_after_unblock_still_works(foundation, service_auth, revocation):
    revocation.block_user(OWNER, at=time.time() - 3600)  # khoá từ lâu, token mới phát hành sau đó
    client = TestClient(foundation[0])
    response = client.get("/api/v1/documents/me", headers={"Authorization": "Bearer " + service_auth()})
    assert response.status_code == 200


def test_same_token_works_until_user_is_blocked(foundation, service_auth, revocation):
    client = TestClient(foundation[0])
    headers = {"Authorization": "Bearer " + service_auth()}
    assert client.get("/api/v1/documents/me", headers=headers).status_code == 200
    revocation.block_user(OWNER, at=time.time() + 60)
    assert client.get("/api/v1/documents/me", headers=headers).status_code == 401


def test_auth_and_csrf_still_fail_first(foundation, service_auth, revocation):
    client = TestClient(foundation[0])
    assert client.post("/api/v1/documents/identity-ocr").status_code == 401
    client.cookies.set("access_token", service_auth())
    assert client.post("/api/v1/documents/identity-ocr").status_code == 403
