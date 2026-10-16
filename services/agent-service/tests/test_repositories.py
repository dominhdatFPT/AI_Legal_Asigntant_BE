"""Test repositories với SQLite trong RAM."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Message
from app.repositories import conversation as repo

USER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
USER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")


def test_make_title():
    assert repo.make_title("  Thủ tục   sang tên\nđất  ") == "Thủ tục sang tên đất"
    long_title = repo.make_title("a " * 100)
    assert len(long_title) == repo.TITLE_MAX_LENGTH
    assert long_title.endswith("…")


def test_create_and_list_only_own_conversations_newest_first(db):
    first = repo.create_conversation(db, USER_A, "Cũ")
    second = repo.create_conversation(db, USER_A, "Mới")
    repo.create_conversation(db, USER_B, "Của người khác")
    first.updated_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    items, total = repo.list_conversations(db, USER_A)

    assert total == 2
    assert [c.id for c in items] == [second.id, first.id]


def test_list_pagination(db):
    for i in range(5):
        repo.create_conversation(db, USER_A, f"Hội thoại {i}")

    page2, total = repo.list_conversations(db, USER_A, page=2, page_size=2)

    assert total == 5
    assert len(page2) == 2


def test_add_message_pair_saves_both_sets_title_and_updated_at(db):
    conversation = repo.create_conversation(db, USER_A)
    created = conversation.updated_at
    asked_at = datetime.now(timezone.utc)

    user_msg, ai_msg = repo.add_message_pair(
        db, conversation, "Sang tên sổ đỏ cần gì?", "Theo Điều 188...",
        {"model": "gemini-test", "tokens_used": 10, "rag_chunk_ids": []}, asked_at,
    )

    assert (user_msg.role, user_msg.content, user_msg.meta) == ("user", "Sang tên sổ đỏ cần gì?", None)
    assert (ai_msg.role, ai_msg.meta["model"]) == ("assistant", "gemini-test")
    assert ai_msg.created_at >= user_msg.created_at
    assert conversation.title == "Sang tên sổ đỏ cần gì?"
    db.refresh(conversation)  # đọc lại từ DB để so sánh cùng kiểu datetime
    assert conversation.updated_at > created
    assert [m.role for m in repo.get_messages(db, conversation.id)] == ["user", "assistant"]


def test_title_is_not_overwritten(db):
    conversation = repo.create_conversation(db, USER_A, "Tiêu đề tự đặt")
    repo.add_message_pair(db, conversation, "Câu hỏi", "Trả lời", None, datetime.now(timezone.utc))
    assert conversation.title == "Tiêu đề tự đặt"


def test_get_messages_limit_returns_most_recent_in_order(db):
    conversation = repo.create_conversation(db, USER_A)
    start = datetime.now(timezone.utc)
    for i in range(3):
        repo.add_message_pair(db, conversation, f"Hỏi {i}", f"Đáp {i}", None, start + timedelta(seconds=i * 10))

    recent = repo.get_messages(db, conversation.id, limit=2)

    assert [m.content for m in recent] == ["Hỏi 2", "Đáp 2"]


def test_delete_conversation_cascades_messages(db):
    conversation = repo.create_conversation(db, USER_A)
    repo.add_message_pair(db, conversation, "Hỏi", "Đáp", None, datetime.now(timezone.utc))

    repo.delete_conversation(db, conversation)

    assert repo.get_conversation(db, conversation.id) is None
    assert db.query(Message).count() == 0
