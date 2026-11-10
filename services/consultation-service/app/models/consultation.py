"""Model khớp database/001_consultation_schema.sql (SQL là bản chính; index ở đây để test SQLite cũng chặn trùng)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Trạng thái và lý do đóng (lưu dạng chữ, CHECK trong SQL)
WAITING, IN_PROGRESS, CLOSED = "waiting", "in_progress", "closed"
LOBBY, DIRECT = "lobby", "direct"
COMPLETED, CANCELLED, LOCKED, ROLE_CHANGED, DELETED, EXPIRED = (
    "completed", "cancelled", "locked", "role_changed", "deleted", "expired")
PENDING_REPORT, RESOLVED, DISMISSED = "pending", "resolved", "dismissed"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite trả datetime không múi giờ; mọi thời điểm lưu trong DB đều là UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class Consultation(Base):
    __tablename__ = "consultations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    citizen_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    lawyer_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    agent_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    question: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=WAITING)
    close_reason: Mapped[str | None] = mapped_column(String(20))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summarized_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow,
                                                 onupdate=utcnow)

    __table_args__ = (
        CheckConstraint("source in ('lobby', 'direct')", name="consultations_source_check"),
        CheckConstraint("status in ('waiting', 'in_progress', 'closed')", name="consultations_status_check"),
        CheckConstraint("citizen_user_id <> lawyer_user_id", name="consultations_not_self_check"),
        CheckConstraint(
            "(status = 'waiting' and source = 'lobby' and lawyer_user_id is null and accepted_at is null"
            " and closed_at is null and close_reason is null)"
            " or (status = 'in_progress' and lawyer_user_id is not null and accepted_at is not null"
            " and closed_at is null and close_reason is null)"
            " or (status = 'closed' and lawyer_user_id is not null and accepted_at is not null"
            " and closed_at is not null and close_reason is not null)",
            name="consultations_state_check"),
        CheckConstraint(
            "(purged_at is null and question is not null)"
            " or (purged_at is not null and question is null and ai_summary is null and status = 'closed')",
            name="consultations_purge_check"),
    )


class ConsultationMessage(Base):
    __tablename__ = "consultation_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    consultation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False)
    sender_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ConsultationSummary(Base):
    __tablename__ = "consultation_summaries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    consultation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("consultation_id", "window_end", name="consultation_summaries_window_key"),)


class ConsultationReport(Base):
    __tablename__ = "consultation_reports"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    consultation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("consultations.id"), nullable=False)
    reporter_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    reported_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PENDING_REPORT)
    handled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (CheckConstraint("reporter_user_id <> reported_user_id", name="consultation_reports_not_self_check"),)


class ConsultationTermsAcceptance(Base):
    __tablename__ = "consultation_terms_acceptances"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    terms_version: Mapped[str] = mapped_column(String(20), primary_key=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


def _index(name: str, *columns, unique: bool = False, where=None):
    return Index(name, *columns, unique=unique, postgresql_where=where, sqlite_where=where)


_index("ix_consultations_lobby", Consultation.created_at, where=Consultation.status == WAITING)
_index("ix_consultations_citizen", Consultation.citizen_user_id, Consultation.status)
_index("ix_consultations_lawyer", Consultation.lawyer_user_id, Consultation.status,
       where=Consultation.lawyer_user_id.is_not(None))
_index("ix_consultations_cleanup", Consultation.last_activity_at, where=Consultation.purged_at.is_(None))
_index("consultations_open_pair_key", Consultation.citizen_user_id, Consultation.lawyer_user_id, unique=True,
       where=Consultation.status == IN_PROGRESS)
_index("ix_consultation_messages_history", ConsultationMessage.consultation_id, ConsultationMessage.created_at,
       ConsultationMessage.id)
_index("ix_consultation_messages_unread", ConsultationMessage.consultation_id, ConsultationMessage.sender_id,
       where=ConsultationMessage.is_read.is_(False))
_index("ix_consultation_reports_admin", ConsultationReport.status, ConsultationReport.created_at)
