"""Test Consultation Service: SQLite in-memory, JWT ký bằng key sinh trong RAM, User/Lawyer Service giả bằng
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
TERMS_VERSION = "2026-11-01"


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
    monkeypatch.setenv("LAWYER_SERVICE_URL", "http://lawyer-service:8000")
    monkeypatch.setenv("AGENT_SERVICE_URL", "http://agent-service:8000")
    monkeypatch.setenv("INTERNAL_TOKEN", INTERNAL_TOKEN)
    monkeypatch.setenv("CONSULTATION_TERMS_VERSION", TERMS_VERSION)


@pytest.fixture
def db():
    from app.core.database import Base
    import app.models  # noqa: F401  (đăng ký bảng vào Base.metadata)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def minimal_transcript(body):
    """Biên bản hợp lệ tối thiểu: một vấn đề của người dân, dẫn tin đầu tiên."""
    return {"has_substance": True, "legal_domains": ["dat_dai"], "overview": "Hỏi về sổ đỏ.",
            "citizen_issues": [{"content": "Hỏi về sổ đỏ.", "source_ids": [body["messages"][0]["id"]]}],
            "facts": [], "lawyer_advice": [], "legal_references": [], "deadlines": [], "next_steps": [],
            "conflicts": [], "open_questions": []}


class Upstream:
    """User Service + Lawyer Service giả. `fail[prefix] = 'timeout' | 'down' | 'bad'` để giả lỗi."""

    def __init__(self):
        self.locked = set()      # user_id bị khóa: GET /users/me trả 403 ACCOUNT_LOCKED
        self.practicing = set()  # user_id có can_practice = true ở Lawyer Service
        self.fail = {}
        self.calls = []
        self.summaries = {}      # conversation_id (str) → (user_id, tóm tắt); thiếu thì Agent trả 404
        self.agent_bodies = []
        self.transcript_bodies = []
        self.transcript_reply = None  # callable(body) → summary dict; None = bản hợp lệ tối thiểu

    def lawyer(self, practicing=True, locked=False):
        user_id = uuid.uuid4()
        if practicing:
            self.practicing.add(user_id)
        if locked:
            self.locked.add(user_id)
        return user_id

    @staticmethod
    def _ok(data):
        return httpx.Response(200, json={"data": data, "error": None, "request_id": "r"})

    @staticmethod
    def _err(status, code, message="lỗi"):
        return httpx.Response(status, json={"data": None, "error": {"code": code, "message": message},
                                            "request_id": "r"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        for prefix, kind in self.fail.items():
            if path.startswith(prefix):
                if kind == "timeout":
                    raise httpx.ReadTimeout("timeout", request=request)
                if kind == "down":
                    raise httpx.ConnectError("down", request=request)
                return httpx.Response(500, text="boom")

        if path == "/api/v1/users/me":
            # API công khai: gọi bằng access token của người dùng, không dùng X-Internal-Token.
            assert "X-Internal-Token" not in request.headers
            caller = jwt.decode(request.headers["Authorization"].removeprefix("Bearer "),
                                options={"verify_signature": False})["sub"]
            if uuid.UUID(caller) in self.locked:
                return self._err(403, "ACCOUNT_LOCKED")
            return self._ok({"id": caller})
        assert request.headers["X-Internal-Token"] == INTERNAL_TOKEN
        if path == "/internal/v1/agent/transcripts/summary":
            body = json.loads(request.content)
            self.transcript_bodies.append(body)
            summary = self.transcript_reply(body) if self.transcript_reply else minimal_transcript(body)
            return self._ok({"summary": summary, "prompt_version": "v1"})
        if m := re.fullmatch(r"/internal/v1/agent/conversations/([^/]+)/summary", path):
            body = json.loads(request.content)
            self.agent_bodies.append(body)
            owner, text = self.summaries.get(m[1], (None, None))
            if owner is None or str(owner) != body["user_id"]:
                return self._err(404, "CONVERSATION_NOT_FOUND")
            if text is None:
                return self._err(422, "CONVERSATION_EMPTY")
            return self._ok({"conversation_id": m[1], "summary": text})
        if m := re.fullmatch(r"/internal/v1/lawyers/([^/]+)/status", path):
            return self._ok({"user_id": m[1], "can_practice": uuid.UUID(m[1]) in self.practicing})
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


class Api:
    """Gọi API ngắn gọn trong test; mỗi hàm trả response của TestClient."""

    def __init__(self, client, auth):
        self.client, self.auth = client, auth

    def agree(self, user, role="citizen"):
        r = self.client.post("/api/v1/consultations/terms/accept", headers=self.auth(user, role),
                             json={"terms_version": TERMS_VERSION})
        assert r.status_code == 200, r.text

    def request(self, user, role="citizen", question="Tôi cần hỏi về sổ đỏ", agreed=True):
        if agreed:
            self.agree(user, role)
        return self.client.post("/api/v1/consultations", headers=self.auth(user, role),
                                json={"question": question, "source": "lobby"})

    def accept(self, lawyer, consultation_id, agreed=True):
        if agreed:
            self.agree(lawyer, "lawyer")
        return self.client.post(f"/api/v1/consultations/{consultation_id}/accept", headers=self.auth(lawyer, "lawyer"))

    def open_chat(self, citizen, lawyer):
        """Tạo yêu cầu và để `lawyer` nhận; trả id cuộc tư vấn đang in_progress."""
        created = self.request(citizen)
        assert created.status_code == 201, created.text
        consultation_id = created.json()["data"]["id"]
        accepted = self.accept(lawyer, consultation_id)
        assert accepted.status_code == 200, accepted.text
        return consultation_id

    def send(self, user, consultation_id, content="xin chào", role="citizen"):
        return self.client.post(f"/api/v1/consultations/{consultation_id}/messages", headers=self.auth(user, role),
                                json={"content": content})


@pytest.fixture
def api(client, auth):
    return Api(client, auth)


@pytest.fixture
def make_api(auth):
    """make_api(client) → Api trên một TestClient khác (test PostgreSQL)."""
    return lambda client: Api(client, auth)
