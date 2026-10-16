"""Dựng dữ liệu trả về theo schema trong openapi.yaml (LawyerProfile, LawyerApplication...)."""

from app.core.config import reapply_cooldown
from app.models.lawyer import REJECTED, LawyerApplication, LawyerProfile, as_utc


def specializations_out(codes: list[str] | None, names: dict[str, str]) -> list[dict] | None:
    if codes is None:
        return None
    return [{"code": c, "name": names.get(c, c)} for c in codes]


def application_ref(application: LawyerApplication | None) -> dict | None:
    if application is None:
        return None
    return {"id": application.id, "type": application.type, "status": application.status}


def profile_out(profile: LawyerProfile, names: dict[str, str], *, is_complete: bool, missing: list[str],
                pending: LawyerApplication | None) -> dict:
    return {
        "id": profile.id,
        "user_id": profile.user_id,
        "source": profile.source,
        "status": profile.status,
        "bar_card_number": profile.bar_card_number,
        "card_document_id": profile.card_document_id,
        "law_firm": profile.law_firm,
        "years_experience": profile.years_experience,
        "bio": profile.bio,
        "specializations": specializations_out(sorted(profile.specialization_codes), names),
        "is_online": profile.is_online,
        "is_complete": is_complete,
        "missing_requirements": missing,
        "pending_application": application_ref(pending),
        "hidden_at": as_utc(profile.hidden_at),
        "created_at": as_utc(profile.created_at),
        "updated_at": as_utc(profile.updated_at),
    }


def application_out(application: LawyerApplication, names: dict[str, str]) -> dict:
    reviewed_at = as_utc(application.reviewed_at)
    return {
        "id": application.id,
        "user_id": application.user_id,
        "type": application.type,
        "status": application.status,
        "bar_card_number": application.bar_card_number,
        "law_firm": application.law_firm,
        "years_experience": application.years_experience,
        "bio": application.bio,
        "specializations": specializations_out(application.specialization_codes, names),
        "card_document_id": application.card_document_id,
        "ocr_result": application.ocr_result,
        "applicant_full_name": application.applicant_full_name,
        "reject_reason": application.reject_reason,
        "reviewed_by": application.reviewed_by,
        "reviewed_at": reviewed_at,
        "submitted_at": as_utc(application.submitted_at),
        "withdrawn_at": as_utc(application.withdrawn_at),
        "can_reapply_at": reviewed_at + reapply_cooldown() if application.status == REJECTED and reviewed_at else None,
        "created_at": as_utc(application.created_at),
        "updated_at": as_utc(application.updated_at),
    }


def pagination(page: int, page_size: int, total: int) -> dict:
    return {"page": page, "page_size": page_size, "total": total,
            "total_pages": -(-total // page_size) if total else 0}
