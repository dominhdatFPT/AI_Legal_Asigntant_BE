"""Test 5 endpoint hội thoại: SQLite trong RAM, JWT giả, RAG + Gemini giả lập."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.auth import TokenPayload
from app.core.database import get_db
from app.main import app
from app.services import agent, llm_client, rag_client

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
BASE = "/api/v1/agent/conversations"
AI_RESULT = {
    "content": "Theo Khoản 1 Điều 188 Luật Đất đai 2024...",
    "metadata": {"rag_chunk_ids": ["a1b2c3d4-e5f6-7890-abcd-ef1234567890"], "model": "gemini-test", "tokens_used": 512},
}


@pytest.fixture
def current_user():
    return {"sub": USER_A}


@pytest.fixture
def client(db, current_user):
    def fake_user():
        return TokenPayload(sub=current_user["sub"], role="citizen", type="access",
                            iss="legal-assistant-user-service", iat=0, exp=9999999999)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def fake_agent(monkeypatch):
    calls = []

    async def fake_process_message(user_content, history, request_id=None):
        calls.append({"user_content": user_content, "history": history, "request_id": request_id})
        return AI_RESULT

    monkeypatch.setattr(agent, "process_message", fake_process_message)
    return calls


def create(client, title=None):
    body = {"title": title} if title else None
    return client.post(BASE, json=body).json()["data"]["id"]


def test_missing_token_returns_401(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get(BASE)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_create_conversation(client):
    r = client.post(BASE, json={"title": "Thủ tục sang tên đất"}, headers={"X-Request-ID": "req-1"})

    assert r.status_code == 201
    assert r.headers["X-Request-ID"] == "req-1"
    body = r.json()
    assert body["error"] is None and body["request_id"] == "req-1"
    assert body["data"]["title"] == "Thủ tục sang tên đất"
    assert {"id", "created_at", "updated_at"} <= body["data"].keys()


def test_create_conversation_without_body(client):
    r = client.post(BASE)
    assert r.status_code == 201
    assert r.json()["data"]["title"] is None


def test_list_conversations_pagination_and_isolation(client, current_user):
    for i in range(3):
        create(client, f"Hội thoại {i}")
    current_user["sub"] = USER_B
    create(client, "Của B")
    current_user["sub"] = USER_A

    r = client.get(BASE, params={"page": 1, "page_size": 2})

    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data["items"]) == 2
    assert data["pagination"] == {"page": 1, "page_size": 2, "total": 3, "total_pages": 2}


def test_list_rejects_bad_page_size(client):
    r = client.get(BASE, params={"page_size": 101})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_conversation_not_found_and_forbidden(client, current_user):
    assert client.get(f"{BASE}/{uuid.uuid4()}").json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

    conversation_id = create(client)
    current_user["sub"] = USER_B
    r = client.get(f"{BASE}/{conversation_id}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CONVERSATION_FORBIDDEN"
    assert client.delete(f"{BASE}/{conversation_id}").status_code == 403
    assert client.post(f"{BASE}/{conversation_id}/messages", json={"content": "Hỏi"}).status_code == 403


def test_send_message_saves_both_messages_and_sets_title(client, fake_agent):
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages",
                    json={"content": "  Sang tên sổ đỏ cần gì?  "}, headers={"X-Request-ID": "req-9"})

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["user_message"]["role"] == "user"
    assert data["user_message"]["content"] == "Sang tên sổ đỏ cần gì?"
    assert data["user_message"]["metadata"] is None
    assert data["assistant_message"]["content"] == AI_RESULT["content"]
    assert data["assistant_message"]["metadata"] == AI_RESULT["metadata"]
    assert fake_agent[0] == {"user_content": "Sang tên sổ đỏ cần gì?", "history": [], "request_id": "req-9"}

    detail = client.get(f"{BASE}/{conversation_id}").json()["data"]
    assert detail["title"] == "Sang tên sổ đỏ cần gì?"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


def test_send_message_passes_history(client, fake_agent):
    conversation_id = create(client)
    client.post(f"{BASE}/{conversation_id}/messages", json={"content": "Câu 1"})
    client.post(f"{BASE}/{conversation_id}/messages", json={"content": "Câu 2"})

    assert fake_agent[1]["history"] == [
        {"role": "user", "content": "Câu 1"},
        {"role": "assistant", "content": AI_RESULT["content"]},
    ]


@pytest.mark.parametrize("error,code", [
    (rag_client.RAGError("down"), "RAG_SERVICE_ERROR"),
    (llm_client.LLMError("503"), "AI_SERVICE_ERROR"),
])
def test_send_message_service_error_returns_503_and_saves_nothing(client, monkeypatch, error, code):
    async def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(agent, "process_message", broken)
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", json={"content": "Hỏi"})

    assert r.status_code == 503
    assert r.json()["error"]["code"] == code
    assert client.get(f"{BASE}/{conversation_id}").json()["data"]["messages"] == []


@pytest.mark.parametrize("content", ["", "   ", "x" * 2001])
def test_send_message_validation(client, fake_agent, content):
    conversation_id = create(client)
    r = client.post(f"{BASE}/{conversation_id}/messages", json={"content": content})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"][0]["field"] == "content"
    assert fake_agent == []


def test_delete_conversation(client, fake_agent):
    conversation_id = create(client)
    client.post(f"{BASE}/{conversation_id}/messages", json={"content": "Hỏi"})

    r = client.delete(f"{BASE}/{conversation_id}")

    assert r.status_code == 200
    assert r.json()["data"] == {"deleted": True}
    assert client.get(f"{BASE}/{conversation_id}").status_code == 404
