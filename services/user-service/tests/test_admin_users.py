"""Quản lý tài khoản (admin): danh sách, tạo, khóa / mở khóa, đổi vai trò, xóa mềm / khôi phục, phân quyền."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.clients import consultation_client
from app.core import firebase
from app.core.database import get_db
from app.core.passwords import hash_password
from app.core.security import create_access_token
from app.main import app
from app.models import CitizenProfile, User, UserRole, UserStatus
from app.services import admin_user_service

USERS = "/api/v1/users"
AUTH = "/api/v1/users/auth"
PROJECT_ID = "ai-legal-assistant-7875a"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for name in ("JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JWT_PRIVATE_KEY", key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode())
    monkeypatch.setenv("JWT_PUBLIC_KEY", key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode())
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJECT_ID)


@pytest.fixture
def closed(monkeypatch):
    """Ghi lại các lần gọi đóng cuộc tư vấn của luật sư (Consultation Service chưa có, chỉ kiểm tra có gọi)."""
    calls = []
    monkeypatch.setattr(admin_user_service.consultation_client, "close_lawyer_consultations",
                        lambda lawyer_id, reason: calls.append((lawyer_id, reason)))
    return calls


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _make(db, username, role=UserRole.citizen, **kw):
    u = User(email=f"{username}@ai-legal.local", username=username, password_hash=hash_password("12345678"),
             full_name=kw.pop("full_name", username), auth_provider="local", role=role, **kw)
    db.add(u)
    db.commit()
    return u


def _h(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id), user.role.value)}"}


def _code(res):
    return res.json()["error"]["code"]


def _login(client, username, password="12345678"):
    return client.post(f"{AUTH}/login", json={"username": username, "password": password})


@pytest.fixture
def admin(db):
    return _make(db, "admin2026", UserRole.admin, full_name="Quản trị viên")


@pytest.fixture
def citizen(db):
    return _make(db, "nguyenvana", full_name="Nguyễn Văn A", phone="0912345678")


@pytest.fixture
def lawyer(db):
    return _make(db, "luatsub", UserRole.lawyer, full_name="Trần Thị B")


# ---------------------------------------------------------------- Phân quyền


@pytest.mark.parametrize("method,path", [
    ("get", ""), ("get", "/summary"), ("post", ""), ("put", "/{id}/role"), ("post", "/{id}/lock"),
    ("post", "/{id}/unlock"), ("delete", "/{id}"), ("post", "/{id}/restore"),
])
def test_khong_co_token_bi_401(client, citizen, method, path):
    res = client.request(method.upper(), USERS + path.format(id=citizen.id))
    assert res.status_code == 401


@pytest.mark.parametrize("who", ["citizen", "lawyer"])
@pytest.mark.parametrize("method,path,body", [
    ("get", "", None), ("get", "/summary", None),
    ("post", "", {"email": "x@gmail.com", "password": "12345678"}),
    ("put", "/{id}/role", {"role": "admin"}), ("post", "/{id}/lock", {"reason": "spam"}),
    ("post", "/{id}/unlock", None), ("delete", "/{id}", None), ("post", "/{id}/restore", None),
])
def test_citizen_lawyer_goi_api_admin_bi_403(client, admin, citizen, lawyer, who, method, path, body):
    caller = citizen if who == "citizen" else lawyer
    res = client.request(method.upper(), USERS + path.format(id=admin.id), json=body, headers=_h(caller))
    assert res.status_code == 403
    assert _code(res) == "FORBIDDEN"


def test_admin_bi_ha_quyen_thi_token_cu_mat_quyen_ngay(client, db, admin):
    headers = _h(admin)  # token vẫn ghi role admin
    admin.role = UserRole.citizen
    db.commit()
    assert client.get(USERS, headers=headers).status_code == 403


def test_admin_bi_khoa_thi_token_cu_bi_chan_ngay(client, db, admin):
    headers = _h(admin)
    admin.status = UserStatus.locked
    db.commit()
    res = client.get(USERS, headers=headers)
    assert res.status_code == 403
    assert _code(res) == "ACCOUNT_LOCKED"


def test_route_tinh_khong_bi_route_user_id_nuot(client, citizen):
    assert client.get(f"{USERS}/health").status_code == 200
    assert client.get(f"{USERS}/me", headers=_h(citizen)).status_code == 200


# ---------------------------------------------------------------- Danh sách, đếm, chi tiết


def test_danh_sach_co_phan_trang_va_loc(client, admin, citizen, lawyer):
    res = client.get(USERS, headers=_h(admin), params={"page_size": 2})
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["pagination"] == {"page": 1, "page_size": 2, "total": 3, "total_pages": 2}
    assert len(data["items"]) == 2

    res = client.get(USERS, headers=_h(admin), params={"role": "lawyer"})
    assert [u["id"] for u in res.json()["data"]["items"]] == [str(lawyer.id)]


@pytest.mark.parametrize("q", ["nguyễn văn", "NGUYENVANA@ai-legal", "0912345"])
def test_tim_theo_ten_email_sdt(client, admin, citizen, lawyer, q):
    items = client.get(USERS, headers=_h(admin), params={"q": q}).json()["data"]["items"]
    assert [u["id"] for u in items] == [str(citizen.id)]


def test_danh_sach_bao_da_khai_ho_so_chua(client, db, admin, citizen):
    now = datetime.now(timezone.utc)
    db.add(CitizenProfile(user_id=citizen.id, id_number="001095012345", full_name="NGUYỄN VĂN A",
                          date_of_birth=datetime(1995, 1, 1).date(), gender="Nam", issue_date=now.date(),
                          issue_place="Cục CS", permanent_detail="1", permanent_ward="P", permanent_province="HN",
                          declared_at=now, consented_at=now))
    db.commit()
    items = client.get(USERS, headers=_h(admin)).json()["data"]["items"]
    flags = {u["id"]: u["has_citizen_profile"] for u in items}
    assert flags == {str(citizen.id): True, str(admin.id): False}


def test_summary_dem_cho_cac_tab(client, db, admin, citizen, lawyer):
    _make(db, "bikhoa", status=UserStatus.locked)
    _make(db, "daxoa", deleted_at=datetime.now(timezone.utc))
    data = client.get(f"{USERS}/summary", headers=_h(admin)).json()["data"]
    assert data == {
        "total": 4,
        "by_role": {"citizen": 2, "lawyer": 1, "admin": 1},
        "by_status": {"active": 3, "locked": 1},
        "deleted": 1,
    }


def test_chi_tiet_admin_xem_moi_nguoi_nguoi_khac_chi_xem_minh(client, admin, citizen, lawyer):
    res = client.get(f"{USERS}/{citizen.id}", headers=_h(admin))
    assert res.status_code == 200
    assert res.json()["data"]["created_as_lawyer"] is False  # admin thấy thêm trường quản trị

    assert client.get(f"{USERS}/{citizen.id}", headers=_h(citizen)).status_code == 200
    res = client.get(f"{USERS}/{lawyer.id}", headers=_h(citizen))
    assert res.status_code == 403
    assert client.get(f"{USERS}/{uuid.uuid4()}", headers=_h(admin)).status_code == 404


# ---------------------------------------------------------------- Tạo tài khoản


@pytest.mark.parametrize("account_type,role,as_lawyer", [
    ("citizen", "citizen", False), ("lawyer", "citizen", True), ("admin", "admin", False),
])
def test_admin_tao_tai_khoan_va_dang_nhap_bang_email(client, admin, account_type, role, as_lawyer):
    body = {"email": "LuatSu.C@Gmail.com", "password": "matkhau123", "full_name": "Lê Văn C",
            "account_type": account_type}
    res = client.post(USERS, json=body, headers=_h(admin))
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["email"] == "luatsu.c@gmail.com"
    assert data["role"] == role
    assert data["created_as_lawyer"] is as_lawyer
    assert data["created_by"] == str(admin.id)
    assert "password" not in data and "password_hash" not in data

    login = _login(client, "luatsu.c@gmail.com", "matkhau123")
    assert login.status_code == 200
    assert login.json()["data"]["user"]["role"] == role


def test_tao_tai_khoan_trung_email(client, db, admin, citizen):
    res = client.post(USERS, json={"email": citizen.email, "password": "12345678"}, headers=_h(admin))
    assert res.status_code == 409
    assert _code(res) == "EMAIL_IN_USE"

    citizen.deleted_at = datetime.now(timezone.utc)
    db.commit()
    res = client.post(USERS, json={"email": citizen.email, "password": "12345678"}, headers=_h(admin))
    assert res.status_code == 409
    assert _code(res) == "EMAIL_DELETED"


@pytest.mark.parametrize("body", [
    {"email": "khong-phai-email", "password": "12345678"},
    {"email": "a@gmail.com", "password": "ngan"},
    {"email": "a@gmail.com", "password": "12345678", "role": "admin"},  # không nhận trường lạ
    {"email": "a@gmail.com", "password": "12345678", "account_type": "superadmin"},
])
def test_tao_tai_khoan_du_lieu_sai(client, admin, body):
    res = client.post(USERS, json=body, headers=_h(admin))
    assert res.status_code == 422


# ---------------------------------------------------------------- Khóa / mở khóa


def test_khoa_co_thoi_han_dang_xuat_ngay_va_hien_ly_do(client, admin, citizen):
    tokens = _login(client, "nguyenvana").json()["data"]
    client.cookies.clear()
    res = client.post(f"{USERS}/{citizen.id}/lock", json={"reason": "Spam nhiều lần", "duration_days": 7},
                      headers=_h(admin))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["status"] == "locked"
    assert data["locked_reason"] == "Spam nhiều lần"
    assert data["locked_by"] == str(admin.id)
    assert data["locked_until"] is not None

    # Token cũ bị chặn ngay ở User Service, refresh token bị thu hồi, đăng nhập lại thấy lý do
    me = client.get(f"{USERS}/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 403
    assert "Spam nhiều lần" in me.json()["error"]["message"]
    refresh = client.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code in (401, 403)
    login = _login(client, "nguyenvana")
    assert login.status_code == 403
    assert _code(login) == "ACCOUNT_LOCKED"
    assert " đến " in login.json()["error"]["message"]


def test_khoa_vinh_vien_khi_khong_gui_thoi_han(client, admin, citizen):
    res = client.post(f"{USERS}/{citizen.id}/lock", json={"reason": "Giả mạo"}, headers=_h(admin))
    assert res.json()["data"]["locked_until"] is None
    assert "vĩnh viễn" in _login(client, "nguyenvana").json()["error"]["message"]


def test_het_han_khoa_thi_tu_mo(client, db, admin, citizen):
    citizen.status = UserStatus.locked
    citizen.locked_reason = "Tạm khóa"
    citizen.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    assert _login(client, "nguyenvana").status_code == 200
    db.refresh(citizen)
    assert citizen.status == UserStatus.active
    assert citizen.locked_reason is None


def test_danh_sach_tu_mo_tai_khoan_het_han_khoa(client, db, admin, citizen):
    citizen.status = UserStatus.locked
    citizen.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    items = client.get(USERS, headers=_h(admin), params={"status": "locked"}).json()["data"]["items"]
    assert items == []


@pytest.mark.parametrize("body", [{}, {"reason": "   "}, {"reason": "x", "duration_days": 0}])
def test_khoa_bat_buoc_ly_do_hop_le(client, admin, citizen, body):
    assert client.post(f"{USERS}/{citizen.id}/lock", json=body, headers=_h(admin)).status_code == 422


def test_khong_tu_khoa_minh_va_khong_khoa_hai_lan(client, admin, citizen):
    res = client.post(f"{USERS}/{admin.id}/lock", json={"reason": "x"}, headers=_h(admin))
    assert (res.status_code, _code(res)) == (409, "CANNOT_LOCK_SELF")

    client.post(f"{USERS}/{citizen.id}/lock", json={"reason": "x"}, headers=_h(admin))
    res = client.post(f"{USERS}/{citizen.id}/lock", json={"reason": "x"}, headers=_h(admin))
    assert (res.status_code, _code(res)) == (409, "USER_ALREADY_LOCKED")


def test_khoa_luat_su_thi_ket_thuc_cuoc_tu_van(client, admin, lawyer, citizen, closed):
    client.post(f"{USERS}/{citizen.id}/lock", json={"reason": "x"}, headers=_h(admin))
    assert closed == []
    client.post(f"{USERS}/{lawyer.id}/lock", json={"reason": "x"}, headers=_h(admin))
    assert closed == [(lawyer.id, "locked")]


def test_admin_khoa_duoc_admin_khac(client, db, admin):
    other = _make(db, "admin2", UserRole.admin)
    assert client.post(f"{USERS}/{other.id}/lock", json={"reason": "x"}, headers=_h(admin)).status_code == 200


def test_mo_khoa(client, admin, citizen):
    res = client.post(f"{USERS}/{citizen.id}/unlock", headers=_h(admin))
    assert (res.status_code, _code(res)) == (409, "USER_NOT_LOCKED")

    client.post(f"{USERS}/{citizen.id}/lock", json={"reason": "x"}, headers=_h(admin))
    res = client.post(f"{USERS}/{citizen.id}/unlock", headers=_h(admin))
    assert res.status_code == 200
    data = res.json()["data"]
    assert (data["status"], data["locked_reason"], data["locked_until"]) == ("active", None, None)
    assert _login(client, "nguyenvana").status_code == 200


# ---------------------------------------------------------------- Đổi vai trò


def test_doi_vai_tro_thu_hoi_token(client, admin, citizen):
    tokens = _login(client, "nguyenvana").json()["data"]
    client.cookies.clear()
    res = client.put(f"{USERS}/{citizen.id}/role", json={"role": "lawyer"}, headers=_h(admin))
    assert res.status_code == 200
    assert res.json()["data"]["role"] == "lawyer"
    assert client.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert _login(client, "nguyenvana").json()["data"]["user"]["role"] == "lawyer"


def test_ha_vai_tro_luat_su_thi_ket_thuc_cuoc_tu_van(client, admin, lawyer, closed):
    client.put(f"{USERS}/{lawyer.id}/role", json={"role": "citizen"}, headers=_h(admin))
    assert closed == [(lawyer.id, "role_changed")]


def test_khong_tu_doi_vai_tro(client, admin):
    res = client.put(f"{USERS}/{admin.id}/role", json={"role": "citizen"}, headers=_h(admin))
    assert (res.status_code, _code(res)) == (409, "CANNOT_CHANGE_OWN_ROLE")


def test_doi_vai_tro_sai_gia_tri(client, admin, citizen):
    assert client.put(f"{USERS}/{citizen.id}/role", json={"role": "boss"}, headers=_h(admin)).status_code == 422


# ---------------------------------------------------------------- Xóa mềm / khôi phục


def test_xoa_mem_va_khoi_phuc(client, admin, citizen):
    headers_citizen = _h(citizen)
    res = client.delete(f"{USERS}/{citizen.id}", headers=_h(admin))
    assert res.status_code == 204
    assert res.content == b""

    # Đăng xuất ngay, không đăng nhập được, không còn trong danh sách mặc định
    assert client.get(f"{USERS}/me", headers=headers_citizen).status_code == 401
    assert _login(client, "nguyenvana").status_code == 401
    ids = [u["id"] for u in client.get(USERS, headers=_h(admin)).json()["data"]["items"]]
    assert str(citizen.id) not in ids
    deleted = client.get(USERS, headers=_h(admin), params={"deleted": True}).json()["data"]["items"]
    assert [u["id"] for u in deleted] == [str(citizen.id)]
    assert deleted[0]["deleted_by"] == str(admin.id)

    # Thao tác khác trên tài khoản đã xóa bị chặn
    res = client.post(f"{USERS}/{citizen.id}/lock", json={"reason": "x"}, headers=_h(admin))
    assert (res.status_code, _code(res)) == (409, "USER_DELETED")

    res = client.post(f"{USERS}/{citizen.id}/restore", headers=_h(admin))
    assert res.status_code == 200
    assert res.json()["data"]["deleted_at"] is None
    assert _login(client, "nguyenvana").status_code == 200


def test_khong_tu_xoa_va_khoi_phuc_tai_khoan_chua_xoa(client, admin, citizen):
    res = client.delete(f"{USERS}/{admin.id}", headers=_h(admin))
    assert (res.status_code, _code(res)) == (409, "CANNOT_DELETE_SELF")
    res = client.post(f"{USERS}/{citizen.id}/restore", headers=_h(admin))
    assert (res.status_code, _code(res)) == (409, "USER_NOT_DELETED")


def test_xoa_luat_su_thi_ket_thuc_cuoc_tu_van(client, admin, lawyer, closed):
    client.delete(f"{USERS}/{lawyer.id}", headers=_h(admin))
    assert closed == [(lawyer.id, "deleted")]


def test_email_google_da_bi_xoa_khong_tu_dang_ky_lai(client, db, admin, monkeypatch):
    u = User(email="a@gmail.com", firebase_uid="uid-a", auth_provider="google.com",
             deleted_at=datetime.now(timezone.utc))
    db.add(u)
    db.commit()

    def fake_verify(token, request, audience=None, clock_skew_in_seconds=0):
        return {"sub": "uid-a", "email": "a@gmail.com", "email_verified": True,
                "firebase": {"sign_in_provider": "google.com"}}

    monkeypatch.setattr(firebase.id_token, "verify_firebase_token", fake_verify)
    res = client.post(f"{AUTH}/firebase", json={"id_token": "google-a"})
    assert res.status_code == 403
    assert _code(res) == "ACCOUNT_DELETED"


def test_ham_dong_tu_van_tam_thoi_khong_nem_loi():
    consultation_client.close_lawyer_consultations(uuid.uuid4(), reason="locked")


# ---------------------------------------------------------------- Bổ sung cho màn hình quản lý


def test_tao_tai_khoan_kem_so_dien_thoai(client, admin):
    body = {"email": "c@gmail.com", "password": "12345678", "phone": "0912.345.678"}
    res = client.post(USERS, json=body, headers=_h(admin))
    assert res.status_code == 201
    assert res.json()["data"]["phone"] == "0912345678"
    body = {"email": "d@gmail.com", "password": "12345678", "phone": "12345"}
    assert client.post(USERS, json=body, headers=_h(admin)).status_code == 422


def test_tao_tai_khoan_trung_so_dien_thoai(client, admin, citizen):
    body = {"email": "c@gmail.com", "password": "12345678", "phone": citizen.phone}
    res = client.post(USERS, json=body, headers=_h(admin))
    assert res.status_code == 409
    assert _code(res) == "PHONE_IN_USE"


def test_admin_xem_ho_so_cccd_cua_nguoi_khac(client, db, admin, citizen):
    url = f"{USERS}/{citizen.id}/citizen-profile"
    res = client.get(url, headers=_h(admin))
    assert res.status_code == 200
    assert res.json()["data"] is None  # chưa khai hồ sơ

    now = datetime.now(timezone.utc)
    db.add(CitizenProfile(user_id=citizen.id, id_number="001095012345", full_name="NGUYỄN VĂN A",
                          date_of_birth=datetime(1995, 1, 1).date(), gender="Nam", issue_date=now.date(),
                          issue_place="Cục CS", permanent_detail="1", permanent_ward="P", permanent_province="HN",
                          declared_at=now, consented_at=now))
    db.commit()
    data = client.get(url, headers=_h(admin)).json()["data"]
    assert data["id_number"] == "001095012345"

    assert client.get(url, headers=_h(citizen)).status_code == 403
    assert client.get(f"{USERS}/{uuid.uuid4()}/citizen-profile", headers=_h(admin)).status_code == 404
