"""Test API nội bộ tóm tắt hội thoại: SQLite trong RAM, LLM giả lập (không gọi mạng)."""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models import Message
from app.repositories import conversation as repo
from app.services import llm_client, prompts, summary

USER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
TOKEN = {"X-Internal-Token": "test-internal-token"}  # khớp INTERNAL_TOKEN trong tests/conftest.py
T0 = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
UNTIL = "2026-11-02T09:00:00Z"
SUMMARY_TEXT = "Người dùng hỏi về thủ tục tách thửa đất. Gợi ý của trợ lý AI: cần xem quy định địa phương."


def url(conversation_id):
    return f"/internal/v1/agent/conversations/{conversation_id}/summary"


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def llm(monkeypatch):
    """LLM giả: ghi lại mọi lượt gọi; test đặt `llm.error` để mô phỏng lỗi."""
    class Fake:
        calls: list[dict] = []
        error: Exception | None = None

    fake = Fake()
    fake.calls = []

    async def fake_generate(system_prompt, history, user_content, **options):
        fake.calls.append({"system_prompt": system_prompt, "history": history, "user_content": user_content, **options})
        if fake.error:
            raise fake.error
        return SUMMARY_TEXT, 100, "model-test"

    monkeypatch.setattr(llm_client, "generate", fake_generate)
    return fake


def add_messages(db, conversation, items):
    """items: (role, content, created_at)"""
    for role, content, created_at in items:
        db.add(Message(conversation_id=conversation.id, role=role, content=content, created_at=created_at))
    db.commit()


@pytest.fixture
def conversation(db):
    conv = repo.create_conversation(db, USER_A)
    add_messages(db, conv, [
        ("user", "Tôi muốn tách thửa đất cho con.", T0),
        ("assistant", "Cần xem điều kiện tách thửa tại địa phương.", T0 + timedelta(minutes=1)),
        ("user", "TIN-SAU-MOC: tôi muốn hỏi chuyện khác.", T0 + timedelta(minutes=90)),
        ("assistant", "TRA-LOI-SAU-MOC", T0 + timedelta(minutes=91)),
    ])
    return conv


def post(client, conversation_id, user_id=USER_A, until=UNTIL, headers=None, **extra):
    body = {"user_id": str(user_id), "until": until, **extra}
    return client.post(url(conversation_id), json=body, headers={**TOKEN, "X-Request-ID": "req-123", **(headers or {})})


# ── Thành công ─────────────────────────────────────────────────────────────────────────────────────────


def test_summary_uses_only_messages_before_until(client, db, conversation, llm):
    r = post(client, conversation.id)

    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == "req-123"
    assert r.json() == {
        "data": {"conversation_id": str(conversation.id), "summary": SUMMARY_TEXT},
        "error": None,
        "request_id": "req-123",
    }
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["system_prompt"] == prompts.SUMMARY_SYSTEM_PROMPT
    assert call["history"] == []
    # Chỉ hai tin trước mốc, đúng thứ tự cũ → mới, có nhãn vai
    assert call["user_content"] == (
        "Người dùng: Tôi muốn tách thửa đất cho con.\n\n"
        "Trợ lý AI: Cần xem điều kiện tách thửa tại địa phương."
    )
    assert "SAU-MOC" not in call["user_content"]


def test_message_exactly_at_until_is_excluded(client, db, llm):
    conv = repo.create_conversation(db, USER_A)
    until = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
    add_messages(db, conv, [
        ("user", "TRUOC-MOC", until - timedelta(seconds=1)),
        ("user", "DUNG-MOC", until),
    ])

    assert post(client, conv.id).status_code == 200
    assert "TRUOC-MOC" in llm.calls[0]["user_content"]
    assert "DUNG-MOC" not in llm.calls[0]["user_content"]


def test_until_with_utc_offset_is_converted(client, db, llm):
    conv = repo.create_conversation(db, USER_A)
    add_messages(db, conv, [
        ("user", "TRUOC-MOC", datetime(2026, 11, 2, 1, 59, tzinfo=timezone.utc)),
        ("user", "SAU-MOC", datetime(2026, 11, 2, 2, 1, tzinfo=timezone.utc)),
    ])

    # 09:00 giờ +07:00 = 02:00 UTC
    assert post(client, conv.id, until="2026-11-02T09:00:00+07:00").status_code == 200
    assert "TRUOC-MOC" in llm.calls[0]["user_content"]
    assert "SAU-MOC" not in llm.calls[0]["user_content"]


def test_llm_options_stay_under_consultation_timeout(client, conversation, llm):
    post(client, conversation.id)

    call = llm.calls[0]
    assert call["max_retries"] == 0
    assert call["timeout_seconds"] < 60
    assert summary.TOTAL_TIMEOUT_SECONDS < 60


def test_summary_is_not_stored_and_can_be_repeated(client, db, conversation, llm):
    before = len(repo.get_messages(db, conversation.id))

    first = post(client, conversation.id)
    second = post(client, conversation.id)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(repo.get_messages(db, conversation.id)) == before
    assert len(llm.calls) == 2


def test_request_id_is_generated_when_missing(client, conversation, llm):
    r = client.post(url(conversation.id), json={"user_id": str(USER_A), "until": UNTIL}, headers=TOKEN)

    assert r.status_code == 200
    assert r.json()["request_id"].startswith("req-")
    assert r.headers["X-Request-ID"] == r.json()["request_id"]


# ── Xác thực ───────────────────────────────────────────────────────────────────────────────────────────


def test_missing_token_returns_401(client, conversation, llm):
    r = client.post(url(conversation.id), json={"user_id": str(USER_A), "until": UNTIL})

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert r.json()["data"] is None
    assert llm.calls == []


def test_wrong_token_returns_401(client, conversation, llm):
    r = post(client, conversation.id, headers={"X-Internal-Token": "sai-token"})

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert llm.calls == []


def test_jwt_is_not_accepted(client, conversation, llm):
    r = client.post(
        url(conversation.id), json={"user_id": str(USER_A), "until": UNTIL},
        headers={"Authorization": "Bearer abc"},
    )

    assert r.status_code == 401


@pytest.mark.parametrize("sent", [{"X-Internal-Token": ""}, {"X-Internal-Token": "test-internal-token"}, {}])
def test_empty_server_token_blocks_everything(client, conversation, llm, monkeypatch, sent):
    monkeypatch.setenv("INTERNAL_TOKEN", "")
    get_settings.cache_clear()

    r = client.post(url(conversation.id), json={"user_id": str(USER_A), "until": UNTIL}, headers=sent)

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert llm.calls == []


def test_token_is_checked_before_body(client, conversation, llm):
    r = client.post(url(conversation.id), json={"bad": 1}, headers={"X-Internal-Token": "sai-token"})

    assert r.status_code == 401


# ── 404: không lộ hội thoại có tồn tại hay không ───────────────────────────────────────────────────────


def test_unknown_other_user_and_deleted_return_the_same_404(client, db, conversation, llm):
    gone = repo.create_conversation(db, USER_A)
    repo.delete_conversation(db, gone)

    responses = [
        post(client, uuid.uuid4()),  # không tồn tại
        post(client, conversation.id, user_id=USER_B),  # của user khác
        post(client, gone.id),  # đã xóa
    ]

    for r in responses:
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert len({r.text for r in responses}) == 1
    assert llm.calls == []


# ── 422 ────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body, field", [
    ({"until": UNTIL}, "user_id"),
    ({"user_id": str(USER_A)}, "until"),
    ({"user_id": "khong-phai-uuid", "until": UNTIL}, "user_id"),
    ({"user_id": str(USER_A), "until": "hôm qua"}, "until"),
    ({"user_id": str(USER_A), "until": "2026-11-02T09:00:00"}, "until"),  # thiếu múi giờ
    ({}, "user_id"),
])
def test_invalid_body_returns_validation_error(client, conversation, llm, body, field):
    r = client.post(url(conversation.id), json=body, headers={**TOKEN, "X-Request-ID": "req-9"})

    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert field in [d["field"] for d in error["details"]]
    assert r.json()["request_id"] == "req-9"
    assert llm.calls == []


def test_invalid_conversation_id_returns_validation_error(client, llm):
    r = client.post(url("khong-phai-uuid"), json={"user_id": str(USER_A), "until": UNTIL}, headers=TOKEN)

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_no_messages_before_until_returns_conversation_empty(client, db, llm):
    conv = repo.create_conversation(db, USER_A)
    add_messages(db, conv, [("user", "Gửi sau mốc", T0 + timedelta(hours=5))])

    r = post(client, conv.id)

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CONVERSATION_EMPTY"
    assert llm.calls == []


def test_conversation_without_messages_returns_conversation_empty(client, db, llm):
    conv = repo.create_conversation(db, USER_A)

    r = post(client, conv.id)

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CONVERSATION_EMPTY"
    assert llm.calls == []


# ── LLM lỗi ────────────────────────────────────────────────────────────────────────────────────────────


def test_llm_error_returns_503_ai_service_error(client, conversation, llm):
    llm.error = llm_client.LLMError("Tất cả provider thất bại")

    r = post(client, conversation.id)

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "AI_SERVICE_ERROR"
    assert r.json()["data"] is None


def test_llm_timeout_returns_503_ai_service_error(client, conversation, monkeypatch):
    async def slow_generate(*args, **options):
        await asyncio.sleep(5)

    monkeypatch.setattr(llm_client, "generate", slow_generate)
    monkeypatch.setattr(summary, "TOTAL_TIMEOUT_SECONDS", 0.05)

    r = post(client, conversation.id)

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "AI_SERVICE_ERROR"


def test_empty_llm_answer_returns_503(client, conversation, monkeypatch):
    async def blank_generate(*args, **options):
        return "   ", 1, "model-test"

    monkeypatch.setattr(llm_client, "generate", blank_generate)

    r = post(client, conversation.id)

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "AI_SERVICE_ERROR"


# ── Log không chứa nội dung ────────────────────────────────────────────────────────────────────────────


def test_logs_do_not_contain_message_or_summary_text(client, db, llm, caplog):
    conv = repo.create_conversation(db, USER_A)
    add_messages(db, conv, [("user", "NOI-DUNG-BI-MAT-CUA-USER", T0)])
    caplog.set_level(logging.DEBUG)

    assert post(client, conv.id).status_code == 200
    llm.error = llm_client.LLMError("loi")
    assert post(client, conv.id).status_code == 503

    assert "NOI-DUNG-BI-MAT-CUA-USER" not in caplog.text
    assert SUMMARY_TEXT not in caplog.text
    assert str(conv.id) in caplog.text
    assert "req-123" in caplog.text


# ── Giới hạn đầu vào ───────────────────────────────────────────────────────────────────────────────────


def test_build_transcript_drops_oldest_messages_when_too_long():
    text = "x" * 2000
    messages = [("user", f"{i:03d}{text}") for i in range(30)]  # 30 tin x ~2000 ký tự > MAX_TRANSCRIPT_CHARS

    transcript, used = summary.build_transcript(messages)

    assert used < 30
    assert len(transcript) <= summary.MAX_TRANSCRIPT_CHARS
    assert "029" in transcript  # giữ tin mới nhất
    assert "000" not in transcript  # bỏ tin cũ nhất
    kept = [i for i in range(30) if f"Người dùng: {i:03d}" in transcript]
    assert kept == list(range(30 - used, 30))  # giữ liền một đoạn cuối, vẫn theo thứ tự cũ → mới


def test_build_transcript_clips_a_very_long_message_and_keeps_the_newest():
    transcript, used = summary.build_transcript([("assistant", "y" * 100000)])

    assert used == 1
    assert len(transcript) < summary.MAX_MESSAGE_CHARS + 100
    assert transcript.endswith(prompts.TRUNCATED_MARK)


def test_only_most_recent_messages_are_loaded_from_db(client, db, llm, monkeypatch):
    monkeypatch.setattr(summary, "MAX_MESSAGES", 3)
    conv = repo.create_conversation(db, USER_A)
    add_messages(db, conv, [("user", f"TIN-{i}", T0 + timedelta(minutes=i)) for i in range(6)])

    assert post(client, conv.id).status_code == 200
    content = llm.calls[0]["user_content"]
    assert "TIN-5" in content and "TIN-3" in content
    assert "TIN-2" not in content
    assert content.index("TIN-3") < content.index("TIN-5")
