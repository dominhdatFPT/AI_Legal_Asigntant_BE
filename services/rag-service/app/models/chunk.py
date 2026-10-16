import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# Số chiều vector. Phải khớp vector(768) trong database/001_rag_schema.sql
# và EMBEDDING_DIM trong .env. Đổi số này = phải sửa schema + nạp lại toàn bộ.
EMBEDDING_DIM = 768


class DocumentChunk(Base):
    """Một đoạn của văn bản (thường là 1 Điều hoặc 1 Khoản) kèm embedding. Bảng document_chunks."""

    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("legal_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Thứ tự đoạn trong văn bản, bắt đầu từ 0
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Nội dung đưa cho Gemini (đã kèm dòng "Điều X. ..." ở đầu)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # {"chuong": "I", "dieu": "188", "khoan": "1", "diem": null} — để agent-service tạo trích dẫn
    section_info: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    # Test dùng SQLite (không có kiểu vector) nên lưu tạm dạng JSON
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIM).with_variant(JSON(), "sqlite"), nullable=False
    )
    # Model đã tạo embedding này. Tìm kiếm chỉ so với chunk cùng model.
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped["LegalDocument"] = relationship(back_populates="chunks")  # noqa: F821
