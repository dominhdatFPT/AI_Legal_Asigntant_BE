"""Test 5 endpoint hội thoại: SQLite trong RAM, JWT giả, RAG + Gemini giả lập."""
import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1 import conversations
from app.core.auth import TokenPayload
from app.core.database import get_db
from app.main import app
from app.repositories import conversation as repo
from app.schemas.message import SendMessageRequest
from app.services import agent, document_client, llm_client, prompts, query_filter, rag_client, relevance, responder

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


# ── Stream: POST /conversations/{id}/messages/stream ───────────────────────────────────────────────────────

STREAM_PARTS = ["Theo ", "Khoản 1 ", "Điều 188."]


def stream_url(conversation_id):
    return f"{BASE}/{conversation_id}/messages/stream"


def parse_sse(text):
    """[(tên sự kiện, dữ liệu JSON)] theo thứ tự nhận."""
    events = []
    for block in text.strip().split("\n\n"):
        event_line, data_line = block.split("\n")
        assert event_line.startswith("event: ") and data_line.startswith("data: ")
        events.append((event_line[len("event: "):], json.loads(data_line[len("data: "):])))
    return events


@pytest.fixture
def streaming(monkeypatch, chunk):
    """rag giả + generate_stream giả (chạy agent.prepare thật). state["fail_after"] = n: ném LLMError sau n mẩu."""
    state = {"stream_calls": [], "searches": [], "parts": list(STREAM_PARTS), "fail_after": None}

    async def fake_search(query, top_k=5, request_id=None, transport=None):
        state["searches"].append(query)
        return [chunk]

    async def fake_stream(system_prompt, history, user_content, info=None):
        state["stream_calls"].append({"system_prompt": system_prompt, "history": history, "user_content": user_content})
        for i, part in enumerate(state["parts"]):
            if state["fail_after"] == i:
                raise llm_client.LLMError("503 giữa chừng")
            yield part
        if info is not None:
            info.update(model="stream-model", tokens=77)

    async def forbidden(*args, **kwargs):
        raise AssertionError("llm_client.generate không được gọi khi stream")

    monkeypatch.setattr(rag_client, "search", fake_search)
    monkeypatch.setattr(llm_client, "generate_stream", fake_stream)
    monkeypatch.setattr(llm_client, "generate", forbidden)
    return state


def saved_messages(client, conversation_id):
    return client.get(f"{BASE}/{conversation_id}").json()["data"]["messages"]


def test_stream_emits_deltas_then_done_matching_saved_messages(client, streaming, chunk):
    conversation_id = create(client)

    r = client.post(stream_url(conversation_id), json={"content": "  Sang tên sổ đỏ cần gì?  "},
                    headers={"X-Request-ID": "req-s1"})

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-accel-buffering"] == "no"
    assert r.headers["x-request-id"] == "req-s1"
    events = parse_sse(r.text)
    assert [name for name, _ in events] == ["delta", "delta", "delta", "done"]
    assert [d["text"] for name, d in events if name == "delta"] == STREAM_PARTS

    done = events[-1][1]
    assert done["assistant_message"]["content"] == "".join(STREAM_PARTS)
    assert done["user_message"]["content"] == "Sang tên sổ đỏ cần gì?"
    assert done["assistant_message"]["metadata"] == {
        "rag_chunk_ids": [chunk["id"]], "model": "stream-model", "tokens_used": 77,
    }
    # done khớp từng trường với dữ liệu đã lưu DB
    saved = saved_messages(client, conversation_id)
    assert [m["role"] for m in saved] == ["user", "assistant"]
    assert saved[0] == done["user_message"] and saved[1] == done["assistant_message"]
    assert streaming["searches"] == ["Sang tên sổ đỏ cần gì?"]
    assert streaming["stream_calls"][0]["user_content"] == "Sang tên sổ đỏ cần gì?"
    assert "Khoản 1 Điều 188, Luật Đất đai 2024" in streaming["stream_calls"][0]["system_prompt"]


def test_stream_sets_title_and_passes_history(client, streaming):
    conversation_id = create(client)
    client.post(stream_url(conversation_id), json={"content": "Câu 1"})
    client.post(stream_url(conversation_id), json={"content": "Câu 2"})

    assert streaming["stream_calls"][1]["history"] == [
        {"role": "user", "content": "Câu 1"},
        {"role": "assistant", "content": "".join(STREAM_PARTS)},
    ]
    assert client.get(f"{BASE}/{conversation_id}").json()["data"]["title"] == "Câu 1"


def test_stream_event_format_keeps_vietnamese_and_escapes_newlines(client, streaming):
    streaming["parts"] = ["Dòng 1\nDòng 2 đ"]
    conversation_id = create(client)

    raw = client.post(stream_url(conversation_id), json={"content": "Hỏi"}).text

    assert raw.startswith('event: delta\ndata: {"text": "Dòng 1\\nDòng 2 đ"}\n\n')
    assert raw.endswith("\n\n")


def test_stream_llm_error_midway_sends_error_event_and_saves_nothing(client, streaming):
    streaming["fail_after"] = 1
    conversation_id = create(client)

    r = client.post(stream_url(conversation_id), json={"content": "Hỏi"})

    assert r.status_code == 200  # đã mở stream nên không còn đổi được mã HTTP
    events = parse_sse(r.text)
    assert [name for name, _ in events] == ["delta", "error"]
    assert events[1][1]["code"] == "AI_SERVICE_ERROR" and events[1][1]["message"]
    assert saved_messages(client, conversation_id) == []


def test_stream_llm_error_before_first_chunk_sends_error_event_and_saves_nothing(client, streaming):
    streaming["fail_after"] = 0
    conversation_id = create(client)

    events = parse_sse(client.post(stream_url(conversation_id), json={"content": "Hỏi"}).text)

    assert [name for name, _ in events] == ["error"]
    assert saved_messages(client, conversation_id) == []


def test_stream_unexpected_error_sends_internal_error_event(client, streaming, monkeypatch):
    def broken_add_message_pair(*args, **kwargs):
        raise RuntimeError("DB down")

    monkeypatch.setattr(repo, "add_message_pair", broken_add_message_pair)
    conversation_id = create(client)

    events = parse_sse(client.post(stream_url(conversation_id), json={"content": "Hỏi"}).text)

    assert [name for name, _ in events] == ["delta", "delta", "delta", "error"]
    assert events[-1][1]["code"] == "INTERNAL_ERROR"


def test_stream_client_disconnect_midway_saves_nothing(db, streaming):
    conversation = repo.create_conversation(db, uuid.UUID(USER_A))
    call = responder.LLMCall("system prompt", [], [], "Hỏi", "req-d", 0.0, 0.0)

    async def run():
        events = conversations._stream_events(
            db, conversation, SendMessageRequest(content="Hỏi"), "req-d", datetime.now(timezone.utc), call, None,
        )
        first = await events.__anext__()
        await events.aclose()  # client ngắt kết nối sau mẩu đầu
        return first

    assert asyncio.run(run()).startswith("event: delta")
    assert repo.get_messages(db, conversation.id) == []


def test_stream_meaningless_question_is_single_delta_then_done(client, streaming):
    conversation_id = create(client)

    events = parse_sse(client.post(stream_url(conversation_id), json={"content": "asdfgh qwerty zxcvb"}).text)

    assert [name for name, _ in events] == ["delta", "done"]
    assert events[0][1] == {"text": query_filter.DEFAULT_ANSWER}
    assert streaming["searches"] == [] and streaming["stream_calls"] == []
    assert len(saved_messages(client, conversation_id)) == 2


def test_stream_out_of_scope_is_single_delta_without_answering_llm(client, streaming, monkeypatch):
    from app.core.config import get_settings

    async def grade(user_content, chunks, request_id=None, info=None):
        info.update(model="grader-model", tokens=9)
        return []

    monkeypatch.setenv("RELEVANCE_CHECK", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(relevance, "grade", grade)
    conversation_id = create(client)

    events = parse_sse(client.post(stream_url(conversation_id), json={"content": "Ly hôn chia tài sản thế nào?"}).text)

    assert [name for name, _ in events] == ["delta", "done"]
    assert events[0][1] == {"text": prompts.OUT_OF_SCOPE_ANSWER}
    assert events[1][1]["assistant_message"]["metadata"] == {
        "rag_chunk_ids": [], "model": "grader-model", "tokens_used": 9, "type": "out_of_scope",
    }
    assert streaming["stream_calls"] == []


def test_stream_llm_bypass_is_single_delta(client, streaming, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("LLM_BYPASS", "true")
    get_settings.cache_clear()
    conversation_id = create(client)

    events = parse_sse(client.post(stream_url(conversation_id), json={"content": "Hỏi"}).text)

    assert [name for name, _ in events] == ["delta", "done"]
    assert events[0][1]["text"].startswith(responder.BYPASS_HEADER)
    assert events[1][1]["assistant_message"]["metadata"]["llm_bypass"] is True
    assert streaming["stream_calls"] == []


def test_stream_with_documents_reviews_without_streaming_and_saves_document_ids(
    client, streaming, monkeypatch, real_agent_prompts,
):
    async def contract(document_ids, token, request_id=None, transport=None):
        return [{"document_id": str(document_ids[0]), "source_kind": "docx",
                 "text": "HỢP ĐỒNG CHUYỂN NHƯỢNG QUYỀN SỬ DỤNG ĐẤT"}]

    monkeypatch.setattr(document_client, "get_extractions", contract)
    conversation_id = create(client)

    r = client.post(stream_url(conversation_id), headers=USER_AUTH,
                    json={"content": "kiểm tra giúp tôi", "document_ids": [DOC_A]})

    events = parse_sse(r.text)
    assert [name for name, _ in events] == ["delta", "done"]
    assert events[0][1] == {"text": "Hợp đồng có điểm chưa phù hợp..."}
    metadata = events[1][1]["assistant_message"]["metadata"]
    assert metadata["mode"] == "contract_review" and metadata["document_ids"] == [DOC_A]
    assert streaming["stream_calls"] == []  # rà soát không stream
    assert "KHÔNG hỏi lại người dùng" in real_agent_prompts[0]


@pytest.mark.parametrize("case", ["other_user", "not_found", "no_token_for_documents", "invalid_body"])
def test_stream_errors_before_opening_stream_are_plain_json(client, current_user, streaming, fake_documents, case):
    conversation_id = create(client)
    url, headers, body, status, code = stream_url(conversation_id), {}, {"content": "Hỏi"}, 403, "CONVERSATION_FORBIDDEN"
    if case == "other_user":
        current_user["sub"] = USER_B
    elif case == "not_found":
        url, status, code = stream_url(uuid.uuid4()), 404, "CONVERSATION_NOT_FOUND"
    elif case == "no_token_for_documents":
        body, status, code = {"content": "Hỏi", "document_ids": [DOC_A]}, 401, "UNAUTHORIZED"
    elif case == "invalid_body":
        body, status, code = {"content": ""}, 422, "VALIDATION_ERROR"

    r = client.post(url, json=body, headers=headers)

    assert r.status_code == status
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"]["code"] == code
    assert streaming["searches"] == [] and streaming["stream_calls"] == []
    assert fake_documents == []


@pytest.mark.parametrize("error,status,code", [
    (document_client.DocumentNotFound("x"), 404, "DOCUMENT_NOT_FOUND"),
    (document_client.DocumentNotReady("Tài liệu đang xử lý, vui lòng thử lại sau."), 409, "DOCUMENT_NOT_READY"),
    (document_client.DocumentServiceError("down"), 503, "DOCUMENT_SERVICE_ERROR"),
])
def test_stream_document_errors_are_plain_json(client, monkeypatch, streaming, error, status, code):
    async def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(document_client, "get_extractions", broken)
    conversation_id = create(client)

    r = client.post(stream_url(conversation_id), headers=USER_AUTH, json={"content": "Hỏi", "document_ids": [DOC_A]})

    assert r.status_code == status and r.json()["error"]["code"] == code
    assert streaming["searches"] == []
    assert saved_messages(client, conversation_id) == []


@pytest.mark.parametrize("error,code", [
    (rag_client.RAGError("down"), "RAG_SERVICE_ERROR"),
    (llm_client.LLMError("503"), "AI_SERVICE_ERROR"),
])
def test_stream_rag_or_prepare_error_is_plain_503_and_saves_nothing(client, monkeypatch, streaming, error, code):
    async def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(agent, "prepare", broken)
    conversation_id = create(client)

    r = client.post(stream_url(conversation_id), json={"content": "Hỏi"})

    assert r.status_code == 503
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"]["code"] == code
    assert saved_messages(client, conversation_id) == []
    assert streaming["stream_calls"] == []


def test_stream_rag_down_for_real_is_503_before_stream(client, monkeypatch, streaming):
    async def broken_search(*args, **kwargs):
        raise rag_client.RAGError("down")

    monkeypatch.setattr(rag_client, "search", broken_search)
    conversation_id = create(client)

    r = client.post(stream_url(conversation_id), json={"content": "Hỏi"})

    assert r.status_code == 503 and r.json()["error"]["code"] == "RAG_SERVICE_ERROR"
    assert streaming["stream_calls"] == []


def test_old_endpoint_unchanged_while_stream_exists(client, fake_agent):
    conversation_id = create(client)

    r = client.post(f"{BASE}/{conversation_id}/messages", json={"content": "Hỏi"})

    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.json()["data"]["assistant_message"]["content"] == AI_RESULT["content"]


# ── Cache câu trả lời ở endpoint stream (ANSWER_CACHE_ENABLED) ────────────────

def enable_answer_cache(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("ANSWER_CACHE_ENABLED", "true")
    get_settings.cache_clear()
    responder.clear_answer_cache()


def test_stream_answer_cache_second_conversation_gets_one_delta_without_llm(client, streaming, monkeypatch, chunk):
    enable_answer_cache(monkeypatch)
    first_id, second_id = create(client), create(client)

    first = parse_sse(client.post(stream_url(first_id), json={"content": "Sang tên sổ đỏ cần gì?"}).text)
    second = parse_sse(client.post(stream_url(second_id), json={"content": "  sang TÊN sổ đỏ cần gì ?"}).text)

    assert [n for n, _ in first] == ["delta", "delta", "delta", "done"]
    assert [n for n, _ in second] == ["delta", "done"]
    assert second[0][1]["text"] == "".join(STREAM_PARTS)
    assert len(streaming["stream_calls"]) == 1 and len(streaming["searches"]) == 1
    done = second[-1][1]
    assert done["assistant_message"]["content"] == "".join(STREAM_PARTS)
    assert done["assistant_message"]["metadata"] == {
        "rag_chunk_ids": [chunk["id"]], "model": "stream-model", "tokens_used": 0, "cache_hit": True,
    }
    # lượt trúng cache vẫn được lưu vào hội thoại mới như bình thường
    saved = saved_messages(client, second_id)
    assert [m["role"] for m in saved] == ["user", "assistant"] and saved[1] == done["assistant_message"]


def test_stream_answer_cache_follow_up_in_same_conversation_is_not_cached(client, streaming, monkeypatch):
    enable_answer_cache(monkeypatch)
    conversation_id = create(client)

    client.post(stream_url(conversation_id), json={"content": "Sang tên sổ đỏ cần gì?"})
    again = parse_sse(client.post(stream_url(conversation_id), json={"content": "Sang tên sổ đỏ cần gì?"}).text)

    assert [n for n, _ in again] == ["delta", "delta", "delta", "done"]  # có lịch sử: gọi LLM thật
    assert len(streaming["stream_calls"]) == 2


def test_stream_answer_cache_broken_stream_is_not_stored(client, streaming, monkeypatch):
    enable_answer_cache(monkeypatch)
    streaming["fail_after"] = 1
    broken = parse_sse(client.post(stream_url(create(client)), json={"content": "Sang tên sổ đỏ cần gì?"}).text)
    assert broken[-1][0] == "error"
    assert len(responder._get_answer_cache()) == 0

    streaming["fail_after"] = None
    ok = parse_sse(client.post(stream_url(create(client)), json={"content": "Sang tên sổ đỏ cần gì?"}).text)

    assert ok[-1][0] == "done" and len(streaming["stream_calls"]) == 2


def test_send_message_and_stream_share_the_answer_cache(client, streaming, monkeypatch):
    enable_answer_cache(monkeypatch)
    parse_sse(client.post(stream_url(create(client)), json={"content": "Sang tên sổ đỏ cần gì?"}).text)

    r = client.post(f"{BASE}/{create(client)}/messages", json={"content": "sang tên sổ đỏ cần gì?"})

    assert r.status_code == 200
    message = r.json()["data"]["assistant_message"]
    assert message["content"] == "".join(STREAM_PARTS)
    assert message["metadata"]["cache_hit"] is True and message["metadata"]["tokens_used"] == 0
    assert len(streaming["stream_calls"]) == 1 and len(streaming["searches"]) == 1


def test_stream_answer_cache_off_by_default(client, streaming):
    for _ in range(2):
        parse_sse(client.post(stream_url(create(client)), json={"content": "Sang tên sổ đỏ cần gì?"}).text)
    assert len(streaming["stream_calls"]) == 2 and len(streaming["searches"]) == 2


# ---------- Kịch bản hội thoại: GET /conversations/{id}/transcript ----------

def seed_messages(db, conversation_id, items, start=datetime(2026, 10, 7, 1, 0, 0, tzinfo=timezone.utc)):
    """Ghi thẳng tin nhắn vào DB với thời gian tăng dần từng phút. items: [(role, content), ...]."""
    from datetime import timedelta
    from app.models import Message

    db.add_all([
        Message(conversation_id=uuid.UUID(str(conversation_id)), role=role, content=content,
                created_at=start + timedelta(minutes=i))
        for i, (role, content) in enumerate(items)
    ])
    db.commit()


def transcript_url(conversation_id):
    return f"{BASE}/{conversation_id}/transcript"


TWO_TURNS = [
    ("user", "Sang tên sổ đỏ cần gì?"),
    ("assistant", "Theo Điều 188 Luật Đất đai 2024:\n- Hồ sơ\n- Lệ phí"),
    ("user", "Còn thời hạn?"),
    ("assistant", "Tối đa 20 ngày."),
]


def test_transcript_json_two_turns_in_order(client, db):
    conversation_id = create(client, "Sang tên đất")
    seed_messages(db, conversation_id, TWO_TURNS)

    r = client.get(transcript_url(conversation_id), headers={"X-Request-ID": "req-t"})

    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == "req-t"
    body = r.json()
    assert body["error"] is None and body["request_id"] == "req-t"
    data = body["data"]
    assert data["conversation_id"] == conversation_id and data["title"] == "Sang tên đất"
    assert {"created_at", "updated_at"} <= data.keys()
    assert data["total_messages"] == 4 and data["truncated"] is False
    assert [t["turn"] for t in data["turns"]] == [1, 2]
    assert data["turns"][0]["user"]["content"] == "Sang tên sổ đỏ cần gì?"
    assert data["turns"][0]["assistant"]["content"] == "Theo Điều 188 Luật Đất đai 2024:\n- Hồ sơ\n- Lệ phí"
    assert data["turns"][1]["user"]["content"] == "Còn thời hạn?"
    assert data["turns"][1]["assistant"]["content"] == "Tối đa 20 ngày."
    assert data["turns"][0]["user"]["created_at"] < data["turns"][0]["assistant"]["created_at"] \
        < data["turns"][1]["user"]["created_at"]


def test_transcript_not_limited_by_history_limit(client, db):
    conversation_id = create(client)
    items = [(role, f"{role} {i}") for i in range(1, 11) for role in ("user", "assistant")]  # 20 tin > HISTORY_LIMIT
    seed_messages(db, conversation_id, items)

    data = client.get(transcript_url(conversation_id)).json()["data"]

    assert len(data["turns"]) == 10 and data["total_messages"] == 20
    assert data["turns"][0]["user"]["content"] == "user 1" and data["turns"][-1]["assistant"]["content"] == "assistant 10"


def test_transcript_same_created_at_orders_by_id(client, db):
    """Bằng thời gian thì xếp theo id để thứ tự ổn định giữa các lần gọi."""
    from app.models import Message

    conversation_id = uuid.UUID(create(client))
    same = datetime(2026, 10, 7, 1, 0, 0, tzinfo=timezone.utc)
    low, high = uuid.UUID(int=1), uuid.UUID(int=2)
    db.add_all([
        Message(id=high, conversation_id=conversation_id, role="assistant", content="Trả lời", created_at=same),
        Message(id=low, conversation_id=conversation_id, role="user", content="Hỏi", created_at=same),
    ])
    db.commit()

    turns = client.get(transcript_url(conversation_id)).json()["data"]["turns"]

    assert turns[0]["user"]["content"] == "Hỏi" and turns[0]["assistant"]["content"] == "Trả lời"


def test_transcript_text_format(client, db):
    conversation_id = create(client, "Sang tên đất")
    seed_messages(db, conversation_id, TWO_TURNS)  # 01:00 UTC = 08:00 giờ Việt Nam

    r = client.get(transcript_url(conversation_id), params={"format": "text"}, headers={"X-Request-ID": "req-t"})

    assert r.status_code == 200
    assert r.headers["content-type"].replace(" ", "").lower() == "text/plain;charset=utf-8"
    assert r.headers["X-Request-ID"] == "req-t"
    assert "content-disposition" not in r.headers
    lines = r.text.split("\n")
    assert lines[0] == "Hội thoại: Sang tên đất"
    assert lines[1].startswith("Bắt đầu: ") and len(lines[1]) == len("Bắt đầu: 07/10/2026 08:00")
    assert r.text.split("\n", 2)[2] == (
        "\n"
        "Lượt 1\n"
        "  Người dùng: Sang tên sổ đỏ cần gì?\n"
        "  Trợ lý: Theo Điều 188 Luật Đất đai 2024:\n- Hồ sơ\n- Lệ phí\n"
        "\n"
        "Lượt 2\n"
        "  Người dùng: Còn thời hạn?\n"
        "  Trợ lý: Tối đa 20 ngày.\n"
    )


def test_transcript_text_start_time_is_vietnam_time(client, db, monkeypatch):
    conversation_id = create(client)
    conversation = repo.get_conversation(db, uuid.UUID(conversation_id))
    conversation.created_at = datetime(2026, 10, 7, 20, 5, tzinfo=timezone.utc)  # 20:05 UTC = 03:05 ngày 08/10 giờ VN
    db.commit()

    text = client.get(transcript_url(conversation_id), params={"format": "text"}).text

    assert "Bắt đầu: 08/10/2026 03:05\n" in text


def test_transcript_text_without_title(client):
    text = client.get(transcript_url(create(client)), params={"format": "text"}).text

    assert text.split("\n")[0] == "Hội thoại: (chưa có tiêu đề)"


def test_transcript_rejects_bad_format(client):
    r = client.get(transcript_url(create(client)), params={"format": "pdf"})

    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_transcript_download_adds_content_disposition(client, db):
    conversation_id = create(client)
    seed_messages(db, conversation_id, TWO_TURNS)

    r = client.get(transcript_url(conversation_id), params={"format": "text", "download": "true"})

    assert r.status_code == 200
    assert r.headers["content-disposition"] == f'attachment; filename="kich-ban-{conversation_id[:8]}.txt"'
    assert r.text.startswith("Hội thoại:")


def test_transcript_download_false_or_json_has_no_content_disposition(client):
    conversation_id = create(client)

    off = client.get(transcript_url(conversation_id), params={"format": "text", "download": "false"})
    as_json = client.get(transcript_url(conversation_id), params={"download": "true"})

    assert "content-disposition" not in off.headers
    assert as_json.status_code == 200 and "content-disposition" not in as_json.headers
    assert as_json.json()["data"]["turns"] == []


def test_transcript_never_contains_other_conversations_messages(client, db, current_user):
    a = create(client, "Của A")
    seed_messages(db, a, [("user", "Hỏi của A"), ("assistant", "Đáp của A")])
    current_user["sub"] = USER_B
    b = create(client, "Của B")
    seed_messages(db, b, [("user", "Hỏi của B"), ("assistant", "Đáp của B"), ("user", "Hỏi thêm của B")])
    current_user["sub"] = USER_A

    json_data = client.get(transcript_url(a)).json()["data"]
    text = client.get(transcript_url(a), params={"format": "text"}).text

    assert json_data["total_messages"] == 2 and len(json_data["turns"]) == 1
    assert json_data["turns"][0]["user"]["content"] == "Hỏi của A"
    assert "của B" not in json.dumps(json_data, ensure_ascii=False) and "của B" not in text


def test_transcript_of_other_users_conversation_is_forbidden(client, db, current_user):
    conversation_id = create(client)
    seed_messages(db, conversation_id, [("user", "Bí mật của A"), ("assistant", "Đáp")])
    current_user["sub"] = USER_B

    for params in ({}, {"format": "text"}, {"format": "text", "download": "true"}):
        r = client.get(transcript_url(conversation_id), params=params)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "CONVERSATION_FORBIDDEN"
        assert "Bí mật" not in r.text


def test_transcript_unknown_conversation_is_404(client):
    r = client.get(transcript_url(uuid.uuid4()))

    assert r.status_code == 404 and r.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def test_transcript_missing_token_returns_401(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = TestClient(app).get(transcript_url(uuid.uuid4()))
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"


def test_transcript_empty_conversation(client):
    conversation_id = create(client)

    as_json = client.get(transcript_url(conversation_id))
    as_text = client.get(transcript_url(conversation_id), params={"format": "text"})

    assert as_json.status_code == 200
    data = as_json.json()["data"]
    assert data["turns"] == [] and data["total_messages"] == 0 and data["truncated"] is False
    assert as_text.status_code == 200 and as_text.text.startswith("Hội thoại: (chưa có tiêu đề)\nBắt đầu: ")
    assert "Lượt" not in as_text.text


def test_transcript_trailing_question_without_answer(client, db):
    conversation_id = create(client)
    seed_messages(db, conversation_id, TWO_TURNS + [("user", "Câu hỏi chưa được trả lời")])

    r = client.get(transcript_url(conversation_id))
    text = client.get(transcript_url(conversation_id), params={"format": "text"}).text

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total_messages"] == 5 and len(data["turns"]) == 3
    last = data["turns"][2]
    assert last["turn"] == 3 and last["user"]["content"] == "Câu hỏi chưa được trả lời" and last["assistant"] is None
    assert text.endswith("Lượt 3\n  Người dùng: Câu hỏi chưa được trả lời\n  Trợ lý:\n")


def test_transcript_consecutive_user_messages_are_kept(client, db):
    conversation_id = create(client)
    seed_messages(db, conversation_id, [("user", "Một"), ("user", "Hai"), ("assistant", "Đáp hai")])

    turns = client.get(transcript_url(conversation_id)).json()["data"]["turns"]

    assert [(t["user"]["content"], t["assistant"] and t["assistant"]["content"]) for t in turns] == [
        ("Một", None), ("Hai", "Đáp hai"),
    ]


def test_transcript_longer_than_500_messages_is_truncated(client, db):
    conversation_id = create(client)
    items = [(role, f"{role} {i}") for i in range(1, 256) for role in ("user", "assistant")]  # 510 tin
    seed_messages(db, conversation_id, items)

    data = client.get(transcript_url(conversation_id)).json()["data"]

    assert data["truncated"] is True and data["total_messages"] == 510
    assert len(data["turns"]) == 250  # 500 tin đầu = 250 lượt
    assert data["turns"][0]["user"]["content"] == "user 1"
    assert data["turns"][-1]["assistant"]["content"] == "assistant 250"


def test_transcript_exactly_500_messages_is_not_truncated(client, db):
    conversation_id = create(client)
    seed_messages(db, conversation_id, [(role, f"{role} {i}") for i in range(250) for role in ("user", "assistant")])

    data = client.get(transcript_url(conversation_id)).json()["data"]

    assert data["truncated"] is False and data["total_messages"] == 500 and len(data["turns"]) == 250
