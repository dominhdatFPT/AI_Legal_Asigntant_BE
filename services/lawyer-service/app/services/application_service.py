"""Đơn xin làm luật sư (new) / đổi thông tin thẻ (card_change) của người dùng.

draft → pending → approved | rejected; draft/pending → withdrawn. Mỗi người một đơn draft/pending.
Nháp được để trống trường; đủ điều kiện kiểm tra khi gửi (và khi sửa đơn đã pending).
"""

import uuid
from datetime import timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import reapply_cooldown
from app.core.responses import (
    ACCOUNT_LOCKED,
    APPLICATION_NOT_DRAFT,
    APPLICATION_NOT_EDITABLE,
    APPLICATION_NOT_FOUND,
    BAR_CARD_NUMBER_TAKEN,
    CARD_NOT_SET,
    CITIZEN_PROFILE_REQUIRED,
    FORBIDDEN,
    LAWYER_NOT_FOUND,
    LAWYER_PROFILE_EXISTS,
    OPEN_APPLICATION_EXISTS,
    REAPPLY_COOLDOWN,
    UNAUTHORIZED,
    VALIDATION_ERROR,
    ApiError,
)
from app.models.lawyer import (
    CARD_CHANGE,
    DRAFT,
    HIDDEN,
    NEW,
    OPEN_STATUSES,
    PENDING,
    WITHDRAWN,
    LawyerApplication,
    as_utc,
    utcnow,
)
from app.repositories import lawyer_repository as repo
from app.schemas.lawyer import ApplicationCreateRequest, ApplicationUpdateRequest
from app.services import document_client, user_client
from app.services.views import application_out, pagination

PROFILE_FIELDS = ("law_firm", "years_experience", "bio", "specialization_codes")
VN_TIME = timezone(timedelta(hours=7))


def _out(db: Session, application: LawyerApplication) -> dict:
    return application_out(application, repo.specialization_names(db))


def _own(db: Session, user_id: uuid.UUID, application_id: uuid.UUID, for_update: bool = False) -> LawyerApplication:
    application = repo.get_application(db, application_id, for_update=for_update)
    if application is None or application.user_id != user_id:
        raise ApiError(404, APPLICATION_NOT_FOUND)
    return application


def _reject_profile_fields(kind: str, changes: dict) -> None:
    """Đơn card_change chỉ mang số thẻ / ảnh thẻ."""
    if kind != CARD_CHANGE:
        return
    extra = [f for f in PROFILE_FIELDS if changes.get(f) is not None]
    if extra:
        raise ApiError(422, VALIDATION_ERROR, details=[
            {"field": f, "message": "Đơn đổi thông tin thẻ chỉ gồm số thẻ và ảnh thẻ; sửa trường này ở hồ sơ luật sư."}
            for f in extra])


def _attach_card(application: LawyerApplication, user_id: uuid.UUID, card_id: uuid.UUID | None) -> None:
    """Kiểm tra ảnh thẻ thuộc người gửi rồi lưu JSON OCR từ Document Service."""
    if card_id is None:
        application.card_document_id = None
        application.ocr_result = None
        return
    card = document_client.get_lawyer_card(card_id)
    if card is None or card["owner_user_id"] != user_id:
        raise ApiError(422, VALIDATION_ERROR, details=[
            {"field": "card_document_id", "message": "Ảnh thẻ không tồn tại hoặc không thuộc về bạn."}])
    application.card_document_id = card_id
    application.ocr_result = card["ocr_result"]


def _missing_for_review(application: LawyerApplication) -> list[dict]:
    details = []
    if application.card_document_id is None:
        details.append({"field": "card_document_id", "message": "Cần tải ảnh thẻ luật sư."})
    if application.type == NEW:
        if not application.bar_card_number:
            details.append({"field": "bar_card_number", "message": "Cần nhập số thẻ hành nghề."})
        if not application.specialization_codes:
            details.append({"field": "specialization_codes", "message": "Phải chọn ít nhất 1 lĩnh vực."})
    return details


def _ensure_ready(db: Session, application: LawyerApplication) -> None:
    details = _missing_for_review(application)
    if details:
        raise ApiError(422, VALIDATION_ERROR, "Đơn còn thiếu thông tin, vui lòng bổ sung các ô được đánh dấu.",
                       details=details)
    if application.bar_card_number and repo.bar_card_taken(db, application.bar_card_number, application.user_id):
        raise ApiError(409, BAR_CARD_NUMBER_TAKEN)


def _ensure_profile_allows(db: Session, user_id: uuid.UUID, kind: str) -> None:
    profile = repo.get_profile_by_user(db, user_id)
    if kind == NEW:
        if profile is not None and profile.status != HIDDEN:
            raise ApiError(409, LAWYER_PROFILE_EXISTS)
    else:
        if profile is None or profile.status == HIDDEN:
            raise ApiError(404, LAWYER_NOT_FOUND)
        if profile.bar_card_number is None:
            raise ApiError(409, CARD_NOT_SET)


def _ensure_role(role: str, kind: str) -> None:
    # Xin làm luật sư: citizen. Đổi thẻ: luật sư (token đã có role lawyer).
    if role != ("citizen" if kind == NEW else "lawyer"):
        raise ApiError(403, FORBIDDEN)


def create(db: Session, user_id: uuid.UUID, role: str, body: ApplicationCreateRequest) -> dict:
    _ensure_role(role, body.type)
    changes = body.model_dump(exclude_unset=True)
    _reject_profile_fields(body.type, changes)
    if repo.get_open_application(db, user_id) is not None:
        raise ApiError(409, OPEN_APPLICATION_EXISTS)
    _ensure_profile_allows(db, user_id, body.type)

    application = LawyerApplication(user_id=user_id, type=body.type, status=DRAFT,
                                    bar_card_number=body.bar_card_number)
    if body.type == NEW:
        application.law_firm = body.law_firm
        application.years_experience = body.years_experience
        application.bio = body.bio
        application.specialization_codes = body.specialization_codes
    _attach_card(application, user_id, body.card_document_id)
    db.add(application)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, OPEN_APPLICATION_EXISTS) from None
    return _out(db, application)


def update(db: Session, user_id: uuid.UUID, application_id: uuid.UUID, body: ApplicationUpdateRequest) -> dict:
    application = _own(db, user_id, application_id, for_update=True)
    if application.status not in OPEN_STATUSES:
        raise ApiError(409, APPLICATION_NOT_EDITABLE)
    changes = body.model_dump(exclude_unset=True)
    _reject_profile_fields(application.type, changes)

    try:
        if "card_document_id" in changes:
            _attach_card(application, user_id, changes.pop("card_document_id"))
        for field, value in changes.items():
            setattr(application, field, value)
        if application.status == PENDING:
            _ensure_ready(db, application)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, BAR_CARD_NUMBER_TAKEN) from None
    except ApiError:
        db.rollback()  # không để thay đổi dở dang trong session
        raise
    return _out(db, application)


def _ensure_cooldown_over(db: Session, user_id: uuid.UUID) -> None:
    last = repo.latest_rejection(db, user_id)
    if last is None or last.reviewed_at is None:
        return
    available_at = as_utc(last.reviewed_at) + reapply_cooldown()
    if utcnow() < available_at:
        when = available_at.astimezone(VN_TIME).strftime("%H:%M %d/%m/%Y")
        raise ApiError(409, REAPPLY_COOLDOWN,
                       f"Đơn trước của bạn vừa bị từ chối, vui lòng chờ đến {when} rồi gửi lại.")


def submit(db: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> dict:
    application = _own(db, user_id, application_id, for_update=True)
    if application.status != DRAFT:
        raise ApiError(409, APPLICATION_NOT_DRAFT)
    _ensure_profile_allows(db, user_id, application.type)
    _ensure_ready(db, application)
    _ensure_cooldown_over(db, user_id)

    status = user_client.get_status(user_id)
    if status is None:
        raise ApiError(401, UNAUTHORIZED)
    if status["is_locked"]:
        raise ApiError(403, ACCOUNT_LOCKED)
    if not status["has_citizen_profile"]:
        raise ApiError(409, CITIZEN_PROFILE_REQUIRED)
    citizen = user_client.get_citizen_profile(user_id)
    if citizen is None:
        raise ApiError(409, CITIZEN_PROFILE_REQUIRED)

    application.applicant_full_name = (citizen.get("full_name") or None) if isinstance(citizen, dict) else None
    application.status = PENDING
    application.submitted_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, BAR_CARD_NUMBER_TAKEN) from None
    return _out(db, application)


def withdraw(db: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> dict:
    application = _own(db, user_id, application_id, for_update=True)
    if application.status not in OPEN_STATUSES:
        raise ApiError(409, APPLICATION_NOT_EDITABLE)
    application.status = WITHDRAWN
    application.withdrawn_at = utcnow()
    db.commit()
    return _out(db, application)


def get_mine(db: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> dict:
    return _out(db, _own(db, user_id, application_id))


def list_mine(db: Session, user_id: uuid.UUID, page: int, page_size: int) -> dict:
    items, total = repo.list_user_applications(db, user_id, page, page_size)
    names = repo.specialization_names(db)
    return {"items": [application_out(a, names) for a in items], "pagination": pagination(page, page_size, total)}
