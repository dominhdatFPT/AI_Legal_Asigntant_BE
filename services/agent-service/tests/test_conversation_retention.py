"""Test cơ chế tự động xóa hội thoại sau 3 ngày không chat (DB + history_cache + API)."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.auth import TokenPayload
from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models import Conversation, Message
from app.repositories import conversation as repo
from app.services import conversation_cleanup, history_cache

USER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
BASE = "/api/v1/agent/conversations"


def _seed_conversation(db, user_id: uuid.UUID, title: str, updated_at: datetime, with_message: bool = True) -> Conversation:
    conv = repo.create_conversation(db, user_id, title)
    if with_message:
        repo.add_message_pair(db, conv, f"Câu hỏi {title}", f"Trả lời {title}", None, updated_at - timedelta(seconds=5))
    conv.created_at = updated_at - timedelta(minutes=1)
    conv.updated_at = updated_at
    db.commit()
    db.refresh(conv)
    return conv


def _session_factory(db):
    @contextmanager
    def _factory():
        yield db

    return _factory


@pytest.fixture
def api_client(db):
    def fake_user():
        return TokenPayload(
            sub=str(USER_A), role="citizen", type="access",
            iss="legal-assistant-user-service", iat=0, exp=9999999999,
        )

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_purge_deletes_only_inactive_conversations_and_cascades_messages(db, monkeypatch):
    monkeypatch.setenv("HISTORY_CACHE_ENABLED", "true")
    monkeypatch.setenv("CACHE_BACKEND", "memory")
    get_settings.cache_clear()

    now = datetime.now(timezone.utc)
    expired = _seed_conversation(db, USER_A, "Quá hạn", now - timedelta(days=3, minutes=1))
    exact_boundary = _seed_conversation(db, USER_B, "Đúng 3 ngày", now - timedelta(days=3))
    still_alive = _seed_conversation(db, USER_A, "Còn hạn", now - timedelta(days=2, hours=23, minutes=59))
    expired_id, exact_id, alive_id = expired.id, exact_boundary.id, still_alive.id

    history_cache.put(USER_A, expired_id, [{"role": "user", "content": "old"}])
    history_cache.put(USER_A, alive_id, [{"role": "user", "content": "keep"}])

    deleted = conversation_cleanup.purge_expired_once(_session_factory(db), now=now)

    assert deleted == 2
    assert repo.get_conversation(db, expired_id) is None
    assert repo.get_conversation(db, exact_id) is None
    assert repo.get_conversation(db, alive_id) is not None
    # Tin nhắn của 2 hội thoại quá hạn phải bị xóa theo ON DELETE CASCADE, chỉ còn 2 tin của hội thoại còn hạn.
    remaining_messages = db.query(Message).all()
    assert len(remaining_messages) == 2
    assert {m.conversation_id for m in remaining_messages} == {alive_id}
    # Redis/memory history_cache chỉ xóa khóa của hội thoại đã xóa.
    assert history_cache.get(USER_A, expired_id) is None
    assert history_cache.get(USER_A, alive_id) == [{"role": "user", "content": "keep"}]


def test_purge_batches_multiple_rounds(db):
    now = datetime.now(timezone.utc)
    for i in range(5):
        _seed_conversation(db, USER_A, f"Cũ {i}", now - timedelta(days=4, minutes=i))
    fresh = _seed_conversation(db, USER_A, "Mới", now - timedelta(hours=1))
    fresh_id = fresh.id

    deleted = conversation_cleanup.purge_expired_once(_session_factory(db), now=now, batch_size=2)

    assert deleted == 5
    assert db.query(Conversation).count() == 1
    assert repo.get_conversation(db, fresh_id) is not None


def test_purge_disabled_when_retention_days_is_zero(db, monkeypatch):
    monkeypatch.setenv("CONVERSATION_RETENTION_DAYS", "0")
    get_settings.cache_clear()

    now = datetime.now(timezone.utc)
    old = _seed_conversation(db, USER_A, "Rất cũ", now - timedelta(days=30))
    old_id = old.id

    assert conversation_cleanup.retention_cutoff(now) is None
    assert conversation_cleanup.purge_expired_once(_session_factory(db), now=now) == 0
    assert repo.get_conversation(db, old_id) is not None


def test_api_hides_and_rejects_expired_conversations_before_sweep(api_client, db):
    now = datetime.now(timezone.utc)
    expired = _seed_conversation(db, USER_A, "Đã hết 3 ngày", now - timedelta(days=3, seconds=5))
    alive = _seed_conversation(db, USER_A, "Đang hoạt động", now - timedelta(days=1))
    other_expired = _seed_conversation(db, USER_B, "Hết hạn của B", now - timedelta(days=5))

    listed = api_client.get(BASE)
    assert listed.status_code == 200
    payload = listed.json()["data"]
    assert payload["pagination"]["total"] == 1
    assert [item["id"] for item in payload["items"]] == [str(alive.id)]

    for path in (
        f"{BASE}/{expired.id}",
        f"{BASE}/{expired.id}/transcript",
        f"{BASE}/{other_expired.id}",
    ):
        res = api_client.get(path)
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

    post_res = api_client.post(f"{BASE}/{expired.id}/messages", json={"content": "Xin chào"})
    assert post_res.status_code == 404
    assert post_res.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

    stream_res = api_client.post(f"{BASE}/{expired.id}/messages/stream", json={"content": "Xin chào"})
    assert stream_res.status_code == 404
    assert stream_res.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

    del_res = api_client.delete(f"{BASE}/{expired.id}")
    assert del_res.status_code == 404
    assert del_res.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

    internal_res = api_client.post(
        f"/internal/v1/agent/conversations/{expired.id}/summary",
        json={"user_id": str(USER_A), "until": now.isoformat()},
        headers={"X-Internal-Token": "test-internal-token"},
    )
    assert internal_res.status_code == 404
    assert internal_res.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def test_new_message_refreshes_updated_at_and_prevents_expiration(db):
    now = datetime.now(timezone.utc)
    conv = _seed_conversation(db, USER_A, "Sắp hết hạn", now - timedelta(days=2, hours=23))
    conv_id = conv.id
    # Gửi tin nhắn mới làm mới updated_at về hiện tại -> không bị xóa khi kiểm tra 2 giờ sau.
    repo.add_message_pair(db, conv, "Hỏi tiếp", "Đáp tiếp", None, now)

    deleted = conversation_cleanup.purge_expired_once(_session_factory(db), now=now + timedelta(hours=2))
    assert deleted == 0
    assert repo.get_conversation(db, conv_id) is not None
