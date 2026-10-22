import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, Uuid, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserRole(str, enum.Enum):
    citizen = "citizen"
    lawyer = "lawyer"
    admin = "admin"


class UserStatus(str, enum.Enum):
    active = "active"
    locked = "locked"


def _pg_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    return Enum(enum_cls, name=name, create_type=False, values_callable=lambda e: [m.value for m in e])


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Tài khoản Firebase (Google / email) có firebase_uid; tài khoản nội bộ (admin...) thì để trống.
    firebase_uid: Mapped[str | None] = mapped_column(String, unique=True)
    auth_provider: Mapped[str | None] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Đăng nhập bằng tên đăng nhập + mật khẩu (mật khẩu chỉ lưu dạng bcrypt hash).
    username: Mapped[str | None] = mapped_column(String, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String)
    full_name: Mapped[str | None] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String, unique=True)
    role: Mapped[UserRole] = mapped_column(_pg_enum(UserRole, "user_role"), nullable=False, default=UserRole.citizen)
    status: Mapped[UserStatus] = mapped_column(
        _pg_enum(UserStatus, "user_status"), nullable=False, default=UserStatus.active
    )
    locked_reason: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Khóa đến thời điểm này thì tự mở; NULL khi status = locked nghĩa là khóa vĩnh viễn.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Tài khoản do admin tạo (NULL = người dùng tự đăng ký).
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    # Admin tạo tài khoản này cho luật sư; vẫn là citizen cho tới khi được duyệt lên lawyer.
    created_as_lawyer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    # Xóa mềm: deleted_at khác NULL thì coi như không tồn tại (không đăng nhập, không đăng ký lại được email).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
