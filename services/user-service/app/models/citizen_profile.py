import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CitizenProfile(Base):
    """Hồ sơ cá nhân theo CCCD, 1-1 với users (database/003_citizen_profiles.sql)."""

    __tablename__ = "citizen_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    # Thông tin trên CCCD (tên cột trùng kết quả eKYC của Document Service, id → id_number)
    id_number: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)
    nationality: Mapped[str] = mapped_column(String, nullable=False, default="Việt Nam")
    place_of_origin: Mapped[str | None] = mapped_column(String)
    place_of_residence: Mapped[str | None] = mapped_column(String)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    issue_place: Mapped[str] = mapped_column(String, nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date)

    # Nơi thường trú (3 phần theo mẫu đơn mới)
    permanent_detail: Mapped[str] = mapped_column(String, nullable=False)
    permanent_ward: Mapped[str] = mapped_column(String, nullable=False)
    permanent_province: Mapped[str] = mapped_column(String, nullable=False)

    # Nơi ở hiện tại
    current_same_as_permanent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    current_detail: Mapped[str | None] = mapped_column(String)
    current_ward: Mapped[str | None] = mapped_column(String)
    current_province: Mapped[str | None] = mapped_column(String)

    # Nhập tay
    ethnicity: Mapped[str | None] = mapped_column(String)
    tax_code: Mapped[str | None] = mapped_column(String)

    # Nguồn dữ liệu và xác nhận
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="manual")
    ekyc_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    declared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
