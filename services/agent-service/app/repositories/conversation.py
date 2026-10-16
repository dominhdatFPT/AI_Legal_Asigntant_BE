# CRUD cho conversations và messages (SQLAlchemy, Session đồng bộ như user-service)
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Conversation, Message

TITLE_MAX_LENGTH = 80


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_title(question: str) -> str:
    """Tiêu đề tự sinh từ câu hỏi đầu tiên: gộp khoảng trắng, cắt còn tối đa 80 ký tự."""
    text = " ".join(question.split())
    if len(text) <= TITLE_MAX_LENGTH:
        return text
    return text[: TITLE_MAX_LENGTH - 1].rstrip() + "…"


def create_conversation(db: Session, user_id: uuid.UUID, title: str | None = None) -> Conversation:
    now = _now()
    conversation = Conversation(user_id=user_id, title=title, created_at=now, updated_at=now)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def list_conversations(
    db: Session, user_id: uuid.UUID, page: int = 1, page_size: int = 20,
) -> tuple[list[Conversation], int]:
    """Hội thoại của 1 user, mới nhất trước. Trả về (danh sách của trang, tổng số)."""
    total = db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
    )
    items = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(items), total or 0


def get_conversation(db: Session, conversation_id: uuid.UUID) -> Conversation | None:
    """Không lọc theo user: endpoint tự kiểm tra để phân biệt 404 (không có) và 403 (của người khác)."""
    return db.get(Conversation, conversation_id)


def delete_conversation(db: Session, conversation: Conversation) -> None:
    db.delete(conversation)  # tin nhắn bị xóa theo (CASCADE)
    db.commit()


def get_messages(db: Session, conversation_id: uuid.UUID, limit: int | None = None) -> list[Message]:
    """Tin nhắn theo thứ tự cũ → mới. Có `limit` thì chỉ lấy `limit` tin nhắn gần nhất."""
    query = select(Message).where(Message.conversation_id == conversation_id)
    if limit is None:
        return list(db.scalars(query.order_by(Message.created_at, Message.id)).all())
    recent = db.scalars(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)).all()
    return list(reversed(recent))


def add_message_pair(
    db: Session,
    conversation: Conversation,
    question: str,
    answer: str,
    answer_metadata: dict | None,
    asked_at: datetime,
) -> tuple[Message, Message]:
    """
    Lưu câu hỏi và câu trả lời trong cùng 1 transaction, cập nhật updated_at,
    tự sinh tiêu đề nếu hội thoại chưa có.
    """
    # Câu trả lời luôn sau câu hỏi để thứ tự tin nhắn không bị đảo khi trùng thời điểm
    answered_at = max(_now(), asked_at + timedelta(microseconds=1))
    user_message = Message(
        conversation_id=conversation.id, role="user", content=question, created_at=asked_at,
    )
    assistant_message = Message(
        conversation_id=conversation.id, role="assistant", content=answer,
        meta=answer_metadata, created_at=answered_at,
    )
    if not conversation.title:
        conversation.title = make_title(question)
    conversation.updated_at = answered_at

    db.add_all([user_message, assistant_message])
    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    return user_message, assistant_message
