import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from common_auth import decode_access_token
from app.core import firebase
from app.core.database import get_db
from app.main import app
from app.models import UserStatus
from app.repositories import user_repository as users

PROJECT_ID = "ai-legal-assistant-7875a"

FAKE_TOKENS = {
    "google-a": {
        "sub": "uid-a",
        "email": "a@gmail.com",
        "email_verified": True,
        "name": "Nguyễn Văn A",
        "picture": "https://lh3.googleusercontent.com/a/abc",
        "firebase": {"sign_in_provider": "google.com"},
    },
    "password-verified": {
        "sub": "uid-p",
        "email": "p@gmail.com",
        "email_verified": True,
        "firebase": {"sign_in_provider": "password"},
    },
    "password-unverified": {
        "sub": "uid-u",
        "email": "u@gmail.com",
        "email_verified": False,
        "firebase": {"sign_in_provider": "password"},
    },
    "no-email": {"sub": "uid-phone", "firebase": {"sign_in_provider": "phone"}},
    "same-email-other-uid": {
        "sub": "uid-other",
        "email": "a@gmail.com",
        "email_verified": True,
        "firebase": {"sign_in_provider": "google.com"},
    },
}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("JWT_PRIVATE_KEY", key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode())
    monkeypatch.setenv("JWT_PUBLIC_KEY", key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode())
    for name in ("JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT_ID)

    def fake_verify(token, request, audience=None, clock_skew_in_seconds=0):
        assert audience == PROJECT_ID
        if token not in FAKE_TOKENS:
            raise ValueError("Token used too late")
        return FAKE_TOKENS[token]

    monkeypatch.setattr(firebase.id_token, "verify_firebase_token", fake_verify)


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _login(client, token):
    return client.post("/api/v1/users/auth/firebase", json={"id_token": token})


def _code(res):
    return res.json()["error"]["code"]


def test_lan_dau_dang_nhap_tao_user_citizen(client, db):
    res = _login(client, "google-a")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["is_new_user"] is True
    assert data["token_type"] == "Bearer" and data["expires_in"] == 900
    assert data["user"]["email"] == "a@gmail.com"
    assert data["user"]["full_name"] == "Nguyễn Văn A"
    assert data["user"]["role"] == "citizen"
    assert decode_access_token(data["access_token"]).id == data["user"]["id"]
    saved = users.get_by_firebase_uid(db, "uid-a")
    assert saved.auth_provider == "google"


def test_dang_nhap_lan_hai_khong_tao_user_moi(client):
    first = _login(client, "google-a").json()["data"]
    second = _login(client, "google-a").json()["data"]
    assert second["is_new_user"] is False
    assert second["user"]["id"] == first["user"]["id"]


def test_refresh_token_tu_login_dung_duoc(client):
    refresh = _login(client, "google-a").json()["data"]["refresh_token"]
    res = client.post("/api/v1/users/auth/refresh", json={"refresh_token": refresh})
    assert res.status_code == 200


def test_token_firebase_sai(client):
    res = _login(client, "token-gia")
    assert res.status_code == 401
    assert _code(res) == "INVALID_FIREBASE_TOKEN"


def test_token_khong_co_email(client):
    res = _login(client, "no-email")
    assert res.status_code == 401
    assert _code(res) == "INVALID_FIREBASE_TOKEN"


def test_email_password_da_xac_thuc_dang_nhap_duoc(client, db):
    assert _login(client, "password-verified").status_code == 200
    assert users.get_by_firebase_uid(db, "uid-p").auth_provider == "password"


def test_email_password_chua_xac_thuc_van_dang_nhap_duoc_mac_dinh(client, monkeypatch):
    monkeypatch.delenv("REQUIRE_EMAIL_VERIFICATION", raising=False)
    res = _login(client, "password-unverified")
    assert res.status_code == 200
    assert res.json()["data"]["user"]["role"] == "citizen"


def test_email_password_chua_xac_thuc_bi_chan_khi_bat_cau_hinh(client, monkeypatch):
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    res = _login(client, "password-unverified")
    assert res.status_code == 403
    assert _code(res) == "EMAIL_NOT_VERIFIED"


def test_tai_khoan_bi_khoa(client, db):
    _login(client, "google-a")
    user = users.get_by_firebase_uid(db, "uid-a")
    user.status = UserStatus.locked
    db.commit()
    res = _login(client, "google-a")
    assert res.status_code == 403
    assert _code(res) == "ACCOUNT_LOCKED"


def test_email_da_thuoc_tai_khoan_khac(client):
    _login(client, "google-a")
    res = _login(client, "same-email-other-uid")
    assert res.status_code == 409
    assert _code(res) == "EMAIL_IN_USE"


def _register(client, email):
    body = {"email": email, "phone": "0912345678", "password": "matkhau123", "confirm_password": "matkhau123"}
    res = client.post("/api/v1/users/auth/register", json=body)
    assert res.status_code == 201
    return res.json()["data"]


def test_google_cung_email_lien_ket_vao_tai_khoan_dang_ky_form(client, db):
    form = _register(client, "a@gmail.com")
    res = _login(client, "google-a")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["is_new_user"] is False
    assert data["user"]["id"] == form["user"]["id"]

    user = users.get_by_email(db, "a@gmail.com")
    assert user.firebase_uid == "uid-a"
    # Chống chiếm trước tài khoản: mật khẩu và phiên do người đăng ký form tạo không còn dùng được.
    assert user.password_hash is None
    login = client.post("/api/v1/users/auth/login", json={"username": "a@gmail.com", "password": "matkhau123"})
    assert login.status_code == 401
    old = client.post("/api/v1/users/auth/refresh", json={"refresh_token": form["refresh_token"]})
    assert old.status_code == 401


def test_email_chua_xac_thuc_khong_lien_ket(client, db):
    _register(client, "u@gmail.com")
    res = _login(client, "password-unverified")
    assert res.status_code == 409
    assert _code(res) == "EMAIL_IN_USE"
    assert users.get_by_email(db, "u@gmail.com").password_hash is not None


def test_body_thieu_id_token(client):
    res = client.post("/api/v1/users/auth/firebase", json={})
    assert res.status_code == 422
    assert _code(res) == "VALIDATION_ERROR"


def test_thieu_firebase_project_id_la_loi_server(client, monkeypatch):
    monkeypatch.delenv("FIREBASE_PROJECT_ID")
    res = _login(client, "google-a")
    assert res.status_code == 500
    assert _code(res) == "INTERNAL_ERROR"


def test_firebase_verify_allows_clock_skew(monkeypatch):
    seen = {}

    def fake_verify(token, request, audience=None, clock_skew_in_seconds=0):
        seen["skew"] = clock_skew_in_seconds
        return FAKE_TOKENS["google-a"]

    monkeypatch.setattr(firebase.id_token, "verify_firebase_token", fake_verify)
    monkeypatch.delenv("FIREBASE_CLOCK_SKEW_SECONDS", raising=False)
    firebase.verify_id_token("any")
    assert seen["skew"] == 10

    monkeypatch.setenv("FIREBASE_CLOCK_SKEW_SECONDS", "999")
    firebase.verify_id_token("any")
    assert seen["skew"] == 60
