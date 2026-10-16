"""API nội bộ GET /internal/v1/users/{user_id}/status (Contract Service kiểm tra trước khi tạo bộ hồ sơ)."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models import CitizenProfile, UserRole, UserStatus
from app.repositories import user_repository as users

URL = "/internal/v1/users/{}/status"
HEADERS = {"X-Internal-Token": "internal-secret"}


@pytest.fixture(autouse=True)
def internal_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "internal-secret")


@pytest.fixture
def client(db):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def user(db):
    u = users.create(db, firebase_uid="fb-1", email="a@gmail.com", full_name="Nguyễn Văn An", auth_provider="google")
    db.commit()
    return u


def _add_profile(db, user):
    now = datetime.now(timezone.utc)
    db.add(CitizenProfile(
        user_id=user.id, id_number="001095012345", full_name="NGUYỄN VĂN AN",
        date_of_birth=datetime(1995, 8, 15).date(), gender="Nam", issue_date=datetime(2021, 9, 20).date(),
        issue_place="Cục Cảnh sát QLHC về TTXH", permanent_detail="Số 45 ngõ 120 Hoàng Hoa Thám",
        permanent_ward="Phường Ngọc Hà", permanent_province="Thành phố Hà Nội",
        declared_at=now, consented_at=now,
    ))
    db.commit()


def _lock(db, user, until=None):
    user.status = UserStatus.locked
    user.locked_reason = "Spam"
    user.locked_at = datetime.now(timezone.utc)
    user.locked_until = until
    db.commit()


def _data(res):
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_da_khai_ho_so_va_khong_khoa_thi_duoc_tao(client, db, user):
    _add_profile(db, user)
    data = _data(client.get(URL.format(user.id), headers=HEADERS))
    assert data["user_id"] == str(user.id)
    assert data["role"] == "citizen" and data["status"] == "active"
    assert data["is_locked"] is False and data["locked_until"] is None
    assert data["has_citizen_profile"] is True and data["profile_status"] == "approved"
    assert data["can_create_dossier"] is True
    assert data["block_reason"] is None and data["block_message"] is None


def test_chua_khai_ho_so_thi_khong_duoc_tao(client, user):
    data = _data(client.get(URL.format(user.id), headers=HEADERS))
    assert data["has_citizen_profile"] is False and data["profile_status"] == "not_submitted"
    assert data["can_create_dossier"] is False
    assert data["block_reason"] == "PROFILE_NOT_SUBMITTED"
    assert data["block_message"]


def test_bi_khoa_co_thoi_han_thi_khong_duoc_tao(client, db, user):
    _add_profile(db, user)
    until = datetime.now(timezone.utc) + timedelta(days=1)
    _lock(db, user, until)
    data = _data(client.get(URL.format(user.id), headers=HEADERS))
    assert data["is_locked"] is True and data["status"] == "locked"
    assert data["locked_until"] is not None
    assert data["can_create_dossier"] is False
    assert data["block_reason"] == "ACCOUNT_LOCKED"
    assert "Spam" in data["block_message"]


def test_bi_khoa_vinh_vien(client, db, user):
    _lock(db, user, None)
    data = _data(client.get(URL.format(user.id), headers=HEADERS))
    assert data["is_locked"] is True and data["locked_until"] is None
    # Khóa được ưu tiên báo trước thiếu hồ sơ.
    assert data["block_reason"] == "ACCOUNT_LOCKED"


def test_khoa_da_het_han_thi_tu_mo(client, db, user):
    _add_profile(db, user)
    _lock(db, user, datetime.now(timezone.utc) - timedelta(minutes=1))
    data = _data(client.get(URL.format(user.id), headers=HEADERS))
    assert data["is_locked"] is False and data["status"] == "active"
    assert data["can_create_dossier"] is True
    db.refresh(user)
    assert user.status == UserStatus.active


def test_tra_dung_vai_tro_luat_su(client, db, user):
    user.role = UserRole.lawyer
    db.commit()
    assert _data(client.get(URL.format(user.id), headers=HEADERS))["role"] == "lawyer"


def test_tai_khoan_da_xoa_mem_tra_404(client, db, user):
    user.deleted_at = datetime.now(timezone.utc)
    db.commit()
    res = client.get(URL.format(user.id), headers=HEADERS)
    assert res.status_code == 404 and res.json()["error"]["code"] == "USER_NOT_FOUND"


@pytest.mark.parametrize("user_id", ["5b1e2c3d-4f5a-4b6c-8d7e-9f0a1b2c3d4e", "khong-phai-uuid"])
def test_user_khong_ton_tai_tra_404(client, user_id):
    res = client.get(URL.format(user_id), headers=HEADERS)
    assert res.status_code == 404 and res.json()["error"]["code"] == "USER_NOT_FOUND"


@pytest.mark.parametrize("token", [None, "sai-token"])
def test_sai_hoac_thieu_token_tra_401(client, user, token):
    headers = {"X-Internal-Token": token} if token else {}
    res = client.get(URL.format(user.id), headers=headers)
    assert res.status_code == 401 and res.json()["error"]["code"] == "UNAUTHORIZED"


def test_chua_cau_hinh_internal_token_thi_chan_het(client, user, monkeypatch):
    monkeypatch.delenv("INTERNAL_TOKEN")
    assert client.get(URL.format(user.id), headers={"X-Internal-Token": ""}).status_code == 401
