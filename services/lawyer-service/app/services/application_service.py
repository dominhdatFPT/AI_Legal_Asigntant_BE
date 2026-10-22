"""Hồ sơ xin làm luật sư của người dùng (trang lawyer-registration).

Gửi là vào hàng chờ duyệt luôn (pending → approved | rejected). Bị từ chối thì sửa và gửi lại: tạo đơn mới,
đơn cũ giữ làm lịch sử. Mỗi người tối đa một đơn pending.
"""

import uuid
from datetime import timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import reapply_cooldown
from app.core.responses import (
    ACCOUNT_LOCKED,
    APPLICATION_NOT_EDITABLE,
    BAR_CARD_NUMBER_TAKEN,
    CITIZEN_PROFILE_REQUIRED,
    FORBIDDEN,
    LAWYER_NOT_FOUND,
    LAWYER_PROFILE_EXISTS,
    REAPPLY_COOLDOWN,
    UNAUTHORIZED,
    ApiError,
)
from app.models.lawyer import HIDDEN, NEW, PENDING, REJECTED, LawyerApplication, as_utc, utcnow
from app.repositories import lawyer_repository as repo
from app.schemas.lawyer import CARD_CREDENTIAL, LawyerSubmitRequest
from app.services import document_client, user_client
from app.services.views import lawyer_view

VN_TIME = timezone(timedelta(hours=7))


def get_mine(db: Session, user_id: uuid.UUID) -> dict:
    application = repo.latest_application(db, user_id)
    if application is None:
        raise ApiError(404, LAWYER_NOT_FOUND)
    return lawyer_view(application, repo.specialization_names(db))


def _ensure_cooldown_over(db: Session, user_id: uuid.UUID) -> None:
    last = repo.latest_rejection(db, user_id)
    if last is None or last.reviewed_at is None:
        return
    available_at = as_utc(last.reviewed_at) + reapply_cooldown()
    if utcnow() < available_at:
        when = available_at.astimezone(VN_TIME).strftime("%H:%M %d/%m/%Y")
        raise ApiError(409, REAPPLY_COOLDOWN,
                       f"Hồ sơ trước của bạn vừa bị từ chối, vui lòng chờ đến {when} rồi gửi lại.")


def _applicant_full_name(user_id: uuid.UUID) -> str | None:
    """Tài khoản còn dùng được và đã khai CCCD; trả họ tên trên CCCD để admin xem."""
    status = user_client.get_status(user_id)
    if status is None:
        raise ApiError(401, UNAUTHORIZED)
    if status["is_locked"]:
        raise ApiError(403, ACCOUNT_LOCKED)
    citizen = user_client.get_citizen_profile(user_id) if status["has_citizen_profile"] else None
    if not isinstance(citizen, dict):
        raise ApiError(409, CITIZEN_PROFILE_REQUIRED)
    return citizen.get("full_name") or None


def submit(db: Session, user_id: uuid.UUID, role: str, body: LawyerSubmitRequest, access_token: str,
           *, resubmit: bool) -> dict:
    """resubmit=False: POST /lawyers (lần đầu). resubmit=True: PUT /lawyers/me (sau khi bị từ chối)."""
    if role != "citizen":
        raise ApiError(403, FORBIDDEN)
    latest = repo.latest_application(db, user_id)
    if resubmit:
        if latest is None:
            raise ApiError(404, LAWYER_NOT_FOUND)
        if latest.status != REJECTED:
            raise ApiError(409, APPLICATION_NOT_EDITABLE)
        _ensure_cooldown_over(db, user_id)
    elif latest is not None:
        # shortcut: luật sư bị hạ vai trò (hồ sơ hidden) chưa xin lại được vì đơn approved cũ vẫn là đơn mới nhất;
        # làm khi có luồng "xin hành nghề lại".
        raise ApiError(409, LAWYER_PROFILE_EXISTS)
    profile = repo.get_profile_by_user(db, user_id)
    if profile is not None and profile.status != HIDDEN:
        raise ApiError(409, LAWYER_PROFILE_EXISTS)
    if repo.bar_card_taken(db, body.bar_card_number, user_id):
        raise ApiError(409, BAR_CARD_NUMBER_TAKEN)

    full_name = _applicant_full_name(user_id)
    document_client.ensure_owned([c.document_id for c in body.credentials], access_token)

    card = next(c for c in body.credentials if c.type == CARD_CREDENTIAL)
    application = LawyerApplication(
        user_id=user_id, type=NEW, status=PENDING, submitted_at=utcnow(),
        bar_card_number=body.bar_card_number, law_firm=body.law_firm, years_experience=body.years_experience,
        bio=body.bio, specialization_codes=body.specialization_codes, card_document_id=card.document_id,
        credentials=[c.model_dump(mode="json") for c in body.credentials], applicant_full_name=full_name)
    db.add(application)
    try:
        db.commit()
    except IntegrityError:
        # Hai request gửi cùng lúc: chặn bởi unique index (một đơn pending / người, số thẻ pending).
        db.rollback()
        if repo.get_open_application(db, user_id) is not None:
            raise ApiError(409, LAWYER_PROFILE_EXISTS) from None
        raise ApiError(409, BAR_CARD_NUMBER_TAKEN) from None
    return lawyer_view(application, repo.specialization_names(db))
