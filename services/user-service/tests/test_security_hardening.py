"""Security regressions for cookie auth and admin access revocation."""

from http.cookies import SimpleCookie
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.core import rabbitmq
from app.core.cookies import REFRESH_COOKIE
from app.core.database import get_db
from app.core.passwords import hash_password
from app.core.rabbitmq_admin import admin_mq
from app.core.security import create_access_token
from app.main import app
from app.models import User, UserRole

AUTH = "/api/users/auth"
USERS = "/api/v1/users"
ALLOWED_ORIGIN = "http://localhost:3000"
FOREIGN_ORIGIN = "https://evil.example"


@pytest.fixture
def client(db, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    for name in (
        "JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH", "AUTH_BROWSER_ORIGINS",
        "AUTH_REFRESH_COOKIE_PATH", "AUTH_COOKIE_SECURE", "AUTH_COOKIE_SAMESITE", "AUTH_COOKIE_DOMAIN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_PRIVATE_KEY", private_pem)
    monkeypatch.setenv("JWT_PUBLIC_KEY", public_pem)

    def override():
        yield db

    async def gateway(scope, receive, send):
        # The cookie jar sees the public URL. The service sees the rewritten path.
        if scope["type"] == "http" and scope["path"].startswith(f"{AUTH}/"):
            scope = {
                **scope,
                "path": scope["path"].replace("/api/users/", "/api/v1/users/", 1),
                "raw_path": scope["raw_path"].replace(b"/api/users/", b"/api/v1/users/", 1),
            }
        await app(scope, receive, send)

    monkeypatch.setitem(app.dependency_overrides, get_db, override)
    test_client = TestClient(gateway, raise_server_exceptions=False)
    try:
        yield test_client
    finally:
        test_client.close()


@pytest.fixture
def accounts(db):
    admin = User(email="admin@example.com", username="admin", auth_provider="local", role=UserRole.admin)
    target = User(
        email="target@example.com", username="target", auth_provider="local",
        role=UserRole.admin, password_hash=hash_password("test-password"),
    )
    db.add_all([admin, target])
    db.commit()
    return admin, target


def _login(client):
    response = client.post(f"{AUTH}/login", json={"username": "target", "password": "test-password"})
    assert response.status_code == 200, response.text
    return response


def _refresh_cookie_headers(response):
    return [
        SimpleCookie(header)[REFRESH_COOKIE]
        for header in response.headers.get_list("set-cookie")
        if header.startswith(f"{REFRESH_COOKIE}=")
    ]


@pytest.mark.parametrize("headers", [
    {"Origin": FOREIGN_ORIGIN},
    {"Sec-Fetch-Site": "cross-site"},
    {"Referer": f"{FOREIGN_ORIGIN}/page"},
])
def test_cookie_refresh_rejects_untrusted_browser_request(client, accounts, headers):
    _login(client)
    response = client.post(f"{AUTH}/refresh", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_ORIGIN_DENIED"
    assert not response.headers.get_list("set-cookie")
    # The denied request leaves the refresh token usable.
    assert client.post(f"{AUTH}/refresh", headers={"Origin": ALLOWED_ORIGIN}).status_code == 200


def test_allowed_browser_refresh_rotates_cookie_without_returning_tokens(client, accounts):
    login = _login(client)
    original = login.json()["data"]["refresh_token"]
    issued, = [cookie for cookie in _refresh_cookie_headers(login) if cookie.value == original]
    assert issued["path"] == AUTH

    response = client.post(f"{AUTH}/refresh", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"expires_in", "token_type"}
    assert data["expires_in"] > 0 and data["token_type"] == "Bearer"
    rotated, = [cookie for cookie in _refresh_cookie_headers(response) if cookie["path"] == AUTH]
    assert rotated.value != original
    assert client.cookies.get(REFRESH_COOKIE) == rotated.value


def test_refresh_migrates_legacy_cookie_and_keeps_headerless_json_contract(client, accounts, monkeypatch):
    with monkeypatch.context() as scoped:
        scoped.setenv("AUTH_REFRESH_COOKIE_PATH", "/api")
        original = _login(client).json()["data"]["refresh_token"]

    response = client.post(f"{AUTH}/refresh")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access_token"] and data["refresh_token"] != original
    cookies = {cookie["path"]: cookie for cookie in _refresh_cookie_headers(response)}
    assert cookies[AUTH].value == data["refresh_token"]
    assert cookies["/api"]["max-age"] == "0"
    assert client.cookies.get(REFRESH_COOKIE) == data["refresh_token"]


def test_explicit_body_refresh_still_returns_tokens(client, accounts):
    original = _login(client).json()["data"]["refresh_token"]
    client.cookies.clear()
    response = client.post(
        f"{AUTH}/refresh", json={"refresh_token": original}, headers={"Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access_token"] and data["refresh_token"] != original


def test_cookie_logout_requires_allowed_origin(client, accounts, monkeypatch):
    _login(client)
    publish = AsyncMock()
    monkeypatch.setattr(rabbitmq, "publish_user_logged_out", publish)

    response = client.post(f"{AUTH}/logout", headers={"Origin": FOREIGN_ORIGIN})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_ORIGIN_DENIED"
    assert not response.headers.get_list("set-cookie")
    publish.assert_not_awaited()

    response = client.post(f"{AUTH}/logout", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    assert {cookie["path"]: cookie["max-age"] for cookie in _refresh_cookie_headers(response)} == {
        AUTH: "0",
        "/api": "0",
    }


@pytest.mark.parametrize("method,suffix,body,status,reason", [
    ("PUT", "/role", {"role": "citizen"}, 200, "role_changed"),
    ("DELETE", "", None, 204, "deleted"),
])
@pytest.mark.parametrize("broker_fails", [False, True])
def test_admin_mutation_publishes_access_revocation(
    client, accounts, monkeypatch, method, suffix, body, status, reason, broker_fails,
):
    admin, target = accounts
    publish = AsyncMock(side_effect=RuntimeError("broker unavailable") if broker_fails else None)
    monkeypatch.setattr(admin_mq, "publish_user_blocked", publish)
    access = create_access_token(str(admin.id), admin.role.value)

    response = client.request(
        method, f"{USERS}/{target.id}{suffix}", json=body, headers={"Authorization": f"Bearer {access}"},
    )
    assert response.status_code == status, response.text
    publish.assert_awaited_once_with(
        user_id=str(target.id), admin_id=str(admin.id), reason=reason, notify_user=False,
    )


def test_unchanged_role_does_not_publish_revocation(client, accounts, monkeypatch):
    admin, target = accounts
    publish = AsyncMock()
    monkeypatch.setattr(admin_mq, "publish_user_blocked", publish)
    access = create_access_token(str(admin.id), admin.role.value)

    response = client.put(
        f"{USERS}/{target.id}/role", json={"role": "admin"}, headers={"Authorization": f"Bearer {access}"},
    )
    assert response.status_code == 200
    publish.assert_not_awaited()
