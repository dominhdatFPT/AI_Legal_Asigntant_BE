"""Chức năng Luật sư phía User Service: API nội bộ cho Lawyer Service (tạo tài khoản, xóa cứng bù trừ, đổi vai trò
khi duyệt đơn), refresh trả ROLE_CHANGED_RELOGIN, và client gọi Lawyer Service ẩn hồ sơ (httpx.MockTransport)."""

import uuid
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.clients import lawyer_client
from app.core.database import get_db
from app.core.passwords import hash_password
from app.core.responses import ApiError
from app.main import app
from app.models import CitizenProfile, User, UserRole

INTERNAL = "/internal/v1/users"
HEADERS = {"X-Internal-Token": "internal-secret"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for name in ("JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_PRIVATE_KEY", key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
    monkeypatch.setenv("JWT_PUBLIC_KEY", key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    monkeypatch.setenv("INTERNAL_TOKEN", "internal-secret")
    monkeypatch.setenv("LAWYER_SERVICE_URL", "http://lawyer-service:8000")


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _make(db, username, role=UserRole.citizen, **kw):
    u = User(email=f"{username}@ai-legal.local", username=username, password_hash=hash_password("12345678"),
             full_name=username, auth_provider="local", role=role, **kw)
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def admin(db):
    return _make(db, "admin2026", UserRole.admin)


def _code(res):
    return res.json()["error"]["code"]


def _body(admin, **kw):
    return {"email": "LuatSu.C@gmail.com", "password": "matkhau123", "full_name": "Lê Văn C", "role": "lawyer",
            "created_by": str(admin.id), **kw}


def _login(client, username, password="12345678"):
    return client.post("/api/v1/users/auth/login", json={"username": username, "password": password})


# ---------------------------------------------------------------- POST /internal/v1/users
def test_tao_tai_khoan_luat_su_vai_tro_lawyer_ngay(client, admin):
    res = client.post(INTERNAL, headers=HEADERS, json=_body(admin))
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    assert (data["role"], data["email"], data["created_by"]) == ("lawyer", "luatsu.c@gmail.com", str(admin.id))
    assert "password_hash" not in data
    assert _login(client, "luatsu.c@gmail.com", "matkhau123").json()["data"]["user"]["role"] == "lawyer"


def test_tao_tai_khoan_can_internal_token(client, admin):
    assert client.post(INTERNAL, json=_body(admin)).status_code == 401
    assert client.post(INTERNAL, headers={"X-Internal-Token": "sai"}, json=_body(admin)).status_code == 401


@pytest.mark.parametrize("override", [{"role": "citizen"}, {"role": "admin"}, {"full_name": ""}, {"password": "1"},
                                      {"email": "x"}, {"created_by": "abc"}, {"extra": 1}])
def test_tao_tai_khoan_du_lieu_sai(client, admin, override):
    assert client.post(INTERNAL, headers=HEADERS, json=_body(admin, **override)).status_code == 422


def test_created_by_phai_la_admin(client, db, admin):
    citizen = _make(db, "nguyenvana")
    res = client.post(INTERNAL, headers=HEADERS, json=_body(admin, created_by=str(citizen.id)))
    assert (res.status_code, _code(res)) == (422, "VALIDATION_ERROR")
    res = client.post(INTERNAL, headers=HEADERS, json=_body(admin, created_by=str(uuid.uuid4())))
    assert res.status_code == 422


def test_tao_tai_khoan_trung_email(client, admin):
    client.post(INTERNAL, headers=HEADERS, json=_body(admin))
    res = client.post(INTERNAL, headers=HEADERS, json=_body(admin))
    assert (res.status_code, _code(res)) == (409, "EMAIL_IN_USE")


# ---------------------------------------------------------------- DELETE /internal/v1/users/{id}
def test_xoa_cung_tai_khoan_vua_tao_thi_email_dung_lai_duoc(client, db, admin):
    user_id = client.post(INTERNAL, headers=HEADERS, json=_body(admin)).json()["data"]["id"]
    res = client.delete(f"{INTERNAL}/{user_id}", headers=HEADERS)
    assert res.status_code == 204 and res.content == b""
    assert db.get(User, uuid.UUID(user_id)) is None
    assert client.post(INTERNAL, headers=HEADERS, json=_body(admin)).status_code == 201
    assert client.delete(f"{INTERNAL}/{uuid.uuid4()}", headers=HEADERS).status_code == 404


def test_khong_xoa_cung_tai_khoan_da_dung(client, db, admin):
    user_id = client.post(INTERNAL, headers=HEADERS, json=_body(admin)).json()["data"]["id"]
    _login(client, "luatsu.c@gmail.com", "matkhau123")  # đã đăng nhập → có refresh token
    res = client.delete(f"{INTERNAL}/{user_id}", headers=HEADERS)
    assert (res.status_code, _code(res)) == (409, "HARD_DELETE_NOT_ALLOWED")
    self_registered = _make(db, "tudangky")  # created_by = NULL
    assert client.delete(f"{INTERNAL}/{self_registered.id}", headers=HEADERS).status_code == 409


def test_khong_xoa_cung_tai_khoan_da_khai_cccd(client, db, admin):
    user = _make(db, "luatsud", created_by=admin.id)
    now = datetime.now(timezone.utc)
    db.add(CitizenProfile(user_id=user.id, id_number="001095012345", full_name="A",
                          date_of_birth=now.date(), gender="Nam", issue_date=now.date(), issue_place="X",
                          permanent_detail="1", permanent_ward="P", permanent_province="T", declared_at=now,
                          consented_at=now))
    db.commit()
    assert client.delete(f"{INTERNAL}/{user.id}", headers=HEADERS).status_code == 409


def test_xoa_cung_can_token_va_id_hop_le(client):
    assert client.delete(f"{INTERNAL}/{uuid.uuid4()}").status_code == 401
    assert client.delete(f"{INTERNAL}/abc", headers=HEADERS).status_code == 422


# ---------------------------------------------------------------- PUT /internal/v1/users/{id}/role
def test_duyet_don_doi_vai_tro_va_refresh_bao_dang_nhap_lai(client, db):
    citizen = _make(db, "nguyenvana")
    tokens = _login(client, "nguyenvana").json()["data"]
    client.cookies.clear()
    res = client.put(f"{INTERNAL}/{citizen.id}/role", headers=HEADERS, json={"role": "lawyer"})
    assert res.status_code == 200 and res.json()["data"]["role"] == "lawyer"
    res = client.post("/api/v1/users/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert (res.status_code, _code(res)) == (401, "ROLE_CHANGED_RELOGIN")
    assert res.json()["error"]["message"] == "Tài khoản của bạn đã được cập nhật quyền, vui lòng đăng nhập lại"
    # Gọi lại khi đã là lawyer: không đổi gì.
    assert client.put(f"{INTERNAL}/{citizen.id}/role", headers=HEADERS, json={"role": "lawyer"}).status_code == 200


def test_token_dung_lai_binh_thuong_van_la_invalid(client, db):
    _make(db, "nguyenvana")
    first = _login(client, "nguyenvana").json()["data"]["refresh_token"]
    client.cookies.clear()
    assert client.post("/api/v1/users/auth/refresh", json={"refresh_token": first}).status_code == 200
    client.cookies.clear()
    res = client.post("/api/v1/users/auth/refresh", json={"refresh_token": first})
    assert (res.status_code, _code(res)) == (401, "INVALID_REFRESH_TOKEN")


def test_doi_vai_tro_noi_bo_cac_truong_hop_loi(client, db, admin):
    deleted = _make(db, "daxoa", deleted_at=datetime.now(timezone.utc))
    res = client.put(f"{INTERNAL}/{admin.id}/role", headers=HEADERS, json={"role": "lawyer"})
    assert (res.status_code, _code(res)) == (409, "ROLE_CHANGE_NOT_ALLOWED")
    res = client.put(f"{INTERNAL}/{deleted.id}/role", headers=HEADERS, json={"role": "lawyer"})
    assert (res.status_code, _code(res)) == (409, "USER_DELETED")
    assert client.put(f"{INTERNAL}/{uuid.uuid4()}/role", headers=HEADERS, json={"role": "lawyer"}).status_code == 404
    assert client.put(f"{INTERNAL}/{admin.id}/role", headers=HEADERS, json={"role": "admin"}).status_code == 422
    assert client.put(f"{INTERNAL}/{admin.id}/role", json={"role": "lawyer"}).status_code == 401


# ---------------------------------------------------------------- lawyer_client.hide_lawyer_profile
def _transport(monkeypatch, handler):
    monkeypatch.setattr(lawyer_client, "transport", httpx.MockTransport(handler))


def test_goi_lawyer_service_an_ho_so(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": {"is_hidden": True}, "error": None, "request_id": "r"})

    _transport(monkeypatch, handler)
    user_id = uuid.uuid4()
    lawyer_client.hide_lawyer_profile(user_id, "deleted", "req-1")
    request = seen[0]
    assert request.url.path == f"/internal/v1/lawyers/{user_id}/hide"
    assert request.headers["X-Internal-Token"] == "internal-secret" and request.headers["X-Request-ID"] == "req-1"
    assert request.content == b'{"reason":"deleted"}'


@pytest.mark.parametrize("behavior,status,code", [
    ("timeout", 504, "LAWYER_TIMEOUT"), ("down", 503, "LAWYER_UNAVAILABLE"), ("500", 502, "LAWYER_BAD_RESPONSE"),
    ("401", 502, "LAWYER_BAD_RESPONSE"), ("html", 502, "LAWYER_BAD_RESPONSE"),
])
def test_lawyer_service_loi(monkeypatch, behavior, status, code):
    def handler(request):
        if behavior == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if behavior == "down":
            raise httpx.ConnectError("down", request=request)
        if behavior == "html":
            return httpx.Response(200, text="<html>")
        return httpx.Response(int(behavior), json={"data": None, "error": {"code": "X", "message": "x"}})

    _transport(monkeypatch, handler)
    with pytest.raises(ApiError) as caught:
        lawyer_client.hide_lawyer_profile(uuid.uuid4(), "role_changed")
    assert (caught.value.status, caught.value.code) == (status, code)


def test_thieu_cau_hinh_lawyer_service(monkeypatch):
    monkeypatch.delenv("LAWYER_SERVICE_URL")
    with pytest.raises(ApiError) as caught:
        lawyer_client.hide_lawyer_profile(uuid.uuid4(), "deleted")
    assert caught.value.status == 503


def test_ha_vai_tro_goi_http_that_toi_lawyer_service(client, db, admin, monkeypatch):
    """Đầu-cuối trong User Service: API admin → lawyer_client → (MockTransport) Lawyer Service."""
    lawyer = _make(db, "luatsub", UserRole.lawyer)
    _transport(monkeypatch, lambda request: httpx.Response(503, text="down"))
    from app.core.security import create_access_token
    headers = {"Authorization": f"Bearer {create_access_token(str(admin.id), 'admin')}"}
    res = client.put(f"/api/v1/users/{lawyer.id}/role", headers=headers, json={"role": "citizen"})
    assert (res.status_code, _code(res)) == (502, "LAWYER_BAD_RESPONSE")
    db.refresh(lawyer)
    assert lawyer.role == UserRole.lawyer
