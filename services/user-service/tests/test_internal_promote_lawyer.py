"""API nội bộ PUT /internal/v1/users/{user_id}/role (Lawyer Service gọi khi admin duyệt hồ sơ luật sư)."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models import UserRole
from app.repositories import user_repository as users

URL = "/internal/v1/users/{}/role"
HEADERS = {"X-Internal-Token": "lawyer-secret"}


@pytest.fixture(autouse=True)
def internal_token(monkeypatch):
    # Route đổi role chỉ nhận LAWYER_SERVICE_TOKEN; INTERNAL_TOKEN vẫn được đặt để chứng minh nó bị từ chối.
    monkeypatch.setenv("INTERNAL_TOKEN", "internal-secret")
    monkeypatch.setenv("LAWYER_SERVICE_TOKEN", "lawyer-secret")


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


def test_promotes_citizen_and_is_idempotent(client, db, user):
    for _ in range(2):
        r = client.put(URL.format(user.id), json={"role": "lawyer"}, headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["data"]["role"] == "lawyer"
    db.refresh(user)
    assert user.role == UserRole.lawyer


def test_rejects_admin_and_other_roles(client, db, user):
    user.role = UserRole.admin
    db.commit()
    assert client.put(URL.format(user.id), json={"role": "lawyer"}, headers=HEADERS).status_code == 409
    assert client.put(URL.format(user.id), json={"role": "admin"}, headers=HEADERS).status_code == 403


@pytest.mark.parametrize("role", ["admin", "citizen", "root", ""])
def test_token_cannot_set_any_role_but_lawyer(client, db, user, role):
    r = client.put(URL.format(user.id), json={"role": role}, headers=HEADERS)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    db.refresh(user)
    assert user.role == UserRole.citizen


def test_requires_lawyer_service_token_and_existing_user(client, user):
    assert client.put(URL.format(user.id), json={"role": "lawyer"}).status_code in (401, 403)
    assert client.put(URL.format(uuid.uuid4()), json={"role": "lawyer"}, headers=HEADERS).status_code == 404


def test_wrong_token_is_rejected(client, db, user):
    r = client.put(URL.format(user.id), json={"role": "lawyer"}, headers={"X-Internal-Token": "wrong"})
    assert r.status_code == 401
    db.refresh(user)
    assert user.role == UserRole.citizen


def test_old_internal_token_is_rejected(client, db, user):
    r = client.put(URL.format(user.id), json={"role": "lawyer"}, headers={"X-Internal-Token": "internal-secret"})
    assert r.status_code == 401
    db.refresh(user)
    assert user.role == UserRole.citizen


@pytest.mark.parametrize("empty", ["unset", "blank"])
def test_unconfigured_lawyer_service_token_returns_503(client, db, user, monkeypatch, empty):
    if empty == "unset":
        monkeypatch.delenv("LAWYER_SERVICE_TOKEN")
    else:
        monkeypatch.setenv("LAWYER_SERVICE_TOKEN", "")
    for headers in (HEADERS, {"X-Internal-Token": ""}, {}):
        assert client.put(URL.format(user.id), json={"role": "lawyer"}, headers=headers).status_code == 503
    db.refresh(user)
    assert user.role == UserRole.citizen


def test_lawyer_service_token_does_not_open_other_internal_routes(client, user):
    # Các route nội bộ khác vẫn chỉ nhận INTERNAL_TOKEN.
    assert client.get("/internal/v1/users/{}/status".format(user.id), headers=HEADERS).status_code == 401
    assert client.get("/internal/v1/users/{}/status".format(user.id),
                      headers={"X-Internal-Token": "internal-secret"}).status_code == 200
