import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CitizenProfile


def get_by_user_id(db: Session, user_id: uuid.UUID) -> CitizenProfile | None:
    return db.get(CitizenProfile, user_id)


def get_by_id_number(db: Session, id_number: str) -> CitizenProfile | None:
    return db.scalar(select(CitizenProfile).where(CitizenProfile.id_number == id_number))


def user_ids_with_profile(db: Session, user_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Trong danh sách user_ids, những ai đã khai hồ sơ CCCD (1 câu query cho cả trang)."""
    if not user_ids:
        return set()
    return set(db.scalars(select(CitizenProfile.user_id).where(CitizenProfile.user_id.in_(user_ids))).all())
