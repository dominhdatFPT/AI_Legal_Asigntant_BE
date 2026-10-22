"""API nội bộ PUT /internal/v1/users/{user_id}/role (Lawyer Service gọi khi admin duyệt hồ sơ luật sư)."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models import UserRole
from app.repositories import user_repository as users

URL = "/internal/v1/users/{}/role"
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
    assert client.put(URL.format(user.id), json={"role": "admin"}, headers=HEADERS).status_code == 422


def test_requires_internal_token_and_existing_user(client, user):
    assert client.put(URL.format(user.id), json={"role": "lawyer"}).status_code in (401, 403)
    assert client.put(URL.format(uuid.uuid4()), json={"role": "lawyer"}, headers=HEADERS).status_code == 404
