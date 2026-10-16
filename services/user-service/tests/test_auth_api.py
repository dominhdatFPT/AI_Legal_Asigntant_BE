from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from common_auth import decode_access_token
from app.core import security
from app.core.database import get_db
from app.main import app
from app.models import UserRole, UserStatus
from app.repositories import refresh_token_repository as refresh_tokens
from app.repositories import user_repository as users
from app.services.auth_service import issue_token_pair


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    for name in ("JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH"):
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
    u = users.create(db, firebase_uid="fb-1", email="a@gmail.com", full_name="Nguyễn Văn A", auth_provider="google")
    db.commit()
    return u


@pytest.fixture
def pair(db, user):
    p = issue_token_pair(db, user)
    db.commit()
    return p


def _refresh(client, token):
    return client.post("/api/v1/users/auth/refresh", json={"refresh_token": token})


def _bearer(pair):
    return {"Authorization": f"Bearer {pair['access_token']}"}


def _error_code(res):
    body = res.json()
    assert body["data"] is None
    assert body["request_id"] == res.headers["X-Request-ID"]
    return body["error"]["code"]


# ---------- refresh ----------

def test_refresh_thanh_cong_tra_cap_token_moi(client, user, pair):
    res = _refresh(client, pair["refresh_token"])
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["token_type"] == "Bearer" and data["expires_in"] == 900
    assert data["refresh_token"] != pair["refresh_token"]
    assert decode_access_token(data["access_token"]).id == str(user.id)


def test_refresh_token_cu_bi_thu_hoi_sau_khi_doi(client, db, pair):
    _refresh(client, pair["refresh_token"])
    old = refresh_tokens.get_by_hash(db, security.hash_refresh_token(pair["refresh_token"]))
    assert old.revoked_at is not None


def test_dung_lai_token_cu_thi_thu_hoi_toan_bo(client, db, pair):
    new_refresh = _refresh(client, pair["refresh_token"]).json()["data"]["refresh_token"]
    res = _refresh(client, pair["refresh_token"])
    assert res.status_code == 401
    assert _error_code(res) == "INVALID_REFRESH_TOKEN"
    assert _refresh(client, new_refresh).status_code == 401


def test_refresh_token_khong_ton_tai(client):
    res = _refresh(client, "chuoi-bat-ky")
    assert res.status_code == 401
    assert _error_code(res) == "INVALID_REFRESH_TOKEN"


def test_refresh_token_het_han(client, db, pair):
    token = refresh_tokens.get_by_hash(db, security.hash_refresh_token(pair["refresh_token"]))
    token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    res = _refresh(client, pair["refresh_token"])
    assert res.status_code == 401
    assert _error_code(res) == "INVALID_REFRESH_TOKEN"


def test_tai_khoan_bi_khoa_khong_refresh_duoc(client, db, user, pair):
    user.status = UserStatus.locked
    db.commit()
    res = _refresh(client, pair["refresh_token"])
    assert res.status_code == 403
    assert _error_code(res) == "ACCOUNT_LOCKED"


def test_refresh_lay_role_moi_nhat_tu_db(client, db, user, pair):
    user.role = UserRole.lawyer
    db.commit()
    access = _refresh(client, pair["refresh_token"]).json()["data"]["access_token"]
    assert decode_access_token(access).role == "lawyer"


def test_refresh_khong_co_body_lan_cookie_tra_401(client):
    res = client.post("/api/v1/users/auth/refresh", json={})
    assert res.status_code == 401
    assert _error_code(res) == "INVALID_REFRESH_TOKEN"


@pytest.mark.parametrize("body", [{"refresh_token": ""}, {"refresh_token": "   "}])
def test_refresh_body_sai_tra_422(client, body):
    res = client.post("/api/v1/users/auth/refresh", json=body)
    assert res.status_code == 422
    assert _error_code(res) == "VALIDATION_ERROR"
    assert res.json()["error"]["details"][0]["field"] == "refresh_token"


# ---------- logout ----------

def test_logout_can_access_token(client, pair):
    res = client.post("/api/v1/users/auth/logout", json={"refresh_token": pair["refresh_token"]})
    assert res.status_code == 401
    assert _error_code(res) == "UNAUTHORIZED"


def test_logout_thu_hoi_refresh_token(client, pair):
    res = client.post("/api/v1/users/auth/logout", json={"refresh_token": pair["refresh_token"]}, headers=_bearer(pair))
    assert res.status_code == 200
    assert res.json()["data"] == {"logged_out": True}
    assert _refresh(client, pair["refresh_token"]).status_code == 401


def test_logout_lan_hai_van_tra_200(client, pair):
    body, headers = {"refresh_token": pair["refresh_token"]}, _bearer(pair)
    client.post("/api/v1/users/auth/logout", json=body, headers=headers)
    assert client.post("/api/v1/users/auth/logout", json=body, headers=headers).status_code == 200


def test_khong_logout_duoc_token_cua_nguoi_khac(client, db, pair):
    other = users.create(db, firebase_uid="fb-2", email="b@gmail.com")
    other_pair = issue_token_pair(db, other)
    db.commit()
    res = client.post(
        "/api/v1/users/auth/logout", json={"refresh_token": other_pair["refresh_token"]}, headers=_bearer(pair)
    )
    assert res.status_code == 200
    assert _refresh(client, other_pair["refresh_token"]).status_code == 200


# ---------- /users/me ----------

def test_me_tra_ho_so_nguoi_dang_dang_nhap(client, user, pair):
    res = client.get("/api/v1/users/me", headers={**_bearer(pair), "X-Request-ID": "req-test"})
    assert res.status_code == 200
    body = res.json()
    assert body["request_id"] == "req-test" and res.headers["X-Request-ID"] == "req-test"
    data = body["data"]
    assert data["id"] == str(user.id)
    assert data["email"] == "a@gmail.com"
    assert data["role"] == "citizen" and data["status"] == "active"
    assert "firebase_uid" not in data


def test_me_khong_co_token(client):
    res = client.get("/api/v1/users/me")
    assert res.status_code == 401
    assert _error_code(res) == "UNAUTHORIZED"


def test_me_tai_khoan_bi_khoa(client, db, user, pair):
    user.status = UserStatus.locked
    db.commit()
    res = client.get("/api/v1/users/me", headers=_bearer(pair))
    assert res.status_code == 403
    assert _error_code(res) == "ACCOUNT_LOCKED"


def test_me_user_da_bi_xoa(client, db, user, pair):
    user.deleted_at = datetime.now(timezone.utc)
    db.commit()
    res = client.get("/api/v1/users/me", headers=_bearer(pair))
    assert res.status_code == 401


def test_me_token_co_sub_khong_phai_uuid(client):
    token = security.create_access_token("khong-phai-uuid", "citizen")
    res = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_health_van_chay(client):
    assert client.get("/api/v1/users/health").json() == {"status": "ok", "service": "user-service"}
