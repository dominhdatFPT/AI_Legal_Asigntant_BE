from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from common_auth import decode_access_token
from app.core.database import get_db
from app.main import app
from app.repositories import user_repository as users

URL = "/api/v1/users/auth/register"
FORM = {"email": "Moi@Gmail.com", "phone": "0912.345.678", "password": "matkhau123", "confirm_password": "matkhau123"}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for name in ("JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_PRIVATE_KEY", key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode())
    monkeypatch.setenv("JWT_PUBLIC_KEY", key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode())


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _code(res):
    return res.json()["error"]["code"]


def test_dang_ky_thanh_cong_va_dang_nhap_luon(client, db):
    res = client.post(URL, json=FORM)
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["is_new_user"] is True
    assert data["user"]["email"] == "moi@gmail.com"
    assert data["user"]["phone"] == "0912345678"
    assert data["user"]["role"] == "citizen"
    assert decode_access_token(data["access_token"]).id == data["user"]["id"]
    assert "access_token" in res.cookies

    saved = users.get_by_email(db, "moi@gmail.com")
    assert saved.auth_provider == "local" and saved.firebase_uid is None
    assert saved.password_hash != "matkhau123"

    login = client.post("/api/v1/users/auth/login", json={"username": "moi@gmail.com", "password": "matkhau123"})
    assert login.status_code == 200


@pytest.mark.parametrize("changes", [
    {"confirm_password": "khac12345"},
    {"password": "ngan", "confirm_password": "ngan"},
    {"phone": "12345"},
    {"email": "khong-phai-email"},
    {"phone": None},
    {"thua": "x"},
])
def test_du_lieu_sai_tra_422(client, changes):
    res = client.post(URL, json={**FORM, **changes})
    assert res.status_code == 422
    assert _code(res) == "VALIDATION_ERROR"


def test_trung_email_tra_409(client, db):
    users.create(db, firebase_uid="uid-g", email="moi@gmail.com", auth_provider="google")
    db.commit()
    res = client.post(URL, json=FORM)
    assert res.status_code == 409
    assert _code(res) == "EMAIL_IN_USE"


def test_email_tai_khoan_da_xoa_tra_403(client, db):
    user = users.create(db, firebase_uid="uid-g", email="moi@gmail.com", auth_provider="google")
    user.deleted_at = datetime.now(timezone.utc)
    db.commit()
    res = client.post(URL, json=FORM)
    assert res.status_code == 403
    assert _code(res) == "ACCOUNT_DELETED"


def test_trung_so_dien_thoai_tra_409(client):
    assert client.post(URL, json=FORM).status_code == 201
    res = client.post(URL, json={**FORM, "email": "khac@gmail.com", "phone": "0912345678"})
    assert res.status_code == 409
    assert _code(res) == "PHONE_IN_USE"
