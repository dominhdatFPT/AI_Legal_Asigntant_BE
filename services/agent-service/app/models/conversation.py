import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Lấy từ JWT `sub`; không có khóa ngoại sang user-service (mỗi service một database).
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # null cho tới khi có câu hỏi đầu tiên, khi đó service tự sinh.
    title: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Thời điểm có tin nhắn mới nhất, dùng để sắp xếp danh sách hội thoại.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(  # noqa: F821
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )
