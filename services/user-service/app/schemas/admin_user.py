"""Request / response cho API quản lý tài khoản (chỉ admin)."""

import enum
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import UserRole
from app.schemas.user import UserOut

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
PHONE_PATTERN = r"^0\d{9}$"


def clean_phone(value):
    # FE hay gửi kiểu "0912.345.678" hoặc "0912 345 678": bỏ dấu chấm, khoảng trắng, gạch ngang.
    if isinstance(value, str):
        value = "".join(ch for ch in value if ch not in " .-") or None
    return value


class AdminUserOut(UserOut):
    """Thông tin đầy đủ hơn UserOut, chỉ admin thấy."""

    auth_provider: str | None = None
    locked_by: uuid.UUID | None = None
    created_by: uuid.UUID | None = None
    deleted_at: datetime | None = None
    deleted_by: uuid.UUID | None = None
    # Đã khai hồ sơ cá nhân (CCCD) chưa; FE dùng để hiện "Chưa cập nhật hồ sơ".
    has_citizen_profile: bool = False


class AccountType(str, enum.Enum):
    """Loại tài khoản admin tạo. Tài khoản luật sư tạo qua Lawyer Service (POST /lawyers/accounts)."""

    citizen = "citizen"
    admin = "admin"


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(min_length=3, max_length=255, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, pattern=PHONE_PATTERN, description="10 chữ số, bắt đầu bằng 0")
    account_type: AccountType = AccountType.citizen

    @field_validator("phone", mode="before")
    @classmethod
    def clean_phone(cls, value):
        return clean_phone(value)

    @field_validator("email")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return value.lower()


class InternalCreateUserRequest(BaseModel):
    """Lawyer Service tạo tài khoản luật sư (POST /internal/v1/users)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(min_length=3, max_length=255, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, pattern=PHONE_PATTERN)
    role: Literal["lawyer"]
    created_by: uuid.UUID

    @field_validator("phone", mode="before")
    @classmethod
    def clean_phone(cls, value):
        return clean_phone(value)

    @field_validator("email")
    @classmethod
    def lower_email(cls, value: str) -> str:
        return value.lower()


class InternalChangeRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["lawyer"]


class ChangeRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: UserRole


class LockUserRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
    # Số ngày khóa; bỏ trống / null = khóa vĩnh viễn (đến khi admin mở).
    duration_days: int | None = Field(default=None, ge=1, le=3650)
