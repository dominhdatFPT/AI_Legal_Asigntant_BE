"""Hồ sơ cá nhân theo CCCD: GET/PUT /users/me/citizen-profile và API nội bộ."""

import copy

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models import CitizenProfile, UserStatus
from app.repositories import user_repository as users
from app.services.auth_service import issue_token_pair

URL = "/api/v1/users/me/citizen-profile"
INTERNAL = "/internal/v1/users/{}/citizen-profile"

FORM = {
    "id_number": "001095012345",
    "full_name": "NGUYỄN VĂN AN",
    "date_of_birth": "1995-08-15",
    "gender": "Nam",
    "nationality": "Việt Nam",
    "issue_date": "2021-09-20",
    "issue_place": "Cục Cảnh sát QLHC về TTXH",
    "expiry_date": "2035-08-15",
    "place_of_origin": "Xã Hải Châu, Huyện Hải Hậu, Tỉnh Nam Định",
    "place_of_residence": "Số 45 ngõ 120 Hoàng Hoa Thám, Phường Thụy Khuê, Quận Tây Hồ, Hà Nội",
    "permanent_address": {"detail": "Số 45 ngõ 120 Hoàng Hoa Thám", "ward": "Phường Ngọc Hà",
                          "province": "Thành phố Hà Nội"},
    "current_same_as_permanent": True,
    "current_address": None,
    "phone": "0912345678",
    "ethnicity": "Kinh",
    "tax_code": None,
    "source": "ekyc",
    "ekyc_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "declared": True,
    "consented": True,
}


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
    monkeypatch.setenv("INTERNAL_TOKEN", "internal-secret")


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _login(db, email="a@gmail.com", uid="fb-1", name="Tên Google"):
    user = users.create(db, firebase_uid=uid, email=email, full_name=name, auth_provider="google")
    pair = issue_token_pair(db, user)
    db.commit()
    return user, {"Authorization": f"Bearer {pair['access_token']}"}


@pytest.fixture
def me(db):
    return _login(db)


def _form(**changes):
    data = copy.deepcopy(FORM)
    data.update(changes)
    return data


def _code(res):
    return res.json()["error"]["code"]


# ---------- GET ----------

def test_chua_khai_ho_so_thi_data_null(client, me):
    _, headers = me
    res = client.get(URL, headers=headers)
    assert res.status_code == 200
    assert res.json()["data"] is None


def test_chua_dang_nhap_tra_401(client):
    assert client.get(URL).status_code == 401
    assert client.put(URL, json=FORM).status_code == 401


# ---------- PUT: lưu mới ----------

def test_luu_ho_so_lan_dau(client, db, me):
    user, headers = me
    res = client.put(URL, json=FORM, headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["user_id"] == str(user.id)
    assert data["id_number"] == "001095012345"
    assert data["date_of_birth"] == "1995-08-15"
    assert data["permanent_address"]["ward"] == "Phường Ngọc Hà"
    assert data["current_address"] is None
    assert data["email"] == "a@gmail.com"          # chỉ để hiển thị
    assert data["phone"] == "0912345678"
    assert data["source"] == "ekyc" and data["ekyc_id"] == FORM["ekyc_id"]
    assert data["declared_at"] and data["consented_at"]
    # GET trả lại đúng hồ sơ vừa lưu
    assert client.get(URL, headers=headers).json()["data"]["id_number"] == "001095012345"


def test_luu_ho_so_doi_ten_tai_khoan_va_luu_sdt(client, db, me):
    user, headers = me
    client.put(URL, json=FORM, headers=headers)
    db.refresh(user)
    assert user.full_name == "NGUYỄN VĂN AN"
    assert user.phone == "0912345678"
    me_res = client.get("/api/v1/users/me", headers=headers).json()["data"]
    assert me_res["full_name"] == "NGUYỄN VĂN AN"


def test_noi_o_hien_tai_khac_thuong_tru(client, me):
    _, headers = me
    current = {"detail": "12 Trần Phú", "ward": "Phường Hà Đông", "province": "Thành phố Hà Nội"}
    res = client.put(URL, json=_form(current_same_as_permanent=False, current_address=current), headers=headers)
    assert res.json()["data"]["current_address"] == current


def test_tick_giong_thuong_tru_thi_bo_qua_dia_chi_hien_tai(client, me):
    _, headers = me
    current = {"detail": "12 Trần Phú", "ward": "Phường Hà Đông", "province": "Thành phố Hà Nội"}
    res = client.put(URL, json=_form(current_same_as_permanent=True, current_address=current), headers=headers)
    assert res.json()["data"]["current_address"] is None


def test_nhap_tay_thi_khong_luu_ekyc_id(client, me):
    _, headers = me
    res = client.put(URL, json=_form(source="manual"), headers=headers)
    data = res.json()["data"]
    assert data["source"] == "manual" and data["ekyc_id"] is None


# ---------- PUT: sửa ----------

def test_luu_lan_hai_thi_ghi_de_khong_tao_ban_moi(client, db, me):
    user, headers = me
    client.put(URL, json=FORM, headers=headers)
    res = client.put(URL, json=_form(ethnicity="Tày", tax_code="8001234567"), headers=headers)
    assert res.status_code == 200
    assert res.json()["data"]["ethnicity"] == "Tày"
    assert db.query(CitizenProfile).count() == 1


def test_luu_lai_cung_so_cccd_cua_minh_khong_bao_trung(client, me):
    _, headers = me
    client.put(URL, json=FORM, headers=headers)
    assert client.put(URL, json=FORM, headers=headers).status_code == 200


# ---------- PUT: lỗi ----------

def test_cccd_da_thuoc_tai_khoan_khac_tra_409(client, db, me):
    _, headers = me
    client.put(URL, json=FORM, headers=headers)
    other, other_headers = _login(db, email="b@gmail.com", uid="fb-2", name="Người khác")
    res = client.put(URL, json=FORM, headers=other_headers)
    assert res.status_code == 409
    assert _code(res) == "CCCD_ALREADY_USED"
    db.refresh(other)
    assert other.full_name == "Người khác"   # lỗi thì không đổi tên tài khoản


def test_so_dien_thoai_da_thuoc_tai_khoan_khac_tra_409(client, db, me):
    _, headers = me
    client.put(URL, json=FORM, headers=headers)
    _, other_headers = _login(db, email="b@gmail.com", uid="fb-2", name="Người khác")
    res = client.put(URL, json=_form(id_number="079099000999"), headers=other_headers)
    assert res.status_code == 409
    assert _code(res) == "PHONE_IN_USE"


@pytest.mark.parametrize("field", ["declared", "consented"])
def test_chua_tick_xac_nhan_tra_422(client, db, me, field):
    user, headers = me
    res = client.put(URL, json=_form(**{field: False}), headers=headers)
    assert res.status_code == 422
    assert _code(res) == "VALIDATION_ERROR"
    assert db.query(CitizenProfile).count() == 0


@pytest.mark.parametrize("field", ["id_number", "full_name", "date_of_birth", "permanent_address", "phone"])
def test_thieu_truong_bat_buoc_tra_422(client, me, field):
    _, headers = me
    data = _form()
    data.pop(field)
    res = client.put(URL, json=data, headers=headers)
    assert res.status_code == 422
    assert _code(res) == "VALIDATION_ERROR"


def test_tai_khoan_bi_khoa_tra_403(client, db, me):
    user, headers = me
    user.status = UserStatus.locked
    db.commit()
    assert _code(client.put(URL, json=FORM, headers=headers)) == "ACCOUNT_LOCKED"
    assert client.get(URL, headers=headers).status_code == 403


# ---------- API nội bộ ----------

def test_internal_lay_ho_so_bang_token_noi_bo(client, me):
    user, headers = me
    client.put(URL, json=FORM, headers=headers)
    res = client.get(INTERNAL.format(user.id), headers={"X-Internal-Token": "internal-secret"})
    assert res.status_code == 200
    assert res.json()["data"]["id_number"] == "001095012345"


def test_internal_user_chua_khai_thi_data_null(client, me):
    user, _ = me
    res = client.get(INTERNAL.format(user.id), headers={"X-Internal-Token": "internal-secret"})
    assert res.status_code == 200 and res.json()["data"] is None


@pytest.mark.parametrize("token", [None, "sai-token"])
def test_internal_sai_hoac_thieu_token_tra_401(client, me, token):
    user, _ = me
    headers = {"X-Internal-Token": token} if token else {}
    assert client.get(INTERNAL.format(user.id), headers=headers).status_code == 401


def test_internal_chua_cau_hinh_internal_token_thi_chan_het(client, me, monkeypatch):
    user, _ = me
    monkeypatch.delenv("INTERNAL_TOKEN")
    res = client.get(INTERNAL.format(user.id), headers={"X-Internal-Token": ""})
    assert res.status_code == 401


def test_internal_user_khong_ton_tai_tra_404(client):
    res = client.get(INTERNAL.format("5b1e2c3d-4f5a-4b6c-8d7e-9f0a1b2c3d4e"),
                     headers={"X-Internal-Token": "internal-secret"})
    assert res.status_code == 404
    assert _code(res) == "USER_NOT_FOUND"


def test_internal_khong_dung_bearer_cua_user_duoc(client, me):
    user, headers = me
    assert client.get(INTERNAL.format(user.id), headers=headers).status_code == 401
