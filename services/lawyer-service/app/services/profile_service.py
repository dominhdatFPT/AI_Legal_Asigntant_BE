"""Hồ sơ luật sư: kiểm tra "đủ", trạng thái nội bộ, ẩn hồ sơ.

Hồ sơ "đủ" = đã khai CCCD (User Service has_citizen_profile) + có số thẻ + ≥ 1 lĩnh vực.
CCCD nằm ở User Service nên `incomplete → active` được kiểm tra lười: mỗi lần Consultation
hỏi trạng thái, đủ thì chuyển active luôn. Không có đường active → incomplete (số thẻ không xóa được,
lĩnh vực luôn ≥ 1, CCCD không xóa được).
"""

import uuid

from sqlalchemy.orm import Session

from app.models.lawyer import (
    ACTIVE,
    HIDDEN,
    INCOMPLETE,
    WITHDRAWN,
    LawyerProfile,
    LawyerProfileSpecialization,
    utcnow,
)
from app.repositories import lawyer_repository as repo
from app.services import user_client

CITIZEN_PROFILE, BAR_CARD, SPECIALIZATIONS = "citizen_profile", "bar_card_number", "specializations"


def evaluate(profile: LawyerProfile, has_citizen_profile: bool) -> tuple[bool, list[str]]:
    """(đủ chưa, còn thiếu gì) - không gọi service khác, không sửa gì."""
    if profile.status == HIDDEN:
        return False, []
    if profile.status == ACTIVE:
        return True, []
    missing = []
    if not has_citizen_profile:
        missing.append(CITIZEN_PROFILE)
    if not profile.bar_card_number:
        missing.append(BAR_CARD)
    if not profile.specializations:
        missing.append(SPECIALIZATIONS)
    return not missing, missing


def refresh_completeness(profile: LawyerProfile) -> tuple[bool, list[str]]:
    """Hồ sơ incomplete: hỏi User Service has_citizen_profile; đủ thì chuyển active (bên gọi commit)."""
    if profile.status != INCOMPLETE:
        return evaluate(profile, True)
    status = user_client.get_status(profile.user_id)
    complete, missing = evaluate(profile, bool(status and status["has_citizen_profile"]))
    if complete:
        profile.status = ACTIVE
    return complete, missing


def set_specializations(profile: LawyerProfile, codes: list[str]) -> None:
    keep = set(codes)
    profile.specializations = [s for s in profile.specializations if s.specialization_code in keep]
    have = set(profile.specialization_codes)
    profile.specializations.extend(LawyerProfileSpecialization(specialization_code=c) for c in codes if c not in have)


def lawyer_status(db: Session, user_id: uuid.UUID) -> dict:
    """GET /internal/v1/lawyers/{user_id}/status cho Consultation Service."""
    profile = repo.get_profile_by_user(db, user_id)
    if profile is None:
        return {"user_id": user_id, "has_profile": False, "profile_id": None, "profile_status": None,
                "is_hidden": False, "is_complete": False, "missing_requirements": [], "can_practice": False}
    complete, missing = refresh_completeness(profile)
    db.commit()
    return {"user_id": user_id, "has_profile": True, "profile_id": profile.id, "profile_status": profile.status,
            "is_hidden": profile.status == HIDDEN, "is_complete": complete, "missing_requirements": missing,
            "can_practice": profile.status == ACTIVE}


def hide_profile(db: Session, user_id: uuid.UUID, reason: str) -> dict:
    """User Service gọi trước khi hạ vai trò / xóa luật sư. Gọi lại nhiều lần an toàn."""
    now = utcnow()
    profile = repo.get_profile_by_user(db, user_id, for_update=True)
    if profile is not None and profile.status != HIDDEN:
        profile.status = HIDDEN
        profile.hidden_at = now
        profile.hidden_reason = reason
        profile.is_online = False
    withdrawn = []
    for application in repo.open_applications(db, user_id):
        application.status = WITHDRAWN
        application.withdrawn_at = now
        withdrawn.append(application.id)
    db.commit()
    # TODO(consultation-service): đóng các cuộc tư vấn đang mở - User Service đã gọi
    # consultation_client.close_lawyer_consultations sau khi lưu việc hạ vai trò / xóa tài khoản.
    return {"user_id": user_id, "profile_found": profile is not None,
            "is_hidden": profile is not None and profile.status == HIDDEN,
            "withdrawn_application_ids": withdrawn}

