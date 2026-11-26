# CRUD cho conversations và messages (SQLAlchemy, Session đồng bộ như user-service)
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Conversation, Message

TITLE_MAX_LENGTH = 80
TRANSCRIPT_MAX_MESSAGES = 500  # trần an toàn của kịch bản hội thoại: dài hơn thì chỉ lấy 500 tin đầu


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def is_expired(conversation: Conversation, cutoff: datetime | None) -> bool:
    """True nếu hội thoại đã quá mốc thời gian không hoạt động (`updated_at <= cutoff`)."""
    return cutoff is not None and _as_utc(conversation.updated_at) <= _as_utc(cutoff)


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
    db: Session, user_id: uuid.UUID, page: int = 1, page_size: int = 20, *, cutoff: datetime | None = None,
) -> tuple[list[Conversation], int]:
    """Hội thoại của 1 user, mới nhất trước. Trả về (danh sách của trang, tổng số).
    Khi truyền `cutoff`, tự loại các hội thoại đã quá hạn không hoạt động (`updated_at <= cutoff`)."""
    # 1 câu: trang + tổng (window count tính trước LIMIT); chỉ đếm riêng khi trang vượt cuối danh sách.
    conditions = [Conversation.user_id == user_id]
    if cutoff is not None:
        conditions.append(Conversation.updated_at > _as_utc(cutoff))
    rows = db.execute(
        select(Conversation, func.count().over().label("total"))
        .where(*conditions)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    if rows:
        total = rows[0].total
    elif page > 1:
        total = db.scalar(select(func.count()).select_from(Conversation).where(*conditions))
    else:
        total = 0
    return [row[0] for row in rows], total or 0


def get_conversation(db: Session, conversation_id: uuid.UUID) -> Conversation | None:
    """Không lọc theo user: endpoint tự kiểm tra để phân biệt 404 (không có) và 403 (của người khác)."""
    return db.get(Conversation, conversation_id)


def get_conversation_of_user(
    db: Session, conversation_id: uuid.UUID, user_id: uuid.UUID, *, cutoff: datetime | None = None,
) -> Conversation | None:
    """Hội thoại có đúng `id` VÀ đúng chủ (và còn hạn nếu truyền `cutoff`). Không có hoặc của người khác đều trả None."""
    conditions = [Conversation.id == conversation_id, Conversation.user_id == user_id]
    if cutoff is not None:
        conditions.append(Conversation.updated_at > _as_utc(cutoff))
    return db.scalar(select(Conversation).where(*conditions))


def get_messages_before(
    db: Session, conversation_id: uuid.UUID, until: datetime, limit: int | None = None,
) -> list[Message]:
    """Tin nhắn có created_at < `until`, cũ → mới. Có `limit` thì chỉ lấy `limit` tin gần `until` nhất."""
    until = until.astimezone(timezone.utc)  # cùng múi giờ với created_at đã lưu (UTC)
    query = select(Message).where(Message.conversation_id == conversation_id, Message.created_at < until)
    if limit is None:
        return list(db.scalars(query.order_by(Message.created_at, Message.id)).all())
    recent = db.scalars(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)).all()
    return list(reversed(recent))


def delete_conversation(db: Session, conversation: Conversation) -> None:
    db.delete(conversation)  # tin nhắn bị xóa theo (CASCADE)
    db.commit()


def delete_expired_conversations(
    db: Session, cutoff: datetime, batch_size: int = 200,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Xóa tối đa `batch_size` hội thoại có `updated_at <= cutoff` (tin nhắn xóa theo ON DELETE CASCADE).
    Kiểm lại điều kiện `updated_at <= cutoff` ngay trong lệnh DELETE để tránh xóa nhầm hội thoại vừa có tin mới.
    Trả về danh sách `(user_id, conversation_id)` đã thực sự xóa khỏi DB."""
    limit = max(1, batch_size)
    bound = _as_utc(cutoff)
    ids = list(
        db.scalars(
            select(Conversation.id)
            .where(Conversation.updated_at <= bound)
            .order_by(Conversation.updated_at.asc(), Conversation.id.asc())
            .limit(limit)
        ).all()
    )
    if not ids:
        return []
    deleted = db.execute(
        delete(Conversation)
        .where(Conversation.id.in_(ids), Conversation.updated_at <= bound)
        .returning(Conversation.user_id, Conversation.id)
        .execution_options(synchronize_session=False)
    ).all()
    db.commit()
    db.expire_all()
    return [(row[0], row[1]) for row in deleted]


def get_messages(db: Session, conversation_id: uuid.UUID, limit: int | None = None) -> list[Message]:
    """Tin nhắn theo thứ tự cũ → mới. Có `limit` thì chỉ lấy `limit` tin nhắn gần nhất."""
    query = select(Message).where(Message.conversation_id == conversation_id)
    if limit is None:
        return list(db.scalars(query.order_by(Message.created_at, Message.id)).all())
    recent = db.scalars(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)).all()
    return list(reversed(recent))


def get_messages_for_transcript(
    db: Session, conversation_id: uuid.UUID, limit: int = TRANSCRIPT_MAX_MESSAGES,
) -> tuple[list[Message], int]:
    """Kịch bản hội thoại: tối đa `limit` tin ĐẦU TIÊN (cũ → mới, bằng thời gian thì theo id cho ổn định), không áp
    giới hạn history_max_messages. Trả về (tin nhắn, tổng số tin của hội thoại); tổng lớn hơn số tin trả về nghĩa là đã bị cắt."""
    count = select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
    rows = db.execute(
        select(Message, func.count().over().label("total"))
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
        .limit(limit)
    ).all()
    # Không có dòng nào thì hội thoại rỗng (trừ khi limit <= 0 — khi đó đếm riêng).
    total = rows[0].total if rows else (db.scalar(count) if limit <= 0 else 0)
    return [row[0] for row in rows], total or 0


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
    conversation = db.merge(conversation)
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
    # Load all response fields even for sessions that expire on commit. The chat
    # caller closes the read transaction from refresh before writing Redis history.
    db.refresh(user_message)
    db.refresh(assistant_message)
    return user_message, assistant_message
