"""HttpOnly cookie chạy song song với Bearer JWT."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from common_auth import ACCESS_COOKIE, decode_access_token
from app.core.cookies import REFRESH_COOKIE
from app.core.database import get_db
from app.core.passwords import hash_password
from app.main import app
from app.models import User
from app.repositories import user_repository as users
from app.services.auth_service import issue_token_pair

AUTH = "/api/v1/users/auth"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    for name in ("JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH", "AUTH_COOKIE_SECURE", "AUTH_COOKIE_SAMESITE",
                 "AUTH_COOKIE_DOMAIN", "AUTH_REFRESH_COOKIE_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_PRIVATE_KEY", private_pem)
    monkeypatch.setenv("JWT_PUBLIC_KEY", public_pem)


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def user(db):
    u = User(email="a@ai-legal.local", username="nguyenvana", password_hash=hash_password("123456"),
             full_name="Nguyễn Văn A", auth_provider="local")
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def logged_in(client, user):
    """Đăng nhập như trình duyệt: cookie server set nằm trong cookie jar của client."""
    res = client.post(f"{AUTH}/login", json={"username": "nguyenvana", "password": "123456"})
    assert res.status_code == 200
    return res.json()["data"]


def _set_cookie_headers(res) -> list[str]:
    return res.headers.get_list("set-cookie")


def _cookie_header(res, name) -> str:
    return next(h for h in _set_cookie_headers(res) if h.startswith(f"{name}="))


def test_refresh_bang_cookie_khong_can_body(client, user, logged_in):
    res = client.post(f"{AUTH}/refresh")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["refresh_token"] != logged_in["refresh_token"]
    # Cookie mới được set và trùng với token trả trong body
    assert client.cookies.get(ACCESS_COOKIE) == data["access_token"]
    assert client.cookies.get(REFRESH_COOKIE) == data["refresh_token"]
    assert decode_access_token(data["access_token"]).id == str(user.id)


def test_cookie_co_httponly_samesite_va_path(client, logged_in):
    res = client.post(f"{AUTH}/refresh")
    access = _cookie_header(res, ACCESS_COOKIE).lower()
    refresh = _cookie_header(res, REFRESH_COOKIE).lower()
    for h in (access, refresh):
        assert "httponly" in h and "samesite=lax" in h and "secure" not in h
    assert "path=/;" in access or access.endswith("path=/")
    assert "path=/api" in refresh


def test_samesite_none_tu_bat_secure(client, logged_in, monkeypatch):
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "none")
    res = client.post(f"{AUTH}/refresh")
    h = _cookie_header(res, ACCESS_COOKIE).lower()
    assert "samesite=none" in h and "secure" in h


def test_body_duoc_uu_tien_hon_cookie(client, db, logged_in):
    other = users.create(db, firebase_uid="fb-2", email="b@gmail.com")
    other_pair = issue_token_pair(db, other)
    db.commit()
    res = client.post(f"{AUTH}/refresh", json={"refresh_token": other_pair["refresh_token"]})
    assert res.status_code == 200
    assert decode_access_token(res.json()["data"]["access_token"]).id == str(other.id)


def test_refresh_cookie_hong_tra_401_va_xoa_cookie(client):
    client.cookies.set(REFRESH_COOKIE, "chuoi-bat-ky")
    res = client.post(f"{AUTH}/refresh")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"
    assert 'max-age=0' in _cookie_header(res, REFRESH_COOKIE).lower()
    assert 'max-age=0' in _cookie_header(res, ACCESS_COOKIE).lower()


def test_me_bang_cookie(client, user, logged_in):
    res = client.get("/api/v1/users/me")
    assert res.status_code == 200
    assert res.json()["data"]["id"] == str(user.id)


def test_bearer_header_duoc_uu_tien_hon_cookie(client, logged_in):
    res = client.get("/api/v1/users/me", headers={"Authorization": "Bearer token-rac"})
    assert res.status_code == 401


def test_logout_bang_cookie_thu_hoi_va_xoa_cookie(client, logged_in):
    res = client.post(f"{AUTH}/logout")
    assert res.status_code == 200
    assert res.json()["data"] == {"logged_out": True}
    assert 'max-age=0' in _cookie_header(res, REFRESH_COOKIE).lower()
    assert client.cookies.get(ACCESS_COOKIE) is None
    # Refresh token cũ đã bị thu hồi
    res = client.post(f"{AUTH}/refresh", json={"refresh_token": logged_in["refresh_token"]})
    assert res.status_code == 401


def test_login_tra_token_trong_body_va_set_cookie(client, user, logged_in):
    # Body vẫn trả token như cũ (Bearer) và đồng thời set cookie
    assert logged_in["access_token"] and logged_in["refresh_token"]
    assert client.cookies.get(ACCESS_COOKIE) == logged_in["access_token"]
    assert client.cookies.get(REFRESH_COOKIE) == logged_in["refresh_token"]
    assert decode_access_token(logged_in["access_token"]).id == str(user.id)
