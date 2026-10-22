"""Body gửi lên của Lawyer Service (khớp openapi.yaml). Kết quả trả về dựng ở app/services/views.py."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

SpecializationCode = Literal["land", "business"]
BarCardNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
# Loại giấy tờ theo các ô tải lên ở trang lawyer-registration (FE).
CredentialType = Literal["the_luat_su", "chung_chi_hanh_nghe", "bang_cu_nhan", "khac"]
REQUIRED_CREDENTIALS = ("the_luat_su", "chung_chi_hanh_nghe")
CARD_CREDENTIAL = "the_luat_su"


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip() or None
    return value


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credential(_Body):
    document_id: uuid.UUID
    type: CredentialType


class LawyerSubmitRequest(_Body):
    """POST /lawyers (gửi lần đầu) và PUT /lawyers/me (gửi lại sau khi bị từ chối)."""

    bar_card_number: BarCardNumber
    law_firm: str | None = Field(default=None, max_length=255)
    years_experience: int | None = Field(default=None, ge=0, le=70)
    bio: str | None = Field(default=None, max_length=2000)
    specialization_codes: list[SpecializationCode] = Field(min_length=1)
    credentials: list[Credential] = Field(min_length=1, max_length=10)

    _blank = field_validator("law_firm", "bio", mode="before")(_blank_to_none)

    @field_validator("specialization_codes")
    @classmethod
    def _unique_codes(cls, codes):
        if len(set(codes)) != len(codes):
            raise ValueError("Lĩnh vực bị chọn trùng.")
        return codes

    @field_validator("credentials")
    @classmethod
    def _required_credentials(cls, value: list[Credential]) -> list[Credential]:
        if len({c.document_id for c in value}) != len(value):
            raise ValueError("Giấy tờ bị gửi trùng.")
        if not set(REQUIRED_CREDENTIALS) <= {c.type for c in value}:
            raise ValueError("Cần tải lên thẻ luật sư và chứng chỉ hành nghề.")
        return value


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=500)


class HideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["role_changed", "deleted"]
