"""Task 1 offline tests: group JWT, sanitized errors, config, DB and Storage adapters."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import MagicMock

import httpx
import psycopg
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services/document-service"


@pytest.fixture
def foundation():
    # The monorepo has multiple unrelated packages all named app. Restore after this fixture.
    previous = {name: module for name, module in list(sys.modules.items()) if name == "app" or name.startswith("app.")}
    for name in previous:
        del sys.modules[name]
    sys.path.insert(0, str(SERVICE))
    try:
        from app.main import app
        from app.core.config import Settings, ConfigurationError
        from app.core.errors import DocumentError
        from app.core.security import document_user
        from app.repositories.database import Database
        from app.services.storage import Storage, MIME_TYPES
        yield app, Settings, ConfigurationError, DocumentError, document_user, Database, Storage, MIME_TYPES
    finally:
        sys.path.remove(str(SERVICE))
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        sys.modules.update(previous)


@pytest.fixture
def config_env(monkeypatch):
    ref = "a" * 20
    for name, value in {
        "DOCUMENT_SUPABASE_URL": f"https://{ref}.supabase.co",
        "DOCUMENT_SUPABASE_SERVICE_ROLE_KEY": "sb_secret_test_only",
        "DOCUMENT_DATABASE_URL": f"postgresql://postgres:test-password@db.{ref}.supabase.co:5432/postgres",
        "DOCUMENT_STORAGE_BUCKET": "document-files",
        "DOCUMENT_HTTP_TIMEOUT_SECONDS": "20",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DOCUMENT_DATABASE_POOLER_HOST", raising=False)
    monkeypatch.delenv("DOCUMENT_ALLOWED_ORIGINS", raising=False)


def test_health_does_not_need_secrets(foundation, monkeypatch):
    for name in ["DOCUMENT_DATABASE_URL", "DOCUMENT_SUPABASE_URL", "JWT_PUBLIC_KEY", "JWT_PUBLIC_KEY_PATH"]:
        monkeypatch.delenv(name, raising=False)
    response = TestClient(foundation[0]).get("/api/v1/documents/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "document-service"}


@pytest.mark.parametrize("role", ["citizen", "lawyer", "admin"])
@pytest.mark.parametrize("cookie", [False, True])
def test_common_jwt(foundation, service_auth, role, cookie):
    client = TestClient(foundation[0])
    token = service_auth(role=role)
    headers = {"X-Request-ID": "task1-test"}
    if cookie:
        client.cookies.set("access_token", token)
    else:
        headers["Authorization"] = "Bearer " + token
    response = client.get("/api/v1/documents/me", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"data": {"user_id": "11111111-1111-4111-8111-111111111111", "role": role},
                               "error": None, "request_id": "task1-test"}
    assert response.headers["X-Request-ID"] == "task1-test"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("claims,status,code", [
    ({"iss": "foreign"}, 401, "UNAUTHORIZED"),
    ({"type": "refresh"}, 401, "UNAUTHORIZED"),
    ({"sub": "not-a-uuid"}, 401, "UNAUTHORIZED"),
    ({"role": "intruder"}, 403, "FORBIDDEN"),
    ({"exp": datetime.now(timezone.utc) - timedelta(minutes=2)}, 401, "TOKEN_EXPIRED"),
])
def test_invalid_claims(foundation, service_auth, claims, status, code):
    response = TestClient(foundation[0]).get("/api/v1/documents/me", headers={"Authorization": "Bearer " + service_auth(**claims)})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["data"] is None


def test_missing_token_and_invalid_header_never_fall_back(foundation, service_auth):
    client = TestClient(foundation[0])
    assert client.get("/api/v1/documents/me").status_code == 401
    client.cookies.set("access_token", service_auth())
    assert client.get("/api/v1/documents/me", headers={"Authorization": "Basic wrong"}).status_code == 401


@pytest.mark.parametrize("origin,status", [(None, 403), ("https://evil.example", 403), ("http://localhost:3000", 200)])
def test_cookie_mutation_origin(foundation, service_auth, origin, status, config_env):
    app, _, _, _, dependency, *_ = foundation
    @app.post("/test-mutation", dependencies=[Depends(dependency)])
    def mutation():
        return {"ok": True}
    client = TestClient(app)
    client.cookies.set("access_token", service_auth())
    headers = {} if origin is None else {"Origin": origin}
    assert client.post("/test-mutation", headers=headers).status_code == status


def test_bearer_mutation_needs_no_origin(foundation, service_auth):
    app, _, _, _, dependency, *_ = foundation
    @app.post("/test-mutation", dependencies=[Depends(dependency)])
    def mutation():
        return {"ok": True}
    assert TestClient(app).post("/test-mutation", headers={"Authorization": "Bearer " + service_auth()}).status_code == 200


def test_errors_never_echo_input_or_exception(foundation, service_auth):
    app = foundation[0]
    class Input(BaseModel):
        amount: int
    @app.post("/test-validation")
    def validate(body: Input):
        return {}
    @app.get("/test-error")
    def crash():
        raise RuntimeError("sb_secret_private CCCD-001095000001 test-password")
    client = TestClient(app, raise_server_exceptions=False)
    for response in [client.post("/test-validation", json={"amount": "CCCD-001095000001"}), client.get("/test-error")]:
        assert response.status_code in {422, 500}
        assert response.json()["data"] is None
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        for secret in ["sb_secret_private", "CCCD-001095000001", "test-password"]:
            assert secret not in response.text


def test_not_found_and_bad_request_id(foundation):
    response = TestClient(foundation[0]).get("/missing", headers={"X-Request-ID": "sensitive value / invalid"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.json()["request_id"].startswith("req-")


def test_malformed_json_matches_group_bad_request(foundation):
    app = foundation[0]
    class Input(BaseModel):
        amount: int
    @app.post("/test-json")
    def accept(body: Input):
        return {}
    response = TestClient(app).post("/test-json", content='{"amount":', headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"


def test_tls_pooler_and_secret_repr(foundation, config_env, monkeypatch):
    Settings = foundation[1]
    monkeypatch.setenv("DOCUMENT_DATABASE_POOLER_HOST", "aws-0-ap-northeast-1.pooler.supabase.com")
    settings = Settings.from_env()
    info = psycopg.conninfo.conninfo_to_dict(settings.database_url)
    assert info["sslmode"] == "require"
    assert info["user"] == "postgres." + "a" * 20
    assert info["port"] == "5432"
    assert info["password"] == "test-password"
    assert "test-password" not in repr(settings)
    assert "sb_secret_test_only" not in repr(settings)


@pytest.mark.parametrize("name,value", [
    ("DOCUMENT_SUPABASE_URL", "https://example.com"),
    ("DOCUMENT_SUPABASE_URL", "https://[bad"),
    ("DOCUMENT_SUPABASE_URL", "https://" + "a" * 20 + ".supabase.co/?token=secret"),
    ("DOCUMENT_DATABASE_URL", "postgresql://secret@db.other.supabase.co/postgres"),
    ("DOCUMENT_DATABASE_URL", "not a connection secret"),
    ("DOCUMENT_DATABASE_URL", ""),
    ("DOCUMENT_DATABASE_URL", "postgresql://postgres:secret@db." + "a" * 20 + ".supabase.co/postgres?sslmode=disable"),
    ("DOCUMENT_DATABASE_URL", "postgresql://postgres:secret@db." + "a" * 20 + ".supabase.co/postgres?hostaddr=127.0.0.1"),
    ("DOCUMENT_SUPABASE_SERVICE_ROLE_KEY", ""),
    ("DOCUMENT_SUPABASE_SERVICE_ROLE_KEY", "sb_publishable_secret"),
    ("DOCUMENT_STORAGE_BUCKET", "public"),
    ("DOCUMENT_DATABASE_POOLER_HOST", "evil.example"),
    ("DOCUMENT_HTTP_TIMEOUT_SECONDS", "nan"),
    ("DOCUMENT_HTTP_TIMEOUT_SECONDS", "0"),
    ("DOCUMENT_HTTP_TIMEOUT_SECONDS", "100"),
])
def test_config_fails_closed(foundation, config_env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(foundation[2]) as exc:
        foundation[1].from_env()
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("origin", ["*", "https://evil.example/path", "https://[bad"])
def test_origin_config_disallows_wildcard_and_paths(foundation, monkeypatch, origin):
    monkeypatch.setenv("DOCUMENT_ALLOWED_ORIGINS", origin)
    with pytest.raises(foundation[2]):
        foundation[1].allowed_origins()


@pytest.mark.parametrize("key,has_bearer", [("sb_secret_test_only", False), ("legacy-test-jwt", True)])
def test_storage_backend_key_headers(foundation, config_env, monkeypatch, key, has_bearer):
    Settings, Storage, mime = foundation[1], foundation[6], foundation[7]
    monkeypatch.setenv("DOCUMENT_SUPABASE_SERVICE_ROLE_KEY", key)
    def handler(request):
        assert request.headers["apikey"] == key
        assert ("authorization" in request.headers) == has_bearer
        assert request.url.path == "/storage/v1/bucket/document-files"
        return httpx.Response(200, json={"id": "document-files", "public": False, "file_size_limit": 9437184,
                                         "allowed_mime_types": list(mime)})
    assert Storage(Settings.from_env(), transport=httpx.MockTransport(handler)).check_bucket()["private"] is True


@pytest.mark.parametrize("body", [{}, [], {"id": "document-files", "public": True},
    {"id": "document-files", "public": False, "file_size_limit": 9437184, "allowed_mime_types": [None]}])
def test_storage_rejects_unsafe_bucket(foundation, config_env, body):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    with pytest.raises(foundation[3]) as exc:
        foundation[6](foundation[1].from_env(), transport=transport).check_bucket()
    assert exc.value.code == "STORAGE_CONFIGURATION_ERROR"


@pytest.mark.parametrize("status", [301, 401, 403, 500])
def test_storage_does_not_follow_redirects_or_echo_errors(foundation, config_env, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://evil.example"}, text="sb_secret_private")
    with pytest.raises(foundation[3]) as exc:
        foundation[6](foundation[1].from_env(), transport=httpx.MockTransport(handler)).check_bucket()
    assert len(calls) == 1
    assert "sb_secret" not in str(exc.value)


@pytest.mark.parametrize("error,code,status", [
    (httpx.ReadTimeout("sb_secret_private"), "STORAGE_TIMEOUT", 504),
    (httpx.ConnectError("sb_secret_private"), "STORAGE_UNAVAILABLE", 503),
])
def test_storage_transport_errors_follow_group_codes(foundation, config_env, error, code, status):
    def handler(request):
        raise error
    with pytest.raises(foundation[3]) as exc:
        foundation[6](foundation[1].from_env(), transport=httpx.MockTransport(handler)).check_bucket()
    assert exc.value.code == code
    assert exc.value.status == status
    assert "sb_secret" not in str(exc.value)


def test_storage_invalid_json_is_bad_response(foundation, config_env):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="sb_secret_private not json"))
    with pytest.raises(foundation[3]) as exc:
        foundation[6](foundation[1].from_env(), transport=transport).check_bucket()
    assert exc.value.code == "STORAGE_BAD_RESPONSE"
    assert exc.value.status == 502


def test_database_read_only_connection_closes_and_masks_errors(foundation, config_env, monkeypatch):
    Database = foundation[5]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(psycopg, "connect", connect)
    with Database(foundation[1].from_env()).connection(read_only=True) as conn:
        assert conn is connection
    connection.execute.assert_called_once_with("SET TRANSACTION READ ONLY")
    assert connection.__exit__.called
    assert connect.call_args.kwargs["connect_timeout"] == 10
    connect.side_effect = psycopg.OperationalError("test-password")
    with pytest.raises(foundation[3]) as exc:
        with Database(foundation[1].from_env()).connection():
            pass
    assert "test-password" not in str(exc.value)
