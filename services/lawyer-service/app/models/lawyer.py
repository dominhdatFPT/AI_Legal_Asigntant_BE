"""Model khớp database/001_lawyer_schema.sql (SQL là bản chính; index ở đây để test SQLite cũng chặn trùng)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, SmallInteger, String, Text, Uuid, and_, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")

# Trạng thái hồ sơ / đơn (lưu dạng chữ, CHECK trong SQL)
INCOMPLETE, ACTIVE, HIDDEN = "incomplete", "active", "hidden"
ADMIN_CREATED, SELF_APPLIED = "admin_created", "self_applied"
NEW, CARD_CHANGE = "new", "card_change"
DRAFT, PENDING, APPROVED, REJECTED, WITHDRAWN = "draft", "pending", "approved", "rejected", "withdrawn"
OPEN_STATUSES = (DRAFT, PENDING)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite trả datetime không múi giờ; mọi thời điểm lưu trong DB đều là UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class Specialization(Base):
    __tablename__ = "specializations"

    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class LawyerProfileSpecialization(Base):
    __tablename__ = "lawyer_profile_specializations"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("lawyer_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    specialization_code: Mapped[str] = mapped_column(
        String(30), ForeignKey("specializations.code"), primary_key=True, index=True
    )
    specialization: Mapped[Specialization] = relationship(lazy="joined")


class LawyerProfile(Base):
    __tablename__ = "lawyer_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=INCOMPLETE)
    bar_card_number: Mapped[str | None] = mapped_column(String(50))
    card_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    card_ocr_result: Mapped[dict | None] = mapped_column(JsonType)
    law_firm: Mapped[str | None] = mapped_column(String(255))
    years_experience: Mapped[int | None] = mapped_column(SmallInteger)
    bio: Mapped[str | None] = mapped_column(Text)
    is_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_reason: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow,
                                                 onupdate=utcnow)

    specializations: Mapped[list[LawyerProfileSpecialization]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def specialization_codes(self) -> list[str]:
        return [s.specialization_code for s in self.specializations]


class LawyerApplication(Base):
    __tablename__ = "lawyer_applications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DRAFT)
    bar_card_number: Mapped[str | None] = mapped_column(String(50))
    law_firm: Mapped[str | None] = mapped_column(String(255))
    years_experience: Mapped[int | None] = mapped_column(SmallInteger)
    bio: Mapped[str | None] = mapped_column(Text)
    specialization_codes: Mapped[list | None] = mapped_column(JsonType)
    card_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    ocr_result: Mapped[dict | None] = mapped_column(JsonType)
    applicant_full_name: Mapped[str | None] = mapped_column(String(255))
    reject_reason: Mapped[str | None] = mapped_column(String(500))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow,
                                                 onupdate=utcnow)


def _partial_unique(name: str, *columns, where):
    return Index(name, *columns, unique=True, postgresql_where=where, sqlite_where=where)


_partial_unique(
    "lawyer_profiles_bar_card_key",
    func.upper(LawyerProfile.bar_card_number),
    where=and_(LawyerProfile.bar_card_number.is_not(None), LawyerProfile.status != HIDDEN),
)
_partial_unique(
    "lawyer_applications_one_open_key",
    LawyerApplication.user_id,
    where=LawyerApplication.status.in_(OPEN_STATUSES),
)
_partial_unique(
    "lawyer_applications_pending_bar_card_key",
    func.upper(LawyerApplication.bar_card_number),
    where=and_(LawyerApplication.status == PENDING, LawyerApplication.bar_card_number.is_not(None)),
)
Index("ix_lawyer_applications_user", LawyerApplication.user_id, LawyerApplication.created_at)
Index("ix_lawyer_applications_admin", LawyerApplication.status, LawyerApplication.type, LawyerApplication.submitted_at)
