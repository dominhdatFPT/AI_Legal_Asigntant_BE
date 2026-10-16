from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.database import database_url
from app.models import UserRole, UserStatus
from app.repositories import refresh_token_repository as tokens
from app.repositories import user_repository as users


def _user(db, uid="firebase-1", email="a@gmail.com"):
    return users.create(db, firebase_uid=uid, email=email, full_name="Nguyễn Văn A", auth_provider="google")


def test_tao_user_moi_mac_dinh_citizen_va_active(db):
    user = _user(db)
    assert user.id is not None
    assert user.role == UserRole.citizen
    assert user.status == UserStatus.active


def test_tim_user_theo_firebase_uid(db):
    user = _user(db)
    assert users.get_by_firebase_uid(db, "firebase-1").id == user.id
    assert users.get_by_firebase_uid(db, "khong-ton-tai") is None


def test_tim_user_theo_id(db):
    user = _user(db)
    assert users.get_by_id(db, user.id).email == "a@gmail.com"


def test_user_da_xoa_mem_khong_tim_thay(db):
    user = _user(db)
    user.deleted_at = datetime.now(timezone.utc)
    db.flush()
    assert users.get_by_firebase_uid(db, "firebase-1") is None
    assert users.get_by_id(db, user.id) is None


def test_trung_firebase_uid_bi_chan(db):
    _user(db)
    with pytest.raises(IntegrityError):
        _user(db, email="khac@gmail.com")


def test_trung_email_bi_chan(db):
    _user(db)
    with pytest.raises(IntegrityError):
        _user(db, uid="firebase-2")


def test_luu_va_tim_refresh_token_theo_hash(db):
    user = _user(db)
    expires = datetime.now(timezone.utc) + timedelta(days=14)
    tokens.add(db, user_id=user.id, token_hash="hash-1", expires_at=expires, user_agent="Chrome")
    found = tokens.get_by_hash(db, "hash-1")
    assert found.user_id == user.id
    assert found.revoked_at is None
    assert tokens.get_by_hash(db, "hash-khac") is None


def test_thu_hoi_mot_refresh_token(db):
    user = _user(db)
    token = tokens.add(db, user_id=user.id, token_hash="hash-1", expires_at=datetime.now(timezone.utc))
    tokens.revoke(db, token)
    first_revoked_at = token.revoked_at
    assert first_revoked_at is not None
    tokens.revoke(db, token)
    assert token.revoked_at == first_revoked_at


def test_thu_hoi_toan_bo_token_cua_mot_user(db):
    a, b = _user(db), _user(db, uid="firebase-2", email="b@gmail.com")
    now = datetime.now(timezone.utc)
    for i in range(3):
        tokens.add(db, user_id=a.id, token_hash=f"a-{i}", expires_at=now)
    tokens.add(db, user_id=b.id, token_hash="b-0", expires_at=now)
    assert tokens.revoke_all_for_user(db, a.id) == 3
    db.expire_all()
    assert all(tokens.get_by_hash(db, f"a-{i}").revoked_at for i in range(3))
    assert tokens.get_by_hash(db, "b-0").revoked_at is None


def test_database_url_tu_doi_sang_driver_psycopg(monkeypatch):
    monkeypatch.setenv("USER_DATABASE_URL", "postgresql://u:p@host:5432/postgres")
    assert database_url() == "postgresql+psycopg://u:p@host:5432/postgres"


def test_thieu_database_url_bao_loi(monkeypatch):
    monkeypatch.delenv("USER_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        database_url()
