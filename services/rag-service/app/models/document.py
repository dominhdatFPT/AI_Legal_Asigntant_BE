import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class LegalDocument(Base):
    """Một văn bản pháp luật (Luật, Nghị định...). Bảng legal_documents."""

    __tablename__ = "legal_documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # luat | nghi_dinh | thong_tu | quyet_dinh | khac
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Số hiệu, VD 31/2024/QH15. Không trùng (NULL thì được phép nhiều dòng).
    doc_number: Mapped[str | None] = mapped_column(String(100), unique=True)
    issuing_body: Mapped[str | None] = mapped_column(String(255))
    issued_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    # active | superseded | draft — chỉ 'active' được đưa vào kết quả tìm kiếm
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    source_url: Mapped[str | None] = mapped_column(Text)
    # Toàn văn gốc, giữ lại để cắt + embedding lại khi đổi model
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # true khi mọi chunk đã có embedding
    is_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(  # noqa: F821
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentChunk.chunk_index",
    )
