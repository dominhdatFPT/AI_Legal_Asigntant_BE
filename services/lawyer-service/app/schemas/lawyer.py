"""Body gửi lên của Lawyer Service (khớp openapi.yaml). Kết quả trả về dựng ở app/services/views.py."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

SpecializationCode = Literal["land", "business"]
BarCardNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip() or None
    return value


def _unique(codes):
    if codes is not None and len(set(codes)) != len(codes):
        raise ValueError("Lĩnh vực bị chọn trùng.")
    return codes


def _require_any_field(model):
    if not model.model_fields_set:
        raise ValueError("Phải gửi ít nhất một trường.")
    return model


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileUpdateRequest(_Body):
    _any = model_validator(mode="after")(_require_any_field)

    bar_card_number: BarCardNumber | None = None
    law_firm: str | None = Field(default=None, max_length=255)
    years_experience: int | None = Field(default=None, ge=0, le=70)
    bio: str | None = Field(default=None, max_length=2000)
    specialization_codes: list[SpecializationCode] | None = Field(default=None, min_length=1)

    _blank = field_validator("law_firm", "bio", mode="before")(_blank_to_none)

    @field_validator("bar_card_number", "specialization_codes", mode="before")
    @classmethod
    def _no_null(cls, value):
        if value is None:
            raise ValueError("Không được để trống.")
        return value

    _uniq = field_validator("specialization_codes")(_unique)


class _ApplicationFields(_Body):
    bar_card_number: BarCardNumber | None = None
    law_firm: str | None = Field(default=None, max_length=255)
    years_experience: int | None = Field(default=None, ge=0, le=70)
    bio: str | None = Field(default=None, max_length=2000)
    specialization_codes: list[SpecializationCode] | None = None
    card_document_id: uuid.UUID | None = None

    _blank = field_validator("law_firm", "bio", mode="before")(_blank_to_none)
    _uniq = field_validator("specialization_codes")(_unique)


class ApplicationCreateRequest(_ApplicationFields):
    type: Literal["new", "card_change"]


class ApplicationUpdateRequest(_ApplicationFields):
    _any = model_validator(mode="after")(_require_any_field)


class CreateLawyerAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=255, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=255)

    @field_validator("email")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)


class HideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["role_changed", "deleted"]
