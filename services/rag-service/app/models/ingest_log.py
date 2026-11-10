import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IngestLog(Base):
    """Nhật ký nạp: ai làm gì, lúc nào, kết quả. Bảng ingest_log (database/003_ingest_log.sql)."""

    __tablename__ = "ingest_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # sub của token người gọi (chuỗi, không khóa ngoại sang user-service)
    actor_user_id: Mapped[str | None] = mapped_column(String(64))
    actor_role: Mapped[str | None] = mapped_column(String(20))
    # create_draft | delete_draft | approve_requested | approve_done | approve_failed
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    # Không có khóa ngoại: văn bản bị xóa thì nhật ký vẫn còn
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    doc_number: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(String(500))
    # Tối đa 500 ký tự, đã che khóa và chuỗi kết nối; không chứa nội dung văn bản
    detail: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
