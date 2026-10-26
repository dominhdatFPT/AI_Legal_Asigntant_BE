"""Test 5 endpoint hội thoại: SQLite trong RAM, JWT giả, RAG + Gemini giả lập."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.auth import TokenPayload
from app.core.database import get_db
from app.main import app
from app.services import agent, document_client, llm_client, rag_client

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

    async def fake_process_message(user_content, history, request_id=None, documents=None):
        call = {"user_content": user_content, "history": history, "request_id": request_id}
        if documents is not None:
            call["documents"] = documents
        calls.append(call)
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


# ── Tài liệu đính kèm (document_ids) ──────────────────────────────────────────

DOC_A = "88888888-8888-4888-8888-888888888888"
DOC_B = "99999999-9999-4999-8999-999999999999"
USER_AUTH = {"Authorization": "Bearer user-token"}


@pytest.fixture
def fake_documents(monkeypatch):
    """document-service giả: ghi lại token + id được hỏi, trả chữ của tài liệu."""
    calls = []

    async def fake_get_extractions(document_ids, token, request_id=None, transport=None):
        calls.append({"ids": document_ids, "token": token, "request_id": request_id})
        return [{"document_id": str(i), "source_kind": "docx", "text": f"Nội dung {i}"} for i in document_ids]

    monkeypatch.setattr(document_client, "get_extractions", fake_get_extractions)
    return calls


@pytest.fixture
def real_agent_prompts(monkeypatch, chunk):
    """Chạy agent.process_message thật, chỉ giả RAG và LLM; trả về các system prompt đã gửi LLM."""
    prompts = []

    async def fake_search(query, top_k=5, request_id=None, transport=None):
        return [chunk]

    async def fake_generate(system_prompt, history, user_content, **options):
        prompts.append(system_prompt)
        return "Hợp đồng có điểm chưa phù hợp...", 300, "gemini-test"

    monkeypatch.setattr(rag_client, "search", fake_search)
    monkeypatch.setattr(llm_client, "generate", fake_generate)
    return prompts


def test_send_message_with_documents_puts_text_in_llm_prompt_and_saves_only_ids(
    client, fake_documents, real_agent_prompts,
):
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", headers={**USER_AUTH, "X-Request-ID": "req-5"},
                    json={"content": "Hợp đồng này có hợp lệ không?", "document_ids": [DOC_A, DOC_B]})

    assert r.status_code == 200
    # token của người dùng được chuyển tiếp nguyên vẹn, id giữ thứ tự
    assert fake_documents == [{"ids": [uuid.UUID(DOC_A), uuid.UUID(DOC_B)], "token": "user-token", "request_id": "req-5"}]
    assert f"Nội dung {DOC_A}" in real_agent_prompts[0] and f"Nội dung {DOC_B}" in real_agent_prompts[0]
    assert r.json()["data"]["assistant_message"]["metadata"]["document_ids"] == [DOC_A, DOC_B]
    assert r.json()["data"]["user_message"]["metadata"] is None

    saved = client.get(f"{BASE}/{conversation_id}").json()["data"]["messages"]
    assert not any("Nội dung" in m["content"] for m in saved)  # nội dung tài liệu không được lưu
    assert "Nội dung" not in str(saved[1]["metadata"])


def test_review_mode_is_saved_in_assistant_metadata(client, monkeypatch, real_agent_prompts):
    async def contract(document_ids, token, request_id=None, transport=None):
        return [{"document_id": str(document_ids[0]), "source_kind": "docx",
                 "text": "HỢP ĐỒNG CHUYỂN NHƯỢNG QUYỀN SỬ DỤNG ĐẤT"}]

    monkeypatch.setattr(document_client, "get_extractions", contract)
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", headers=USER_AUTH,
                    json={"content": "kiểm tra giúp tôi", "document_ids": [DOC_A]})

    metadata = r.json()["data"]["assistant_message"]["metadata"]
    assert metadata["mode"] == "contract_review" and metadata["contract_type"] == "chuyen_nhuong_dat"
    assert metadata["document_ids"] == [DOC_A]
    assert "KHÔNG hỏi lại người dùng" in real_agent_prompts[0]


def test_review_with_every_rag_query_failing_returns_503_and_saves_nothing(client, monkeypatch, fake_documents):
    async def broken(*args, **kwargs):
        raise rag_client.RAGError("down")

    async def never(*args, **kwargs):
        raise AssertionError("không được gọi AI khi RAG lỗi")

    monkeypatch.setattr(rag_client, "search", broken)
    monkeypatch.setattr(llm_client, "generate", never)
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", headers=USER_AUTH,
                    json={"content": "kiểm tra giúp tôi", "document_ids": [DOC_A]})

    assert r.status_code == 503 and r.json()["error"]["code"] == "RAG_SERVICE_ERROR"
    assert client.get(f"{BASE}/{conversation_id}").json()["data"]["messages"] == []


def test_send_message_with_cookie_token_forwards_it(client, fake_documents, fake_agent):
    conversation_id = create(client)
    client.cookies.set("access_token", "cookie-token")

    r = client.post(f"{BASE}/{conversation_id}/messages", json={"content": "Hỏi", "document_ids": [DOC_A]})

    assert r.status_code == 200
    assert fake_documents[0]["token"] == "cookie-token"


def test_send_message_with_documents_but_no_token_returns_401(client, fake_documents, fake_agent):
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", json={"content": "Hỏi", "document_ids": [DOC_A]})

    assert r.status_code == 401
    assert fake_documents == [] and fake_agent == []


def test_send_message_without_document_ids_behaves_as_before(client, fake_agent, fake_documents):
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", json={"content": "Hỏi"})

    assert r.status_code == 200
    assert fake_documents == []  # không gọi document-service
    assert "documents" not in fake_agent[0]
    assert r.json()["data"]["assistant_message"]["metadata"] == AI_RESULT["metadata"]


def test_send_message_with_empty_document_ids_behaves_as_before(client, fake_agent, fake_documents):
    conversation_id = create(client)
    r = client.post(f"{BASE}/{conversation_id}/messages", json={"content": "Hỏi", "document_ids": []})
    assert r.status_code == 200
    assert fake_documents == []
    assert "document_ids" not in r.json()["data"]["assistant_message"]["metadata"]


@pytest.mark.parametrize("error,status,code", [
    (document_client.DocumentNotFound("x"), 404, "DOCUMENT_NOT_FOUND"),
    (document_client.DocumentNotReady("Tài liệu đang xử lý, vui lòng thử lại sau."), 409, "DOCUMENT_NOT_READY"),
    (document_client.DocumentServiceError("down"), 503, "DOCUMENT_SERVICE_ERROR"),
])
def test_document_error_returns_code_and_saves_nothing(client, monkeypatch, fake_agent, error, status, code):
    async def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(document_client, "get_extractions", broken)
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", headers=USER_AUTH,
                    json={"content": "Hỏi", "document_ids": [DOC_A]})

    assert r.status_code == status
    assert r.json()["error"]["code"] == code
    assert fake_agent == []  # không gọi AI
    assert client.get(f"{BASE}/{conversation_id}").json()["data"]["messages"] == []


def test_not_ready_message_is_shown_to_user(client, monkeypatch, fake_agent):
    async def not_ready(*args, **kwargs):
        raise document_client.DocumentNotReady(document_client.NOT_READY_MESSAGE)

    monkeypatch.setattr(document_client, "get_extractions", not_ready)
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", headers=USER_AUTH,
                    json={"content": "Hỏi", "document_ids": [DOC_A]})

    assert "đang xử lý" in r.json()["error"]["message"]


def test_rag_error_with_documents_still_saves_nothing(client, monkeypatch, fake_documents):
    async def broken(*args, **kwargs):
        raise rag_client.RAGError("down")

    monkeypatch.setattr(agent, "process_message", broken)
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", headers=USER_AUTH,
                    json={"content": "Hỏi", "document_ids": [DOC_A]})

    assert r.status_code == 503 and r.json()["error"]["code"] == "RAG_SERVICE_ERROR"
    assert client.get(f"{BASE}/{conversation_id}").json()["data"]["messages"] == []


def test_documents_are_not_fetched_for_someone_elses_conversation(client, current_user, fake_documents, fake_agent):
    conversation_id = create(client)
    current_user["sub"] = USER_B

    r = client.post(f"{BASE}/{conversation_id}/messages", headers=USER_AUTH,
                    json={"content": "Hỏi", "document_ids": [DOC_A]})

    assert r.status_code == 403
    assert fake_documents == []


@pytest.mark.parametrize("document_ids", [
    [DOC_A, DOC_B, str(uuid.uuid4()), str(uuid.uuid4())],  # quá 3 tài liệu
    [DOC_A, DOC_A],                                        # trùng
    ["khong-phai-uuid"],                                   # id sai
])
def test_invalid_document_ids_return_422(client, fake_documents, fake_agent, document_ids):
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", headers=USER_AUTH,
                    json={"content": "Hỏi", "document_ids": document_ids})

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert fake_documents == [] and fake_agent == []


def test_three_documents_are_accepted(client, fake_documents, fake_agent):
    conversation_id = create(client)
    ids = [str(uuid.uuid4()) for _ in range(3)]
    r = client.post(f"{BASE}/{conversation_id}/messages", headers=USER_AUTH, json={"content": "Hỏi", "document_ids": ids})
    assert r.status_code == 200


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
