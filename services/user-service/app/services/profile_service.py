"""Hồ sơ cá nhân theo CCCD: đọc và lưu (tạo mới hoặc ghi đè)."""

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.responses import CCCD_ALREADY_USED, PHONE_IN_USE, VALIDATION_ERROR, ApiError
from app.models import CitizenProfile, User
from app.repositories import citizen_profile_repository as profiles
from app.repositories import user_repository as users
from app.schemas.citizen_profile import Address, CitizenProfileOut, CitizenProfileRequest


def to_out(profile: CitizenProfile, user: User) -> CitizenProfileOut:
    current = None
    if not profile.current_same_as_permanent and profile.current_detail is not None:
        current = Address(detail=profile.current_detail, ward=profile.current_ward or "",
                          province=profile.current_province or "")
    return CitizenProfileOut(
        user_id=profile.user_id,
        id_number=profile.id_number,
        full_name=profile.full_name,
        date_of_birth=profile.date_of_birth,
        gender=profile.gender,
        nationality=profile.nationality,
        issue_date=profile.issue_date,
        issue_place=profile.issue_place,
        expiry_date=profile.expiry_date,
        place_of_origin=profile.place_of_origin,
        place_of_residence=profile.place_of_residence,
        permanent_address=Address(detail=profile.permanent_detail, ward=profile.permanent_ward,
                                  province=profile.permanent_province),
        current_same_as_permanent=profile.current_same_as_permanent,
        current_address=current,
        phone=user.phone,
        email=user.email,
        ethnicity=profile.ethnicity,
        tax_code=profile.tax_code,
        source=profile.source,
        ekyc_id=profile.ekyc_id,
        declared_at=profile.declared_at,
        consented_at=profile.consented_at,
        updated_at=profile.updated_at,
    )


def get_profile(db: Session, user: User) -> CitizenProfileOut | None:
    profile = profiles.get_by_user_id(db, user.id)
    return to_out(profile, user) if profile else None


def save_profile(db: Session, user: User, data: CitizenProfileRequest) -> CitizenProfileOut:
    """Tạo mới hoặc ghi đè hồ sơ; đổi tên tài khoản theo tên trên CCCD và lưu số điện thoại."""
    if not (data.declared and data.consented):
        raise ApiError(422, VALIDATION_ERROR,
                       "Vui lòng tick cam đoan thông tin đúng sự thật và đồng ý lưu thông tin cá nhân.")

    owner = profiles.get_by_id_number(db, data.id_number)
    if owner is not None and owner.user_id != user.id:
        raise ApiError(409, CCCD_ALREADY_USED)
    if users.phone_taken(db, data.phone, except_user_id=user.id):
        raise ApiError(409, PHONE_IN_USE)

    profile = profiles.get_by_user_id(db, user.id)
    if profile is None:
        profile = CitizenProfile(user_id=user.id)
        db.add(profile)

    profile.id_number = data.id_number
    profile.full_name = data.full_name
    profile.date_of_birth = data.date_of_birth
    profile.gender = data.gender
    profile.nationality = data.nationality
    profile.issue_date = data.issue_date
    profile.issue_place = data.issue_place
    profile.expiry_date = data.expiry_date
    profile.place_of_origin = data.place_of_origin
    profile.place_of_residence = data.place_of_residence

    profile.permanent_detail = data.permanent_address.detail
    profile.permanent_ward = data.permanent_address.ward
    profile.permanent_province = data.permanent_address.province

    profile.current_same_as_permanent = data.current_same_as_permanent
    current = None if data.current_same_as_permanent else data.current_address
    profile.current_detail = current.detail if current else None
    profile.current_ward = current.ward if current else None
    profile.current_province = current.province if current else None

    profile.ethnicity = data.ethnicity
    profile.tax_code = data.tax_code
    profile.source = data.source
    profile.ekyc_id = data.ekyc_id if data.source == "ekyc" else None

    now = datetime.now(timezone.utc)
    profile.declared_at = now
    profile.consented_at = now

    # Tên tài khoản lấy theo tên trên CCCD; số điện thoại lưu ở tài khoản.
    user.full_name = data.full_name
    user.phone = data.phone

    try:
        db.flush()
    except IntegrityError:
        # Hai người lưu cùng một số CCCD gần như cùng lúc: unique index chặn người sau.
        db.rollback()
        raise ApiError(409, CCCD_ALREADY_USED)

    db.commit()
    return to_out(profile, user)
