"""Nghiệp vụ cuộc tư vấn, theo docs/api/CONSULTATION_DESIGN.md. Gọi service khác (HTTP) trước, rồi mới mở
transaction và lấy khóa, để không giữ khóa database trong lúc chờ mạng."""

import logging
import uuid
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import Caller
from app.core import database
from app.core.config import MAX_OPEN_PER_LAWYER, MAX_WAITING_PER_USER, terms_version
from app.core.responses import (
    CANNOT_ACCEPT_OWN_CONSULTATION,
    CONSULTATION_ALREADY_ACCEPTED,
    CONSULTATION_ALREADY_OPEN,
    CONSULTATION_INVALID_STATUS,
    CONSULTATION_NOT_FOUND,
    DIRECT_NOT_ALLOWED,
    FORBIDDEN,
    LAWYER_CAPACITY_REACHED,
    LAWYER_NOT_APPROVED,
    SUMMARY_NOT_AVAILABLE,
    TERMS_NOT_ACCEPTED,
    WAITING_LIMIT_REACHED,
    ApiError,
    invalid,
)
from app.models.consultation import (
    COMPLETED,
    IN_PROGRESS,
    LOBBY,
    WAITING,
    Consultation,
    as_utc,
    utcnow,
)
from app.repositories import consultation_repository as repo
from app.services import agent_client, lawyer_client, user_client
from app.services.views import consultation_view, consultation_views, message_view, paginated

logger = logging.getLogger("consultation-service")

CITIZEN_SIDE, LAWYER_SIDE = 1, 2  # hai không gian khóa advisory, xem CONSULTATION_DESIGN.md mục 5


def _not_found() -> ApiError:
    return ApiError(404, CONSULTATION_NOT_FOUND)


def _require_user_role(caller: Caller) -> None:
    """Luật sư được dùng tính năng của người dân; admin thì không."""
    if caller.role not in ("citizen", "lawyer"):
        raise ApiError(403, FORBIDDEN)


def _require_terms(db: Session, caller: Caller) -> None:
    if not repo.has_accepted_terms(db, caller.id, terms_version()):
        raise ApiError(403, TERMS_NOT_ACCEPTED)


def require_practicing_lawyer(caller: Caller, access_token: str) -> None:
    if caller.role != "lawyer":
        raise ApiError(403, FORBIDDEN)
    user_client.ensure_active(access_token)
    if not lawyer_client.can_practice(caller.id):
        raise ApiError(403, LAWYER_NOT_APPROVED)


def _participant(db: Session, caller: Caller, consultation_id: uuid.UUID) -> Consultation:
    """Cuộc tư vấn mà người gọi là người tạo hoặc luật sư của nó; còn lại (kể cả admin) là 404."""
    c = repo.get_visible(db, consultation_id, utcnow())
    if c is None or caller.id not in (c.citizen_user_id, c.lawyer_user_id):
        raise _not_found()
    return c


def _is_open_pair_conflict(error: IntegrityError) -> bool:
    # Lỗi unique consultations_open_pair_key (SQLite không nêu tên index, chỉ nói UNIQUE).
    text = str(error.orig)
    return "consultations_open_pair_key" in text or "UNIQUE" in text.upper()


# ---------------------------------------------------------------- Điều khoản
def terms_status(db: Session, caller: Caller) -> dict:
    version = terms_version()
    return {"version": version, "accepted": repo.has_accepted_terms(db, caller.id, version)}


def accept_terms(db: Session, caller: Caller, version: str) -> dict:
    current = terms_version()
    if version != current:
        raise invalid("terms_version", "Không phải phiên bản điều khoản hiện hành. Vui lòng tải lại trang.")
    if not repo.has_accepted_terms(db, caller.id, current):
        try:
            repo.add_terms_acceptance(db, caller.id, current, utcnow())
            db.commit()
        except IntegrityError:  # hai tab đồng ý cùng lúc: bản của tab kia đã ghi, giữ accepted_at lần đầu
            db.rollback()
    return {"version": current, "accepted": True}


# ---------------------------------------------------------------- Tạo yêu cầu
def create(db: Session, caller: Caller, body) -> dict:
    _require_user_role(caller)
    if body.source == "direct":
        if body.lawyer_id is None:
            raise invalid("lawyer_id", "Bắt buộc khi source là direct.")
        raise ApiError(422, DIRECT_NOT_ALLOWED)  # TODO Giai đoạn 3
    _require_terms(db, caller)

    now = utcnow()
    repo.lock_user(db, CITIZEN_SIDE, caller.id)
    if repo.count_waiting(db, caller.id, now) >= MAX_WAITING_PER_USER:
        raise ApiError(409, WAITING_LIMIT_REACHED)
    consultation = Consultation(
        citizen_user_id=caller.id, source=LOBBY, agent_session_id=body.agent_session_id, question=body.question,
        status=WAITING, last_activity_at=now, created_at=now, updated_at=now)
    db.add(consultation)
    db.commit()
    return consultation_view(db, consultation, caller.id)


# ---------------------------------------------------------------- Xem
def list_mine(db: Session, caller: Caller, status: str | None, page: int, page_size: int) -> dict:
    _require_user_role(caller)
    rows, total = repo.list_mine(db, caller.id, status, utcnow(), page, page_size)
    return paginated(consultation_views(db, rows, caller.id), total, page, page_size)


def lobby(db: Session, caller: Caller, access_token: str, page: int, page_size: int) -> dict:
    require_practicing_lawyer(caller, access_token)
    rows, total = repo.list_lobby(db, caller.id, utcnow(), page, page_size)
    return paginated(consultation_views(db, rows, caller.id), total, page, page_size)


def get_one(db: Session, caller: Caller, access_token: str, consultation_id: uuid.UUID) -> dict:
    c = repo.get_visible(db, consultation_id, utcnow())
    if c is None:
        raise _not_found()
    if caller.id not in (c.citizen_user_id, c.lawyer_user_id):
        # Người ngoài chỉ xem được yêu cầu đang chờ, và chỉ khi là luật sư được hành nghề.
        if c.status != WAITING or caller.role != "lawyer":
            raise _not_found()
        try:
            require_practicing_lawyer(caller, access_token)
        except ApiError as e:
            if e.status == 403:
                raise _not_found() from None
            raise
    return consultation_view(db, c, caller.id)


# ---------------------------------------------------------------- Nhận yêu cầu
def accept(db: Session, caller: Caller, access_token: str, consultation_id: uuid.UUID) -> tuple[dict, list[uuid.UUID]]:
    """Trả (cuộc tư vấn, id các yêu cầu chờ khác của citizen vừa bị xóa)."""
    require_practicing_lawyer(caller, access_token)
    _require_terms(db, caller)
    now = utcnow()
    c = repo.get_visible(db, consultation_id, now)
    if c is None:
        raise _not_found()
    if c.status != WAITING:
        raise ApiError(409, CONSULTATION_ALREADY_ACCEPTED)
    if c.citizen_user_id == caller.id:
        raise ApiError(409, CANNOT_ACCEPT_OWN_CONSULTATION)

    try:
        repo.lock_user(db, CITIZEN_SIDE, c.citizen_user_id)
        repo.lock_user(db, LAWYER_SIDE, caller.id)
        if repo.count_open_as_lawyer(db, caller.id) >= MAX_OPEN_PER_LAWYER:
            raise ApiError(409, LAWYER_CAPACITY_REACHED)
        if repo.open_pair_exists(db, c.citizen_user_id, caller.id):
            raise ApiError(409, CONSULTATION_ALREADY_OPEN)
        if repo.accept_waiting(db, c.id, caller.id, now) == 0:
            raise ApiError(409, CONSULTATION_ALREADY_ACCEPTED)
        removed = repo.delete_waiting_of(db, c.citizen_user_id)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        if _is_open_pair_conflict(e):
            raise ApiError(409, CONSULTATION_ALREADY_OPEN) from None
        raise
    except ApiError:
        db.rollback()
        raise
    db.refresh(c)
    return consultation_view(db, c, caller.id), removed


# ---------------------------------------------------------------- Tin nhắn
def list_messages(db: Session, caller: Caller, consultation_id: uuid.UUID, limit: int,
                  before: uuid.UUID | None, after: uuid.UUID | None) -> dict:
    c = _participant(db, caller, consultation_id)
    if before is not None and after is not None:
        raise invalid("before", "Không dùng chung với after.")
    cursors = {}
    for name, value in (("before", before), ("after", after)):
        cursors[name] = None
        if value is not None:
            cursors[name] = repo.get_message(db, c.id, value)
            if cursors[name] is None:
                raise invalid(name, "Không tìm thấy tin nhắn này trong cuộc tư vấn.")
    items, has_more = repo.messages_page(db, c.id, limit, cursors["before"], cursors["after"])
    return {"items": [message_view(m) for m in items], "has_more": has_more}


def mark_read(db: Session, caller: Caller, consultation_id: uuid.UUID) -> dict:
    c = _participant(db, caller, consultation_id)
    if c.status == WAITING:  # yêu cầu đang chờ chưa có tin nhắn nào
        raise _not_found()
    repo.mark_read(db, c.id, caller.id)
    db.commit()
    return {"consultation_id": c.id, "unread_count": 0}


def send_message(db: Session, caller: Caller, access_token: str, consultation_id: uuid.UUID,
                 content: str) -> tuple[dict, uuid.UUID]:
    """Trả (tin nhắn, id người nhận) để API đẩy sự kiện sau khi commit."""
    c = _participant(db, caller, consultation_id)
    if c.status != IN_PROGRESS:
        raise ApiError(409, CONSULTATION_INVALID_STATUS)
    if caller.id == c.lawyer_user_id:
        user_client.ensure_active(access_token)  # luật sư bị khóa mất quyền gửi tin ngay

    now = utcnow()
    # UPDATE có điều kiện trước: giữ khóa dòng, nên cuộc vừa bị đóng ở transaction khác sẽ không nhận thêm tin.
    if repo.touch_open(db, c.id, now) == 0:
        db.rollback()
        raise ApiError(409, CONSULTATION_INVALID_STATUS)
    last = repo.latest_message(db, c.id)
    if last is not None:
        # Đồng hồ thô có thể cho hai tin cùng thời điểm; ép tăng dần để phân trang theo mốc không đảo thứ tự.
        now = max(now, as_utc(last.created_at) + timedelta(microseconds=1))
    message = repo.add_message(db, c.id, caller.id, content, now)
    db.commit()
    recipient = c.lawyer_user_id if caller.id == c.citizen_user_id else c.citizen_user_id
    return message_view(message), recipient


# ---------------------------------------------------------------- Đóng
def close(db: Session, caller: Caller, consultation_id: uuid.UUID) -> dict:
    c = _participant(db, caller, consultation_id)
    if c.status != IN_PROGRESS:
        raise ApiError(409, CONSULTATION_INVALID_STATUS)
    if repo.close_open(db, c.id, COMPLETED, caller.id, utcnow()) == 0:
        db.rollback()
        raise ApiError(409, CONSULTATION_INVALID_STATUS)
    db.commit()
    db.refresh(c)
    return consultation_view(db, c, caller.id)


# ---------------------------------------------------------------- Tóm tắt AI
def _generate_summary(db: Session, c: Consultation) -> str:
    """Gọi Agent tóm tắt các tin trước lúc gửi yêu cầu, lưu một lần, trả bản đang có trong database."""
    if c.agent_session_id is None:
        raise ApiError(404, SUMMARY_NOT_AVAILABLE)
    consultation_id = c.id
    args = (c.agent_session_id, c.citizen_user_id, as_utc(c.created_at))
    db.commit()  # trả kết nối về pool trước khi chờ LLM (tối đa 60 giây)
    text = agent_client.summarize(*args)
    if text is not None:
        repo.save_summary(db, consultation_id, text)
        db.commit()
    saved = repo.get_summary(db, consultation_id)
    if saved is None:
        raise ApiError(404, SUMMARY_NOT_AVAILABLE)
    return saved


def get_summary(db: Session, caller: Caller, access_token: str, consultation_id: uuid.UUID) -> dict:
    c = repo.get_visible(db, consultation_id, utcnow())
    if c is None or caller.id == c.citizen_user_id:
        raise _not_found()
    if caller.id != c.lawyer_user_id:
        # Người ngoài chỉ xem được khi yêu cầu còn chờ và họ là luật sư được hành nghề.
        if c.status != WAITING or caller.role != "lawyer":
            raise _not_found()
        try:
            require_practicing_lawyer(caller, access_token)
        except ApiError as e:
            if e.status == 403:
                raise _not_found() from None
            raise
    text = c.ai_summary if c.ai_summary is not None else _generate_summary(db, c)
    return {"consultation_id": consultation_id, "ai_summary": text}


def summarize_in_background(consultation_id: uuid.UUID, session_factory=None) -> None:
    """Tạo tóm tắt ngay sau khi gửi yêu cầu. Lỗi chỉ ghi log: GET .../summary sẽ tạo lại khi luật sư mở xem."""
    try:
        with (session_factory or database.new_session)() as db:
            c = repo.get_visible(db, consultation_id, utcnow())
            if c is not None and c.agent_session_id is not None and c.ai_summary is None:
                _generate_summary(db, c)
    except Exception as e:  # noqa: BLE001  việc nền không được làm hỏng gì
        logger.warning("Tạo tóm tắt nền lỗi consultation_id=%s: %s", consultation_id, type(e).__name__)
