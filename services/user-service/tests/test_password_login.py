import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from common_auth import decode_access_token
from app.core.database import get_db
from app.core.passwords import hash_password, verify_password
from app.main import app
from app.models import User, UserRole, UserStatus


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


@pytest.fixture
def admin(db):
    user = User(
        email="admin2026@ai-legal.local",
        username="admin2026",
        password_hash=hash_password("123456"),
        full_name="Quản trị viên",
        auth_provider="local",
        role=UserRole.admin,
    )
    db.add(user)
    db.commit()
    return user


def _login(client, username, password):
    return client.post("/api/v1/users/auth/login", json={"username": username, "password": password})


def test_hash_va_kiem_tra_mat_khau():
    hashed = hash_password("123456")
    assert hashed != "123456"
    assert verify_password("123456", hashed)
    assert not verify_password("sai", hashed)
    assert not verify_password("123456", None)


def test_admin_dang_nhap_thanh_cong_tra_role_admin(client, admin):
    res = _login(client, "admin2026", "123456")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["user"]["role"] == "admin"
    assert data["user"]["username"] == "admin2026"
    assert "password_hash" not in data["user"]
    current = decode_access_token(data["access_token"])
    assert current.id == str(admin.id) and current.role == "admin"
    assert data["refresh_token"]


def test_username_khong_phan_biet_hoa_thuong(client, admin):
    assert _login(client, "Admin2026", "123456").status_code == 200


def test_sai_mat_khau(client, admin):
    res = _login(client, "admin2026", "654321")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_username_khong_ton_tai(client):
    res = _login(client, "khong-co", "123456")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_tai_khoan_khong_co_mat_khau_khong_dang_nhap_bang_mat_khau(client, db):
    db.add(User(email="g@gmail.com", username="google-user", firebase_uid="fb-g", auth_provider="google"))
    db.commit()
    assert _login(client, "google-user", "").status_code == 422
    assert _login(client, "google-user", "bat-ky").status_code == 401


def test_tai_khoan_bi_khoa(client, db, admin):
    admin.status = UserStatus.locked
    db.commit()
    res = _login(client, "admin2026", "123456")
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ACCOUNT_LOCKED"
