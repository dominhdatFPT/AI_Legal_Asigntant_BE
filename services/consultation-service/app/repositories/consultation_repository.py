"""Truy vấn database của Consultation Service. Không commit ở đây: service quyết định ranh giới transaction."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select, text, tuple_, update
from sqlalchemy.orm import Session

from app.core.config import RETENTION
from app.models.consultation import (
    CLOSED,
    IN_PROGRESS,
    WAITING,
    Consultation,
    ConsultationMessage,
    ConsultationSummary,
    ConsultationTermsAcceptance,
)

_NO_SYNC = {"synchronize_session": False}


# ---------------------------------------------------------------- Điều kiện dùng chung
def waiting_alive(now: datetime):
    """Yêu cầu chờ chưa quá hạn 10 ngày. Dùng ở hàng chờ, đếm giới hạn và accept để ba nơi luôn cùng một luật."""
    return and_(Consultation.status == WAITING, Consultation.created_at > now - RETENTION)


def get_visible(db: Session, consultation_id: uuid.UUID, now: datetime) -> Consultation | None:
    """Cuộc tư vấn API còn nhìn thấy: chưa xóa nội dung, và nếu đang chờ thì chưa quá hạn."""
    return db.scalar(select(Consultation).where(
        Consultation.id == consultation_id, Consultation.purged_at.is_(None),
        or_(Consultation.status != WAITING, waiting_alive(now))))


def lock_user(db: Session, space: int, user_id: uuid.UUID) -> None:
    """Khóa advisory theo user (1 = phía người tạo, 2 = phía luật sư); luôn lấy citizen trước luật sư."""
    # Chỉ PostgreSQL có advisory lock; test dùng SQLite một kết nối nên không có tranh chấp để chặn.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("select pg_advisory_xact_lock(:space, hashtext(:user_id))"),
                   {"space": space, "user_id": str(user_id)})


# ---------------------------------------------------------------- Đếm
def count_waiting(db: Session, citizen_id: uuid.UUID, now: datetime) -> int:
    return db.scalar(select(func.count()).select_from(Consultation).where(
        Consultation.citizen_user_id == citizen_id, waiting_alive(now))) or 0


def count_open_as_lawyer(db: Session, lawyer_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(Consultation).where(
        Consultation.lawyer_user_id == lawyer_id, Consultation.status == IN_PROGRESS)) or 0


def open_pair_exists(db: Session, citizen_id: uuid.UUID, lawyer_id: uuid.UUID) -> bool:
    return db.scalar(select(Consultation.id).where(
        Consultation.citizen_user_id == citizen_id, Consultation.lawyer_user_id == lawyer_id,
        Consultation.status == IN_PROGRESS).limit(1)) is not None


# ---------------------------------------------------------------- Ghi cuộc tư vấn
def accept_waiting(db: Session, consultation_id: uuid.UUID, lawyer_id: uuid.UUID, now: datetime) -> int:
    """Số dòng đổi: 0 = đã có người nhận / bị xóa / hết hạn."""
    return db.execute(
        update(Consultation).where(Consultation.id == consultation_id, waiting_alive(now))
        .values(status=IN_PROGRESS, lawyer_user_id=lawyer_id, accepted_at=now, last_activity_at=now, updated_at=now)
        .execution_options(**_NO_SYNC)).rowcount


def delete_waiting_of(db: Session, citizen_id: uuid.UUID) -> list[uuid.UUID]:
    rows = db.execute(delete(Consultation).where(
        Consultation.citizen_user_id == citizen_id, Consultation.status == WAITING)
        .returning(Consultation.id).execution_options(**_NO_SYNC))
    return [r[0] for r in rows]


def close_open(db: Session, consultation_id: uuid.UUID, reason: str, closed_by: uuid.UUID | None,
               now: datetime) -> int:
    """Chỉ đóng được cuộc đang in_progress; không đổi last_activity_at."""
    return db.execute(
        update(Consultation).where(Consultation.id == consultation_id, Consultation.status == IN_PROGRESS)
        .values(status=CLOSED, close_reason=reason, closed_by=closed_by, closed_at=now, updated_at=now)
        .execution_options(**_NO_SYNC)).rowcount


def touch_open(db: Session, consultation_id: uuid.UUID, now: datetime) -> int:
    """Cập nhật last_activity_at khi có tin mới. 0 dòng = cuộc không còn in_progress.

    UPDATE có điều kiện giữ khóa dòng đến hết transaction nên một cuộc vừa bị đóng không nhận thêm tin
    (cùng mục đích với FOR SHARE); khác FOR SHARE ở chỗ hai bên gửi cùng lúc chỉ xếp hàng chứ không deadlock.
    """
    return db.execute(
        update(Consultation).where(Consultation.id == consultation_id, Consultation.status == IN_PROGRESS)
        .values(last_activity_at=now, updated_at=now).execution_options(**_NO_SYNC)).rowcount


# ---------------------------------------------------------------- Danh sách
def _page(db: Session, stmt, order, page: int, page_size: int) -> tuple[list[Consultation], int]:
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
    return list(rows), total


def list_mine(db: Session, user_id: uuid.UUID, status: str | None, now: datetime, page: int, page_size: int):
    stmt = select(Consultation).where(
        or_(Consultation.citizen_user_id == user_id, Consultation.lawyer_user_id == user_id),
        Consultation.purged_at.is_(None),
        or_(Consultation.status != WAITING, waiting_alive(now)))
    if status:
        stmt = stmt.where(Consultation.status == status)
    return _page(db, stmt, (Consultation.updated_at.desc(), Consultation.id), page, page_size)


def list_lobby(db: Session, lawyer_id: uuid.UUID, now: datetime, page: int, page_size: int):
    stmt = select(Consultation).where(waiting_alive(now), Consultation.citizen_user_id != lawyer_id)
    return _page(db, stmt, (Consultation.created_at.asc(), Consultation.id), page, page_size)


# ---------------------------------------------------------------- Tin nhắn
def _cursor(m: ConsultationMessage):
    return tuple_(ConsultationMessage.created_at, ConsultationMessage.id), (m.created_at, m.id)


def get_message(db: Session, consultation_id: uuid.UUID, message_id: uuid.UUID) -> ConsultationMessage | None:
    return db.scalar(select(ConsultationMessage).where(
        ConsultationMessage.id == message_id, ConsultationMessage.consultation_id == consultation_id))


def latest_message(db: Session, consultation_id: uuid.UUID) -> ConsultationMessage | None:
    return db.scalar(select(ConsultationMessage).where(ConsultationMessage.consultation_id == consultation_id)
                     .order_by(ConsultationMessage.created_at.desc(), ConsultationMessage.id.desc()).limit(1))


def messages_page(db: Session, consultation_id: uuid.UUID, limit: int, before: ConsultationMessage | None,
                  after: ConsultationMessage | None) -> tuple[list[ConsultationMessage], bool]:
    """Tin theo thứ tự cũ → mới. Lấy limit + 1 dòng để biết còn nữa hay không (has_more)."""
    stmt = select(ConsultationMessage).where(ConsultationMessage.consultation_id == consultation_id)
    if after is not None:
        column, value = _cursor(after)
        rows = db.scalars(stmt.where(column > value).order_by(
            ConsultationMessage.created_at, ConsultationMessage.id).limit(limit + 1)).all()
        return list(rows[:limit]), len(rows) > limit
    if before is not None:
        column, value = _cursor(before)
        stmt = stmt.where(column < value)
    rows = db.scalars(stmt.order_by(ConsultationMessage.created_at.desc(), ConsultationMessage.id.desc())
                      .limit(limit + 1)).all()
    return list(reversed(rows[:limit])), len(rows) > limit


def add_message(db: Session, consultation_id: uuid.UUID, sender_id: uuid.UUID, content: str,
                created_at: datetime) -> ConsultationMessage:
    message = ConsultationMessage(consultation_id=consultation_id, sender_id=sender_id, content=content,
                                  created_at=created_at)
    db.add(message)
    db.flush()
    return message


def last_messages(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, ConsultationMessage]:
    if not ids:
        return {}
    rank = func.row_number().over(
        partition_by=ConsultationMessage.consultation_id,
        order_by=(ConsultationMessage.created_at.desc(), ConsultationMessage.id.desc())).label("rank")
    ranked = select(ConsultationMessage.id, rank).where(ConsultationMessage.consultation_id.in_(ids)).subquery()
    rows = db.scalars(select(ConsultationMessage).join(ranked, ranked.c.id == ConsultationMessage.id)
                      .where(ranked.c.rank == 1))
    return {m.consultation_id: m for m in rows}


def unread_counts(db: Session, ids: list[uuid.UUID], viewer_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Số tin của người kia mà `viewer_id` chưa đọc; tin của chính mình không tính."""
    if not ids:
        return {}
    rows = db.execute(select(ConsultationMessage.consultation_id, func.count()).where(
        ConsultationMessage.consultation_id.in_(ids), ConsultationMessage.sender_id != viewer_id,
        ConsultationMessage.is_read.is_(False)).group_by(ConsultationMessage.consultation_id))
    return {cid: n for cid, n in rows}


def mark_read(db: Session, consultation_id: uuid.UUID, viewer_id: uuid.UUID) -> None:
    """Đánh dấu đã đọc mọi tin của bên kia; tin của chính mình không đổi."""
    db.execute(update(ConsultationMessage).where(
        ConsultationMessage.consultation_id == consultation_id, ConsultationMessage.sender_id != viewer_id,
        ConsultationMessage.is_read.is_(False)).values(is_read=True).execution_options(**_NO_SYNC))


# ---------------------------------------------------------------- Điều khoản
def has_accepted_terms(db: Session, user_id: uuid.UUID, version: str) -> bool:
    return db.get(ConsultationTermsAcceptance, (user_id, version)) is not None


def add_terms_acceptance(db: Session, user_id: uuid.UUID, version: str, now: datetime) -> None:
    db.add(ConsultationTermsAcceptance(user_id=user_id, terms_version=version, accepted_at=now))
    db.flush()


# ---------------------------------------------------------------- Tóm tắt AI
def get_summary(db: Session, consultation_id: uuid.UUID) -> str | None:
    return db.scalar(select(Consultation.ai_summary).where(Consultation.id == consultation_id,
                                                           Consultation.purged_at.is_(None)))


def save_summary(db: Session, consultation_id: uuid.UUID, text_: str) -> int:
    """Lưu một lần: chạy trùng thì chỉ bản đầu được ghi; cuộc đã xóa nội dung thì không ghi gì."""
    return db.execute(update(Consultation).where(
        Consultation.id == consultation_id, Consultation.ai_summary.is_(None), Consultation.purged_at.is_(None),
    ).values(ai_summary=text_), execution_options=_NO_SYNC).rowcount


# ---------------------------------------------------------------- Tóm tắt hội thoại
def _unsummarized():
    return and_(ConsultationMessage.consultation_id == Consultation.id,
                or_(Consultation.summarized_until.is_(None), ConsultationMessage.created_at > Consultation.summarized_until))


def summary_candidates(db: Session, now: datetime, idle: timedelta, batch: int, limit: int) -> list[uuid.UUID]:
    """Cuộc có tin chưa tóm tắt và: đã đóng, hoặc im lặng đủ `idle`, hoặc chưa tóm tắt đã đủ `batch` tin."""
    pending = select(func.count()).where(_unsummarized()).correlate(Consultation).scalar_subquery()
    return list(db.scalars(select(Consultation.id).where(
        Consultation.purged_at.is_(None), Consultation.status != WAITING, pending > 0,
        or_(Consultation.status == CLOSED, Consultation.last_activity_at <= now - idle, pending >= batch),
    ).order_by(Consultation.last_activity_at).limit(limit)))


def unsummarized_messages(db: Session, consultation_id: uuid.UUID, since: datetime | None,
                          limit: int) -> list[ConsultationMessage]:
    stmt = select(ConsultationMessage).where(ConsultationMessage.consultation_id == consultation_id)
    if since is not None:
        stmt = stmt.where(ConsultationMessage.created_at > since)
    return list(db.scalars(stmt.order_by(ConsultationMessage.created_at, ConsultationMessage.id).limit(limit)))


def latest_summary(db: Session, consultation_id: uuid.UUID) -> dict | None:
    return db.scalar(select(ConsultationSummary.summary).where(ConsultationSummary.consultation_id == consultation_id)
                     .order_by(ConsultationSummary.window_end.desc()).limit(1))


def save_window_summary(db: Session, consultation_id: uuid.UUID, since: datetime | None, window_end: datetime,
                        message_count: int, summary: dict, schema_version: int, prompt_version: str | None) -> bool:
    """Ghi biên bản và đẩy mốc `summarized_until` trong cùng transaction. False = mốc đã bị bản khác đẩy trước
    (hai bản service cùng tóm tắt một đoạn): không ghi gì, người gọi rollback."""
    moved = db.execute(update(Consultation).where(
        Consultation.id == consultation_id, Consultation.purged_at.is_(None),
        Consultation.summarized_until.is_not_distinct_from(since),
    ).values(summarized_until=window_end).execution_options(**_NO_SYNC)).rowcount
    if moved == 0:
        return False
    db.add(ConsultationSummary(consultation_id=consultation_id, window_start=since, window_end=window_end,
                               message_count=message_count, summary=summary, schema_version=schema_version,
                               prompt_version=prompt_version))
    db.flush()
    return True
