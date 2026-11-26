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
                 "AUTH_COOKIE_DOMAIN", "AUTH_REFRESH_COOKIE_PATH", "AUTH_BROWSER_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_PRIVATE_KEY", private_pem)
    monkeypatch.setenv("JWT_PUBLIC_KEY", public_pem)


@pytest.fixture
def client(db, monkeypatch, keys):
    # TestClient gọi thẳng service qua đường dẫn có /v1.
    monkeypatch.setenv("AUTH_REFRESH_COOKIE_PATH", AUTH)

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False, headers={"Origin": "http://localhost:3000"})
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
    client.headers.pop("Origin", None)
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


# Browser readiness protocol; existing /refresh tests remain unchanged.
SESSION = f"{AUTH}/session"


def _browser_cookies(access, refresh):
    return {"Cookie": f"{ACCESS_COOKIE}={access}; {REFRESH_COOKIE}={refresh}"}


def _expired_access(monkeypatch, user):
    from datetime import timedelta
    from app.core import security

    with monkeypatch.context() as scoped:
        scoped.setattr(security, "ACCESS_TOKEN_TTL", timedelta(seconds=-30))
        return security.create_access_token(str(user.id), user.role.value)


def test_session_anonymous_is_200_and_never_clears_newer_cookies(client):
    res = client.post(SESSION)
    assert res.status_code == 200
    assert res.json()["data"]["state"] == "anonymous"
    assert res.headers["cache-control"] == "no-store"
    assert not _set_cookie_headers(res)


def test_session_live_access_does_not_rotate(client, db, logged_in):
    from app.core import security
    from app.repositories import refresh_token_repository as refresh_tokens

    res = client.post(SESSION)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["state"] == "ready" and data["renewed"] is False
    assert data["expires_in"] > 60 and data["jti"]
    assert "access_token" not in data and "refresh_token" not in data
    old = refresh_tokens.get_by_hash(db, security.hash_refresh_token(logged_in["refresh_token"]))
    assert old.revoked_at is None
    assert not _set_cookie_headers(res)


def test_session_expired_access_rotates_before_protected_api(client, db, user, logged_in, monkeypatch):
    from app.core import security
    from app.repositories import refresh_token_repository as refresh_tokens

    headers = _browser_cookies(_expired_access(monkeypatch, user), logged_in["refresh_token"])
    res = client.post(SESSION, headers=headers)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["state"] == "ready" and data["renewed"] is True
    assert data["user_id"] == str(user.id) and data["expires_in"] > 60
    assert "access_token" not in data and "refresh_token" not in data
    old = refresh_tokens.get_by_hash(db, security.hash_refresh_token(logged_in["refresh_token"]))
    assert old.revoked_at is not None
    assert client.get("/api/v1/users/me").status_code == 200


def test_session_same_rejected_generation_only_rotates_once(client, db, logged_in):
    import jwt
    from app.core import security
    from app.repositories import refresh_token_repository as refresh_tokens

    old_jti = jwt.decode(logged_in["access_token"], options={"verify_signature": False})["jti"]
    first = client.post(SESSION, params={"rejected_jti": old_jti})
    assert first.json()["data"]["renewed"] is True
    new_refresh = client.cookies.get(REFRESH_COOKIE)
    second = client.post(SESSION, params={"rejected_jti": old_jti})
    assert second.json()["data"]["renewed"] is False
    assert first.json()["data"]["jti"] == second.json()["data"]["jti"]
    assert not _set_cookie_headers(second)
    token = refresh_tokens.get_by_hash(db, security.hash_refresh_token(new_refresh))
    assert token.revoked_at is None


def test_session_late_competitor_gets_retry_without_tokens_revocation_or_cookie_deletion(
    client, db, user, logged_in, monkeypatch
):
    from app.core import security
    from app.repositories import refresh_token_repository as refresh_tokens

    stale = _browser_cookies(_expired_access(monkeypatch, user), logged_in["refresh_token"])
    assert client.post(SESSION, headers=stale).json()["data"]["state"] == "ready"
    new_refresh = client.cookies.get(REFRESH_COOKIE)
    loser = client.post(SESSION, headers=stale)
    assert loser.status_code == 200 and loser.json()["data"] == {"state": "retry"}
    assert not _set_cookie_headers(loser)
    assert refresh_tokens.get_by_hash(db, security.hash_refresh_token(new_refresh)).revoked_at is None
    assert client.post(SESSION).json()["data"]["state"] == "ready"
    assert client.get("/api/v1/users/me").status_code == 200


def test_session_reuse_outside_grace_revokes_all_without_cookie_deletion(
    client, db, user, logged_in, monkeypatch
):
    from datetime import datetime, timedelta, timezone
    from app.core import security
    from app.repositories import refresh_token_repository as refresh_tokens

    stale = _browser_cookies(_expired_access(monkeypatch, user), logged_in["refresh_token"])
    assert client.post(SESSION, headers=stale).json()["data"]["state"] == "ready"
    new_refresh = client.cookies.get(REFRESH_COOKIE)
    old = refresh_tokens.get_by_hash(db, security.hash_refresh_token(logged_in["refresh_token"]))
    old.revoked_at = datetime.now(timezone.utc) - timedelta(seconds=11)
    db.commit()
    res = client.post(SESSION, headers=stale)
    assert res.status_code == 200 and res.json()["data"]["state"] == "anonymous"
    assert not _set_cookie_headers(res)
    assert refresh_tokens.get_by_hash(db, security.hash_refresh_token(new_refresh)).revoked_at is not None


def test_session_locked_user_never_reaches_protected_api(client, db, user, logged_in):
    from app.models import UserStatus

    user.status = UserStatus.locked
    db.commit()
    res = client.post(SESSION)
    assert res.status_code == 200
    assert res.json()["data"]["state"] == "anonymous"
    assert res.json()["data"]["code"] == "ACCOUNT_LOCKED"


def test_session_bad_refresh_is_state_without_set_cookie(client):
    res = client.post(SESSION, headers=_browser_cookies("bad-access", "bad-refresh"))
    assert res.status_code == 200
    assert res.json()["data"]["state"] == "anonymous"
    assert not _set_cookie_headers(res)


@pytest.mark.parametrize("headers,expected", [
    ({"Origin": "http://localhost:3000"}, 200),
    ({"Origin": "http://127.0.0.1:3000"}, 200),
    ({"Referer": "http://localhost:3000/user-dashboard?q=1"}, 200),
    ({}, 403),
    ({"Origin": "null"}, 403),
    ({"Origin": "https://evil.example"}, 403),
    ({"Origin": "http://localhost:3000.evil.example"}, 403),
    ({"Origin": "http://localhost:3000", "Referer": "https://evil.example/page"}, 200),
    ({"Origin": "https://evil.example", "Referer": "http://localhost:3000/page"}, 403),
    ({"Origin": "http://localhost:3000/path"}, 403),
    ({"Referer": "https://evil.example/page"}, 403),
    ({"Origin": "http://localhost:3000@evil.example"}, 403),
])
def test_session_origin_policy(client, db, user, logged_in, monkeypatch, headers, expected):
    from app.core import security
    from app.repositories import refresh_token_repository as refresh_tokens

    client.headers.pop("Origin", None)
    # Force the refresh path; denied origins must never get as far as rotation.
    request_headers = _browser_cookies(_expired_access(monkeypatch, user), logged_in["refresh_token"])
    request_headers.update(headers)
    res = client.post(SESSION, headers=request_headers)
    assert res.status_code == expected
    old = refresh_tokens.get_by_hash(db, security.hash_refresh_token(logged_in["refresh_token"]))
    if expected == 403:
        assert res.json()["error"]["code"] == "CSRF_ORIGIN_DENIED"
        assert old.revoked_at is None
        assert not _set_cookie_headers(res)
    else:
        assert res.json()["data"]["state"] == "ready"
        assert refresh_tokens.is_rotation(old)


def test_session_explicit_deployed_origin_ignores_proxy_host(client, monkeypatch):
    monkeypatch.setenv("AUTH_BROWSER_ORIGINS", "https://portal.example.vn")
    res = client.post(SESSION, headers={
        "Origin": "https://portal.example.vn",
        "Host": "user-service:8000",
        "X-Forwarded-Host": "gateway.internal",
    })
    assert res.status_code == 200
    assert res.json()["data"]["state"] == "anonymous"
    denied = client.post(SESSION, headers={
        "Origin": "https://evil.example", "X-Forwarded-Host": "evil.example",
    })
    assert denied.status_code == 403
    assert not _set_cookie_headers(denied)


@pytest.mark.parametrize("rotation_endpoint", ["session", "refresh"])
def test_both_rotation_paths_are_eligible_for_session_grace(
    client, db, user, logged_in, monkeypatch, rotation_endpoint
):
    from app.core import security
    from app.repositories import refresh_token_repository as refresh_tokens

    stale = _browser_cookies(_expired_access(monkeypatch, user), logged_in["refresh_token"])
    assert client.post(f"{AUTH}/{rotation_endpoint}", headers=stale).status_code == 200
    winner = client.cookies.get(REFRESH_COOKIE)
    old = refresh_tokens.get_by_hash(db, security.hash_refresh_token(logged_in["refresh_token"]))
    assert refresh_tokens.is_rotation(old)
    loser = client.post(SESSION, headers=stale)
    assert loser.json()["data"] == {"state": "retry"}
    assert not _set_cookie_headers(loser)
    assert refresh_tokens.get_by_hash(db, security.hash_refresh_token(winner)).revoked_at is None


@pytest.mark.parametrize("reason", ["logout", "revoke_all", "lock", "reuse_all"])
def test_active_revocation_cancels_grace_for_rotated_ancestors(
    client, db, user, logged_in, monkeypatch, reason
):
    from app.core import security
    from app.models import UserStatus
    from app.repositories import refresh_token_repository as refresh_tokens

    stale = _browser_cookies(_expired_access(monkeypatch, user), logged_in["refresh_token"])
    assert client.post(SESSION, headers=stale).json()["data"]["state"] == "ready"
    successor = client.cookies.get(REFRESH_COOKIE)
    if reason == "logout":
        assert client.post(f"{AUTH}/logout").status_code == 200
    elif reason == "reuse_all":
        # Existing /refresh retains strict reuse detection even inside 10 seconds.
        res = client.post(f"{AUTH}/refresh", json={"refresh_token": logged_in["refresh_token"]})
        assert res.status_code == 401
    else:
        if reason == "lock":
            user.status = UserStatus.locked
        refresh_tokens.revoke_all_for_user(db, user.id)
        db.commit()
    old = refresh_tokens.get_by_hash(db, security.hash_refresh_token(logged_in["refresh_token"]))
    assert old.revoked_at is not None and not refresh_tokens.is_rotation(old)
    res = client.post(SESSION, headers=stale)
    assert res.status_code == 200
    assert res.json()["data"]["state"] == "anonymous"
    assert not _set_cookie_headers(res)
    expected_code = "ACCOUNT_LOCKED" if reason == "lock" else "INVALID_REFRESH_TOKEN"
    assert res.json()["data"]["code"] == expected_code
    assert refresh_tokens.get_by_hash(db, security.hash_refresh_token(successor)).revoked_at is not None


@pytest.mark.parametrize("reason", ["logout", "revoke_all", "lock"])
def test_direct_revocation_never_becomes_rotation_grace(
    client, db, user, logged_in, monkeypatch, reason
):
    from app.core import security
    from app.models import UserStatus
    from app.repositories import refresh_token_repository as refresh_tokens

    if reason == "logout":
        assert client.post(f"{AUTH}/logout").status_code == 200
    else:
        if reason == "lock":
            user.status = UserStatus.locked
        refresh_tokens.revoke_all_for_user(db, user.id)
        db.commit()
    old = refresh_tokens.get_by_hash(db, security.hash_refresh_token(logged_in["refresh_token"]))
    assert old.revoked_at is not None and not refresh_tokens.is_rotation(old)
    res = client.post(SESSION, headers=_browser_cookies(
        _expired_access(monkeypatch, user), logged_in["refresh_token"]
    ))
    assert res.json()["data"]["state"] == "anonymous"
    assert not _set_cookie_headers(res)


def test_account_lock_precedes_rotation_grace_even_without_prior_revoke_all(
    client, db, user, logged_in, monkeypatch
):
    from app.models import UserStatus
    from app.core import security
    from app.repositories import refresh_token_repository as refresh_tokens

    stale = _browser_cookies(_expired_access(monkeypatch, user), logged_in["refresh_token"])
    assert client.post(SESSION, headers=stale).json()["data"]["state"] == "ready"
    user.status = UserStatus.locked
    db.commit()
    res = client.post(SESSION, headers=stale)
    assert res.json()["data"]["state"] == "anonymous"
    assert res.json()["data"]["code"] == "ACCOUNT_LOCKED"
    old = refresh_tokens.get_by_hash(db, security.hash_refresh_token(logged_in["refresh_token"]))
    assert not refresh_tokens.is_rotation(old)


def test_legacy_revoked_token_without_marker_is_fail_closed(client, db, user, logged_in, monkeypatch):
    from datetime import datetime, timezone
    from app.core import security
    from app.repositories import refresh_token_repository as refresh_tokens

    old = refresh_tokens.get_by_hash(db, security.hash_refresh_token(logged_in["refresh_token"]))
    old.revoked_at = datetime.now(timezone.utc)
    db.commit()
    res = client.post(SESSION, headers=_browser_cookies(
        _expired_access(monkeypatch, user), logged_in["refresh_token"]
    ))
    assert res.json()["data"]["state"] == "anonymous"
    assert not _set_cookie_headers(res)
