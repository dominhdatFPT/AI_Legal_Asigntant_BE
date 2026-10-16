"""Test Lawyer Service: SQLite in-memory, JWT ký bằng key sinh trong RAM, User/Document Service giả bằng
httpx.MockTransport (không gọi mạng thật)."""

import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

INTERNAL_TOKEN = "test-internal-token"


@pytest.fixture(scope="session")
def key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public


@pytest.fixture(autouse=True)
def env(monkeypatch, key_pair):
    monkeypatch.setenv("JWT_PUBLIC_KEY", key_pair[1])
    monkeypatch.delenv("JWT_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.setenv("USER_SERVICE_URL", "http://user-service:8000")
    monkeypatch.setenv("DOCUMENT_SERVICE_URL", "http://document-service:8000")
    monkeypatch.setenv("INTERNAL_TOKEN", INTERNAL_TOKEN)
    monkeypatch.setenv("LAWYER_REAPPLY_COOLDOWN_HOURS", "24")


@pytest.fixture
def db():
    from app.core.database import Base
    from app.models import Specialization

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add_all([Specialization(code="land", name="Đất đai", sort_order=1),
                         Specialization(code="business", name="Hộ kinh doanh", sort_order=2)])
        session.commit()
        yield session
    engine.dispose()


class Upstream:
    """User Service + Document Service giả. `fail[prefix] = 'timeout' | 'down' | 'bad'` để giả lỗi."""

    def __init__(self):
        self.users = {}      # user_id -> {"has_citizen_profile", "is_locked", "full_name", "role"}
        self.cards = {}      # card_id -> {"owner_user_id", "ocr_result"}
        self.taken_emails = set()
        self.role_error = None   # (status, code) cho PUT role
        self.fail = {}
        self.calls = []

    def user(self, user_id=None, *, cccd=True, locked=False, full_name="Nguyễn Văn A", role="citizen"):
        user_id = user_id or uuid.uuid4()
        self.users[user_id] = {"has_citizen_profile": cccd, "is_locked": locked, "full_name": full_name, "role": role}
        return user_id

    def card(self, owner, *, full_name="NGUYEN VAN A", number="12345/TP", status="completed"):
        card_id = uuid.uuid4()
        fields = None if status == "failed" else {"full_name": full_name, "bar_card_number": number,
                                                  "bar_association": "Đoàn Luật sư TP. Hồ Chí Minh",
                                                  "issue_date": "2020-05-12"}
        self.cards[card_id] = {"owner_user_id": str(owner), "ocr_result": {
            "status": status, "fields": fields, "warnings": [], "engine": "tesseract-local",
            "error_code": "OCR_UNREADABLE" if status == "failed" else None}}
        return card_id

    @staticmethod
    def _ok(data, status=200):
        return httpx.Response(status, json={"data": data, "error": None, "request_id": "r"})

    @staticmethod
    def _err(status, code, message="lỗi"):
        return httpx.Response(status, json={"data": None, "error": {"code": code, "message": message},
                                            "request_id": "r"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        assert request.headers["X-Internal-Token"] == INTERNAL_TOKEN
        for prefix, kind in self.fail.items():
            if path.startswith(prefix):
                if kind == "timeout":
                    raise httpx.ReadTimeout("timeout", request=request)
                if kind == "down":
                    raise httpx.ConnectError("down", request=request)
                return httpx.Response(500, text="boom")

        if m := re.fullmatch(r"/internal/v1/users/([^/]+)/status", path):
            u = self.users.get(uuid.UUID(m[1]))
            if u is None:
                return self._err(404, "USER_NOT_FOUND")
            return self._ok({"user_id": m[1], "role": u["role"], "status": "active", "is_locked": u["is_locked"],
                             "has_citizen_profile": u["has_citizen_profile"]})
        if m := re.fullmatch(r"/internal/v1/users/([^/]+)/citizen-profile", path):
            u = self.users.get(uuid.UUID(m[1]))
            if u is None:
                return self._err(404, "USER_NOT_FOUND")
            return self._ok({"full_name": u["full_name"], "id_number": "079090001234"} if u["has_citizen_profile"]
                            else None)
        if path == "/internal/v1/users" and method == "POST":
            body = json.loads(request.content)
            if body["email"] in self.taken_emails:
                return self._err(409, "EMAIL_IN_USE", "Email này đã được dùng cho một tài khoản khác.")
            new_id = self.user(cccd=False, full_name=body["full_name"], role="lawyer")
            return self._ok({"id": str(new_id), "email": body["email"], "full_name": body["full_name"],
                             "role": "lawyer"}, 201)
        if (m := re.fullmatch(r"/internal/v1/users/([^/]+)", path)) and method == "DELETE":
            self.users.pop(uuid.UUID(m[1]), None)
            return httpx.Response(204)
        if m := re.fullmatch(r"/internal/v1/users/([^/]+)/role", path):
            if self.role_error:
                return self._err(*self.role_error)
            self.users[uuid.UUID(m[1])]["role"] = "lawyer"
            return self._ok({"id": m[1], "role": "lawyer"})
        if m := re.fullmatch(r"/internal/v1/documents/lawyer-cards/([^/]+)", path):
            card = self.cards.get(uuid.UUID(m[1]))
            if card is None:
                return self._err(404, "NOT_FOUND")
            return self._ok({"card_id": m[1], **card, "created_at": "2026-10-15T02:00:00Z"})
        if m := re.fullmatch(r"/internal/v1/documents/lawyer-cards/([^/]+)/view-url", path):
            if uuid.UUID(m[1]) not in self.cards:
                return self._err(404, "NOT_FOUND")
            return self._ok({"card_id": m[1], "url": "https://x.supabase.co/storage/v1/object/sign/a?token=t",
                             "expires_in": 300})
        return self._err(404, "NOT_FOUND")


@pytest.fixture
def upstream(monkeypatch):
    from app.services import http_client

    fake = Upstream()
    monkeypatch.setattr(http_client, "transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(http_client, "sleep", lambda seconds: None)
    return fake


@pytest.fixture
def client(db, upstream):
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.main import app

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def auth(key_pair):
    """auth(user_id, role) → header Authorization."""
    from common_auth import ALGORITHM, ISSUER

    def headers(user_id, role="citizen"):
        now = datetime.now(timezone.utc)
        token = jwt.encode({"sub": str(user_id), "role": role, "type": "access", "iss": ISSUER, "iat": now,
                            "exp": now + timedelta(minutes=15)}, key_pair[0], algorithm=ALGORITHM)
        return {"Authorization": f"Bearer {token}"}

    return headers

