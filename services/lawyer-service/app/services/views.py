"""Dựng dữ liệu trả về theo schema LawyerProfile trong openapi.yaml (trang lawyer-registration + tab duyệt của admin)."""

from app.models.lawyer import LawyerApplication, as_utc


def specializations_out(codes: list[str] | None, names: dict[str, str]) -> list[dict]:
    return [{"code": c, "name": names.get(c, c)} for c in codes or []]


def lawyer_view(application: LawyerApplication, names: dict[str, str]) -> dict:
    """Một hồ sơ xin làm luật sư; `id` là mã đơn, dùng cho /lawyers/{id}/approve | reject."""
    return {
        "id": application.id,
        "user_id": application.user_id,
        "full_name": application.applicant_full_name,
        "approval_status": application.status,
        "rejected_reason": application.reject_reason,
        "bar_card_number": application.bar_card_number,
        "law_firm": application.law_firm,
        "years_experience": application.years_experience,
        "bio": application.bio,
        "specializations": specializations_out(application.specialization_codes, names),
        "credentials": application.credentials or [],
        "reviewed_at": as_utc(application.reviewed_at),
        "created_at": as_utc(application.submitted_at or application.created_at),
        "updated_at": as_utc(application.updated_at),
    }


def pagination(page: int, page_size: int, total: int) -> dict:
    return {"page": page, "page_size": page_size, "total": total,
            "total_pages": -(-total // page_size) if total else 0}
