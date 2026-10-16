"""Việc của admin: tạo tài khoản luật sư, xem / đếm / duyệt / từ chối đơn."""

import logging
import uuid

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.responses import (
    APPLICANT_UNAVAILABLE,
    APPLICATION_NOT_FOUND,
    APPLICATION_NOT_PENDING,
    BAR_CARD_NUMBER_TAKEN,
    LAWYER_DB_UNAVAILABLE,
    ApiError,
)
from app.models.lawyer import (
    ACTIVE,
    ADMIN_CREATED,
    APPROVED,
    DRAFT,
    HIDDEN,
    INCOMPLETE,
    NEW,
    PENDING,
    REJECTED,
    SELF_APPLIED,
    LawyerApplication,
    LawyerProfile,
    utcnow,
)
from app.repositories import lawyer_repository as repo
from app.schemas.lawyer import CreateLawyerAccountRequest
from app.services import document_client, profile_service, user_client
from app.services.text_match import is_mismatch, normalize_card, normalize_name
from app.services.views import application_out, pagination, profile_out

logger = logging.getLogger("lawyer-service")


def create_lawyer_account(db: Session, admin_id: uuid.UUID, body: CreateLawyerAccountRequest) -> dict:
    """Tạo tài khoản role lawyer ở User Service rồi hồ sơ incomplete; lưu hồ sơ lỗi thì xóa cứng tài khoản."""
    user = user_client.create_lawyer_account(body.email, body.password, body.full_name, admin_id)
    user_id = uuid.UUID(str(user["id"]))
    profile = LawyerProfile(user_id=user_id, source=ADMIN_CREATED, status=INCOMPLETE)
    try:
        db.add(profile)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.error("Lưu hồ sơ luật sư lỗi, xóa cứng tài khoản vừa tạo user_id=%s", user_id)
        try:
            user_client.hard_delete_user(user_id)
        except ApiError as e:
            # Không xóa được: tài khoản lawyer không có hồ sơ, cần xử lý tay (chạy 008_export_lawyer_profiles.sql).
            logger.error("KHÔNG xóa cứng được tài khoản user_id=%s (%s), cần xử lý tay", user_id, e.code)
        raise ApiError(503, LAWYER_DB_UNAVAILABLE,
                       "Không lưu được hồ sơ luật sư, tài khoản chưa được tạo. Vui lòng thử lại sau.") from None
    complete, missing = profile_service.evaluate(profile, False)
    return {
        "user": {"id": user_id, "email": user["email"], "full_name": user.get("full_name"), "role": user["role"]},
        "profile": profile_out(profile, repo.specialization_names(db), is_complete=complete, missing=missing,
                               pending=None),
    }


def _reviewable(db: Session, application_id: uuid.UUID, for_update: bool = False) -> LawyerApplication:
    """Admin không bao giờ thấy nháp."""
    application = repo.get_application(db, application_id, for_update=for_update)
    if application is None or application.status == DRAFT:
        raise ApiError(404, APPLICATION_NOT_FOUND)
    return application


def list_applications(db: Session, status: str, type_: str | None, order: str, page: int, page_size: int) -> dict:
    items, total = repo.list_admin_applications(db, status, type_, order, page, page_size)
    names = repo.specialization_names(db)
    return {"items": [application_out(a, names) for a in items], "pagination": pagination(page, page_size, total)}


def summary(db: Session, type_: str | None) -> dict:
    return repo.count_admin_applications(db, type_)


def review(db: Session, application_id: uuid.UUID) -> dict:
    application = _reviewable(db, application_id)
    names = repo.specialization_names(db)
    citizen = user_client.get_citizen_profile(application.user_id)
    card_image = document_client.get_view_url(application.card_document_id) if application.card_document_id else None

    profile = repo.get_profile_by_user(db, application.user_id)
    current = None
    if profile is not None:
        complete, missing = profile_service.evaluate(profile, citizen is not None)
        current = profile_out(profile, names, is_complete=complete, missing=missing,
                              pending=repo.get_open_application(db, profile.user_id))

    ocr_fields = (application.ocr_result or {}).get("fields") or {}
    number = application.bar_card_number or (profile.bar_card_number if profile else None)
    return {
        "application": application_out(application, names),
        "card_image": card_image,
        "citizen_profile": citizen,
        "current_profile": current,
        "checks": {
            "is_name_mismatch": is_mismatch((citizen or {}).get("full_name"), ocr_fields.get("full_name"),
                                            normalize_name),
            "is_card_number_mismatch": is_mismatch(number, ocr_fields.get("bar_card_number"), normalize_card),
        },
    }


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


def _apply_card_change(db: Session, application: LawyerApplication) -> None:
    profile = repo.get_profile_by_user(db, application.user_id, for_update=True)
    if profile is None or profile.status == HIDDEN:
        raise ApiError(409, APPLICANT_UNAVAILABLE)
    if application.bar_card_number:
        profile.bar_card_number = application.bar_card_number
    profile.card_document_id = application.card_document_id
    profile.card_ocr_result = application.ocr_result


def approve(db: Session, admin_id: uuid.UUID, application_id: uuid.UUID) -> dict:
    application = _reviewable(db, application_id, for_update=True)
    if application.status != PENDING:
        raise ApiError(409, APPLICATION_NOT_PENDING)
    if application.bar_card_number and repo.bar_card_taken(db, application.bar_card_number, application.user_id):
        raise ApiError(409, BAR_CARD_NUMBER_TAKEN)

    if application.type == NEW:
        # Đổi vai trò trước; lỗi thì đơn vẫn pending. Lưu DB lỗi sau bước này: admin duyệt lại (đổi vai trò lặp lại an toàn).
        user_client.promote_to_lawyer(application.user_id)
        _apply_new(db, application)
    else:
        _apply_card_change(db, application)
    application.status = APPROVED
    application.reviewed_by = admin_id
    application.reviewed_at = utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, BAR_CARD_NUMBER_TAKEN) from None
    return application_out(application, repo.specialization_names(db))


def reject(db: Session, admin_id: uuid.UUID, application_id: uuid.UUID, reason: str) -> dict:
    application = _reviewable(db, application_id, for_update=True)
    if application.status != PENDING:
        raise ApiError(409, APPLICATION_NOT_PENDING)
    application.status = REJECTED
    application.reject_reason = reason
    application.reviewed_by = admin_id
    application.reviewed_at = utcnow()
    db.commit()
    return application_out(application, repo.specialization_names(db))
