import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DocumentVersion(Base):
    """Bản nội dung CŨ của một văn bản, lưu mỗi khi admin thay nội dung. Bảng document_versions (database/004_document_versions.sql)."""

    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Không có khóa ngoại: văn bản bị xóa thì các bản cũ vẫn còn
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # sub của token admin bấm thay (chuỗi, không khóa ngoại sang user-service)
    replaced_by_user_id: Mapped[str | None] = mapped_column(String(64))
    # Toàn văn CŨ bị thay
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Số chunk của bản cũ
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    content_length: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
