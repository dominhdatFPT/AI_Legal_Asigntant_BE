"""Việc của admin: xem danh sách, duyệt / từ chối hồ sơ xin làm luật sư."""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.responses import (
    APPLICATION_NOT_FOUND,
    CREDENTIAL_NOT_FOUND,
    APPLICATION_NOT_PENDING,
    BAR_CARD_NUMBER_TAKEN,
    ApiError,
)
from app.models.lawyer import (
    ACTIVE,
    APPROVED,
    DRAFT,
    NEW,
    PENDING,
    REJECTED,
    SELF_APPLIED,
    LawyerApplication,
    LawyerProfile,
    utcnow,
)
from app.repositories import lawyer_repository as repo
from app.services import document_client, profile_service, user_client
from app.services.views import lawyer_view, pagination


def _reviewable(db: Session, application_id: uuid.UUID) -> LawyerApplication:
    """Admin không bao giờ thấy nháp; chỉ xử lý đơn đang chờ duyệt."""
    application = repo.get_application(db, application_id, for_update=True)
    if application is None or application.status == DRAFT:
        raise ApiError(404, APPLICATION_NOT_FOUND)
    if application.status != PENDING:
        raise ApiError(409, APPLICATION_NOT_PENDING)
    return application


def credential_file(db: Session, application_id: uuid.UUID, document_id: uuid.UUID) -> tuple[bytes, str, str]:
    """(nội dung, Content-Type, Content-Disposition) của một giấy tờ trong hồ sơ; chỉ mở được giấy tờ đã gửi kèm hồ sơ."""
    application = repo.get_application(db, application_id)
    if application is None or application.status == DRAFT:
        raise ApiError(404, APPLICATION_NOT_FOUND)
    if str(document_id) not in {str(c.get("document_id")) for c in application.credentials or []}:
        raise ApiError(404, CREDENTIAL_NOT_FOUND)
    response = document_client.get_content(application.user_id, document_id)
    # Xem ngay trên trình duyệt (Document Service chỉ nhận PDF/JPG/PNG nên mở inline an toàn).
    disposition = response.headers.get("Content-Disposition", "").replace("attachment", "inline", 1) or "inline"
    return response.content, response.headers.get("Content-Type", "application/octet-stream"), disposition


def list_applications(db: Session, status: str, order: str, page: int, page_size: int) -> dict:
    items, total = repo.list_admin_applications(db, status, NEW, order, page, page_size)
    names = repo.specialization_names(db)
    return {"items": [lawyer_view(a, names) for a in items], "pagination": pagination(page, page_size, total)}


def _apply_new(db: Session, application: LawyerApplication) -> None:
    profile = repo.get_profile_by_user(db, application.user_id, for_update=True)
    if profile is None:
        profile = LawyerProfile(user_id=application.user_id, source=SELF_APPLIED)
        db.add(profile)
    profile.source = SELF_APPLIED
    profile.status = ACTIVE
    profile.hidden_at = None
    profile.hidden_reason = None
    profile.is_online = False
    profile.bar_card_number = application.bar_card_number
    profile.card_document_id = application.card_document_id
    profile.card_ocr_result = application.ocr_result
    profile.law_firm = application.law_firm
    profile.years_experience = application.years_experience
    profile.bio = application.bio
    profile_service.set_specializations(profile, application.specialization_codes or [])


def approve(db: Session, admin_id: uuid.UUID, application_id: uuid.UUID) -> dict:
    application = _reviewable(db, application_id)
    if application.bar_card_number and repo.bar_card_taken(db, application.bar_card_number, application.user_id):
        raise ApiError(409, BAR_CARD_NUMBER_TAKEN)

    # Đổi vai trò trước; lỗi thì đơn vẫn pending. Lưu DB lỗi sau bước này: admin duyệt lại (đổi vai trò lặp lại an toàn).
    user_client.promote_to_lawyer(application.user_id)
    _apply_new(db, application)
    application.status = APPROVED
    application.reviewed_by = admin_id
    application.reviewed_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, BAR_CARD_NUMBER_TAKEN) from None
    return lawyer_view(application, repo.specialization_names(db))


def reject(db: Session, admin_id: uuid.UUID, application_id: uuid.UUID, reason: str) -> dict:
    application = _reviewable(db, application_id)
    application.status = REJECTED
    application.reject_reason = reason
    application.reviewed_by = admin_id
    application.reviewed_at = utcnow()
    db.commit()
    return lawyer_view(application, repo.specialization_names(db))
