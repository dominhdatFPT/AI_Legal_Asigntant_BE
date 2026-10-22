"""Truy vấn bảng của Lawyer Service."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.lawyer import (
    APPROVED,
    HIDDEN,
    NEW,
    OPEN_STATUSES,
    PENDING,
    REJECTED,
    LawyerApplication,
    LawyerProfile,
    Specialization,
)

def specializations(db: Session) -> list[Specialization]:
    return list(db.scalars(select(Specialization).order_by(Specialization.sort_order, Specialization.code)))


def specialization_names(db: Session) -> dict[str, str]:
    return {s.code: s.name for s in specializations(db)}


def get_profile_by_user(db: Session, user_id: uuid.UUID, for_update: bool = False) -> LawyerProfile | None:
    query = select(LawyerProfile).where(LawyerProfile.user_id == user_id)
    if for_update:
        query = query.with_for_update()
    return db.scalar(query)


def get_application(db: Session, application_id: uuid.UUID, for_update: bool = False) -> LawyerApplication | None:
    query = select(LawyerApplication).where(LawyerApplication.id == application_id)
    if for_update:
        query = query.with_for_update()
    return db.scalar(query)


def get_open_application(db: Session, user_id: uuid.UUID) -> LawyerApplication | None:
    return db.scalar(select(LawyerApplication).where(
        LawyerApplication.user_id == user_id, LawyerApplication.status.in_(OPEN_STATUSES)))


def open_applications(db: Session, user_id: uuid.UUID) -> list[LawyerApplication]:
    return list(db.scalars(select(LawyerApplication).where(
        LawyerApplication.user_id == user_id, LawyerApplication.status.in_(OPEN_STATUSES))))


def latest_application(db: Session, user_id: uuid.UUID) -> LawyerApplication | None:
    """Hồ sơ xin làm luật sư gần nhất (bỏ qua nháp và đơn đã rút)."""
    return db.scalar(select(LawyerApplication)
                     .where(LawyerApplication.user_id == user_id, LawyerApplication.type == NEW,
                            LawyerApplication.status.in_((PENDING, APPROVED, REJECTED)))
                     .order_by(LawyerApplication.created_at.desc(), LawyerApplication.id).limit(1))


def latest_rejection(db: Session, user_id: uuid.UUID) -> LawyerApplication | None:
    return db.scalar(select(LawyerApplication)
                     .where(LawyerApplication.user_id == user_id, LawyerApplication.status == REJECTED)
                     .order_by(LawyerApplication.reviewed_at.desc()).limit(1))


def bar_card_taken(db: Session, number: str, user_id: uuid.UUID) -> bool:
    """Số thẻ đã thuộc hồ sơ chưa ẩn hoặc đơn đang chờ duyệt của NGƯỜI KHÁC (không phân biệt hoa thường)."""
    key = number.upper()
    in_profiles = select(LawyerProfile.id).where(
        func.upper(LawyerProfile.bar_card_number) == key, LawyerProfile.status != HIDDEN,
        LawyerProfile.user_id != user_id)
    in_pending = select(LawyerApplication.id).where(
        func.upper(LawyerApplication.bar_card_number) == key, LawyerApplication.status == PENDING,
        LawyerApplication.user_id != user_id)
    return db.scalar(in_profiles.limit(1)) is not None or db.scalar(in_pending.limit(1)) is not None


def _page(db: Session, query, page: int, page_size: int):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    items = list(db.scalars(query.offset((page - 1) * page_size).limit(page_size)))
    return items, total


def list_admin_applications(db: Session, status: str, type_: str | None, order: str, page: int, page_size: int):
    query = select(LawyerApplication).where(LawyerApplication.status == status)
    if type_:
        query = query.where(LawyerApplication.type == type_)
    sort = LawyerApplication.submitted_at.asc() if order == "asc" else LawyerApplication.submitted_at.desc()
    return _page(db, query.order_by(sort, LawyerApplication.id), page, page_size)
